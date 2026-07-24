"""Network routing, proxy preflight, and redacted request diagnostics."""

from dataclasses import dataclass, field
import csv
import io
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import threading
import time
from urllib import error, parse, request


APP_DIR = os.path.dirname(os.path.abspath(__file__))
NETWORK_LOG = os.path.join(APP_DIR, "network.log")
_PROXY_ENV_NAMES = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)
_SOURCE_LABELS = {
    "config": "上传器显式代理",
    "windows": "Windows 系统代理",
    "environment": "环境变量代理",
    "direct": "无代理/TUN",
}


@dataclass
class ProxyDecision:
    url: str | None
    source: str
    warnings: list[str] = field(default_factory=list)

    @property
    def source_label(self):
        return _SOURCE_LABELS.get(self.source, self.source)

    @property
    def safe_url(self):
        return redact_proxy_url(self.url) if self.url else "无"


@dataclass
class NetworkPreflight:
    decision: ProxyDecision
    warnings: list[str]
    log_path: str = NETWORK_LOG


def normalize_proxy_url(value):
    """Return a normalized HTTP proxy URL, or None for an invalid value."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    try:
        parts = parse.urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or not parts.port:
            return None
    except (TypeError, ValueError):
        return None
    auth = ""
    if parts.username:
        auth = parse.quote(parts.username, safe="")
        if parts.password is not None:
            auth += ":" + parse.quote(parts.password, safe="")
        auth += "@"
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parts.scheme}://{auth}{host}:{parts.port}"


def redact_proxy_url(value):
    normalized = normalize_proxy_url(value)
    if not normalized:
        return "无效代理"
    parts = parse.urlsplit(normalized)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parts.scheme}://{host}:{parts.port}"


def _parse_windows_proxy_server(value):
    value = str(value or "").strip()
    if not value:
        return None
    entries = {}
    first = None
    for item in value.split(";"):
        item = item.strip()
        if not item:
            continue
        if first is None:
            first = item
        if "=" in item:
            scheme, server = item.split("=", 1)
            entries[scheme.strip().lower()] = server.strip()
    return entries.get("https") or entries.get("http") or first


def read_windows_proxy():
    """Read the enabled WinINET proxy used by normal Windows applications."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            return normalize_proxy_url(_parse_windows_proxy_server(server))
        finally:
            winreg.CloseKey(key)
    except Exception:
        return None


def _is_loopback(host):
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_proxy_listener_available(proxy_url, timeout=0.35):
    """Check local proxy ports; remote proxies are left to the real request."""
    normalized = normalize_proxy_url(proxy_url)
    if not normalized:
        return False
    parts = parse.urlsplit(normalized)
    if not _is_loopback(parts.hostname):
        return True
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout=timeout):
            return True
    except OSError:
        return False


def _environment_proxy_values(environ):
    grouped = {}
    for name in _PROXY_ENV_NAMES:
        raw = environ.get(name)
        if raw:
            grouped.setdefault(raw, []).append(name)
    return [("/".join(names), raw) for raw, names in grouped.items()]


def resolve_proxy(config=None, environ=None, windows_proxy=None,
                  listener_checker=None):
    """Choose a usable proxy without trusting stale loopback environment ports."""
    if config is None:
        try:
            from config_manager import load_config
            config = load_config()
        except Exception:
            config = {}
    environ = os.environ if environ is None else environ
    if windows_proxy is None:
        windows_proxy = read_windows_proxy()
    listener_checker = listener_checker or is_proxy_listener_available

    raw_candidates = []
    if config.get("proxy"):
        raw_candidates.append(("config", "配置项 proxy", config.get("proxy")))
    if windows_proxy:
        raw_candidates.append(("windows", "Windows 系统代理", windows_proxy))
    raw_candidates.extend(
        ("environment", name, value)
        for name, value in _environment_proxy_values(environ)
    )

    warnings = []
    usable = []
    seen = set()
    for source, name, raw in raw_candidates:
        normalized = normalize_proxy_url(raw)
        if not normalized:
            warnings.append(f"已忽略无效的{name}：{_sanitize_text(str(raw))}")
            continue
        safe = redact_proxy_url(normalized)
        if normalized in seen:
            continue
        seen.add(normalized)
        if not listener_checker(normalized):
            warnings.append(f"已忽略失效的{name} {safe}：本地端口未监听。")
            continue
        usable.append((source, name, normalized))

    if usable:
        selected_source, selected_name, selected_url = usable[0]
        conflicting = [
            f"{name}={redact_proxy_url(url)}"
            for source, name, url in usable[1:]
            if url != selected_url
        ]
        if conflicting:
            warnings.append(
                f"检测到代理配置冲突；本次使用{selected_name} "
                f"{redact_proxy_url(selected_url)}，未使用：{', '.join(conflicting)}。"
            )
        return ProxyDecision(selected_url, selected_source, warnings)

    return ProxyDecision(None, "direct", warnings)


def _set_process_proxy_environment(decision, environ):
    if decision.url:
        for name in _PROXY_ENV_NAMES:
            environ[name] = decision.url
    else:
        for name in _PROXY_ENV_NAMES:
            environ.pop(name, None)


def _running_proxy_families(process_names=None):
    if process_names is None:
        try:
            completed = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process_names = []
            for row in csv.reader(io.StringIO(completed.stdout)):
                if row:
                    process_names.append(row[0])
        except Exception:
            return []

    families = {}
    patterns = (
        ("v2rayN/xray", ("v2rayn", "xray")),
        (
            "Bitz Net 后台服务 (com.vortex.helper)",
            ("vortex", "com.vortex.helper", "bitz"),
        ),
        ("Clash/mihomo", ("clash", "mihomo")),
        ("sing-box", ("sing-box", "singbox")),
    )
    for process_name in process_names:
        lowered = str(process_name).lower()
        for display, needles in patterns:
            if any(needle in lowered for needle in needles):
                families[display] = True
    return list(families)


def _configured_hosts(config):
    urls = [
        config.get("deepseek_url"),
        config.get("routeapi_url"),
        config.get("hfsyapi_url"),
        "https://raw.githubusercontent.com",
        "https://api.github.com",
        "https://github.com",
    ]
    storage = config.get("storage") or {}
    urls.append(storage.get("base_url"))
    hosts = []
    for value in urls:
        try:
            host = parse.urlsplit(str(value or "")).hostname
        except ValueError:
            host = None
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def _dns_warnings(config, resolver=None):
    resolver = resolver or socket.getaddrinfo
    warnings = []
    hosts = _configured_hosts(config)
    results = {
        host: {"addresses": [], "error": None, "finished": False}
        for host in hosts
    }

    def worker(host):
        try:
            infos = resolver(host, 443, type=socket.SOCK_STREAM)
            results[host]["addresses"] = sorted({item[4][0] for item in infos})
        except Exception as exc:
            results[host]["error"] = exc
        finally:
            results[host]["finished"] = True

    threads = []
    for host in hosts:
        thread = threading.Thread(target=worker, args=(host,), daemon=True)
        thread.start()
        threads.append(thread)
    deadline = time.monotonic() + 1.5
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))

    for host in hosts:
        result = results[host]
        if not result["finished"]:
            addresses, failure = [], "DNS 查询超时"
        elif result["error"]:
            addresses, failure = [], _sanitize_text(str(result["error"]))
        else:
            addresses, failure = result["addresses"], None
        if failure:
            _write_log(f"DNS host={host} result=failed detail={failure}")
            warnings.append(f"本机 DNS 检查失败：{host}（{failure}）。")
            continue
        _write_log(f"DNS host={host} result=ok addresses={','.join(addresses[:6])}")
        suspicious = []
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
                if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified:
                    suspicious.append(address)
            except ValueError:
                continue
        if suspicious:
            warnings.append(
                f"DNS 结果异常：{host} 解析到 {', '.join(suspicious[:3])}。"
            )
    return warnings


def initialize_network_environment(config=None, environ=None,
                                   windows_proxy=None, listener_checker=None,
                                   process_names=None, dns_resolver=None,
                                   check_dns=True):
    """Run startup checks and align only this uploader process's environment."""
    if config is None:
        try:
            from config_manager import load_config
            config = load_config()
        except Exception:
            config = {}
    environ = os.environ if environ is None else environ
    decision = resolve_proxy(
        config=config,
        environ=environ,
        windows_proxy=windows_proxy,
        listener_checker=listener_checker,
    )
    _set_process_proxy_environment(decision, environ)
    warnings = list(decision.warnings)

    families = _running_proxy_families(process_names)
    if len(families) > 1:
        warnings.append(
            "检测到多套代理程序同时运行："
            + "、".join(families)
            + "。它们可能相互覆盖系统代理、DNS 或路由。"
        )
    if check_dns:
        warnings.extend(_dns_warnings(config, dns_resolver))

    _write_log(
        "PREFLIGHT "
        f"mode={decision.source_label} proxy={decision.safe_url} "
        f"proxy_families={','.join(families) or 'none'} "
        f"warnings={len(warnings)}"
    )
    for warning in warnings:
        _write_log(f"WARNING {_sanitize_text(warning)}")
    return NetworkPreflight(decision, warnings)


def _sanitize_text(value):
    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer ***", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-***", text)
    text = re.sub(
        r"(?i)(token|api[_-]?key|secret|authorization)(\s*[=:]\s*)[^&\s,;]+",
        r"\1\2***",
        text,
    )

    def hide_url(match):
        try:
            parts = parse.urlsplit(match.group(0))
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            suffix = "/..." if parts.path not in {"", "/"} or parts.query else ""
            return f"{parts.scheme}://{host}{port}{suffix}"
        except Exception:
            return "<url>"

    text = re.sub(r"https?://[^\s\"'<>，。；：（）]+", hide_url, text)
    return " ".join(text.split())[:1200]


def _write_log(message):
    try:
        if os.path.isfile(NETWORK_LOG) and os.path.getsize(NETWORK_LOG) > 2 * 1024 * 1024:
            backup = NETWORK_LOG + ".1"
            if os.path.exists(backup):
                os.remove(backup)
            os.replace(NETWORK_LOG, backup)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(NETWORK_LOG, "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {_sanitize_text(message)}\n")
    except Exception:
        pass


def classify_network_error(exc, proxy_used=False):
    if isinstance(exc, error.HTTPError):
        return "http"
    current = exc
    if isinstance(exc, error.URLError):
        current = exc.reason
    if isinstance(current, ssl.SSLError) or "SSL" in str(current).upper():
        return "tls"
    if isinstance(current, socket.gaierror):
        return "dns"
    if isinstance(current, (TimeoutError, socket.timeout)):
        return "timeout"
    if "TUNNEL CONNECTION FAILED" in str(current).upper():
        return "proxy"
    if getattr(current, "winerror", None) in {10060, 10061}:
        return "proxy" if proxy_used else "connect"
    return "connect"


def explain_network_error(exc, host, route, decision):
    phase = classify_network_error(exc, proxy_used=bool(decision.url and route != "direct"))
    labels = {
        "dns": "DNS 解析",
        "proxy": "代理连接",
        "connect": "目标连接",
        "timeout": "连接超时",
        "tls": "TLS/SSL 握手",
        "http": "HTTP 响应",
    }
    via = (
        f"{decision.source_label} {decision.safe_url}"
        if route != "direct" and decision.url
        else "直连"
    )
    return (
        f"{labels.get(phase, '网络')}失败：{host}，路径={via}；"
        f"底层错误：{_sanitize_text(exc)}。详情见 network.log"
    )


class NetworkRequestError(RuntimeError):
    """Operator-facing network error with the original exception preserved."""

    def __init__(self, message, original):
        super().__init__(message)
        self.original = original


class LoggedOpener:
    def __init__(self, opener, route, decision):
        self._opener = opener
        self.route = route
        self.decision = decision

    def open(self, req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        host = parse.urlsplit(url).hostname or "unknown"
        via = (
            f"{self.decision.source_label}:{self.decision.safe_url}"
            if self.route != "direct" and self.decision.url
            else "直连"
        )
        _write_log(f"REQUEST host={host} route={self.route} via={via}")
        try:
            response = self._opener.open(req, *args, **kwargs)
            status = getattr(response, "status", None) or getattr(response, "code", None)
            _write_log(f"RESPONSE host={host} status={status or 'unknown'} via={via}")
            return response
        except error.HTTPError as exc:
            _write_log(f"FAIL host={host} phase=http status={exc.code} via={via}")
            raise
        except Exception as exc:
            phase = classify_network_error(
                exc,
                proxy_used=bool(self.decision.url and self.route != "direct"),
            )
            _write_log(
                f"FAIL host={host} phase={phase} via={via} "
                f"detail={_sanitize_text(exc)}"
            )
            raise NetworkRequestError(
                explain_network_error(exc, host, self.route, self.decision),
                exc,
            ) from exc


def build_network_opener(route="auto", config=None):
    """Build an opener whose proxy behavior cannot drift with stale env vars."""
    if route not in {"direct", "auto", "proxy"}:
        raise ValueError(f"Unknown network route: {route}")
    decision = (
        ProxyDecision(None, "direct")
        if route == "direct"
        else resolve_proxy(config=config)
    )
    if not decision.url:
        opener = request.build_opener(request.ProxyHandler({}))
    else:
        opener = request.build_opener(request.ProxyHandler({
            "http": decision.url,
            "https": decision.url,
        }))
    return LoggedOpener(opener, route, decision)
