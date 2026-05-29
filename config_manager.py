# Author: Administrator
# Created: 2026-05-25
"""Configuration manager for 耀我科技上传器 v2.0.

Manages all configuration I/O:
  - config.yaml (API keys, storage, pricing, proxy)
  - prompts.yaml (AI prompt templates with built-in defaults)
  - Category code table (.xls parsing via xlrd)
  - Banned words list (.txt)
  - Proxy auto-detection (registry, env, config fallback)
"""

import os
import yaml

# ============================================================
# Path resolution
# ============================================================

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_file(filename):
    """Search for a config file in SKILL_DIR first, then the parent directory.

    Returns the absolute path if found, or None.
    """
    for directory in (SKILL_DIR, os.path.dirname(SKILL_DIR)):
        path = os.path.join(directory, filename)
        if os.path.isfile(path):
            return path
    return None


# ============================================================
# 1. config.yaml
# ============================================================

def load_config():
    """Read config.yaml and return a dict.

    Search order: SKILL_DIR -> parent directory.
    Returns an empty dict if the file does not exist or is unparseable.
    """
    path = _find_file("config.yaml")
    if path is None:
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    """Write *cfg* to config.yaml under SKILL_DIR."""
    path = os.path.join(SKILL_DIR, "config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)


# ============================================================
# 2. prompts.yaml
# ============================================================

# Built-in defaults drawn from design.md Chapter 5.
DEFAULT_PROMPTS = {
    "title": (
        "你是韩国电商标题优化专家。生成Gmarket商品标题：\n"
        "- CRITICAL: 纯韩文标题必须45字符以内，超过45字符无效。严格控制在45字符以内。\n"
        "- 只输出纯韩文字符，不要任何标点符号、数字、英文、特殊字符\n"
        "- 如含英文/数字：100字符以内（商品名+促销文案），英文数字可保留\n"
        "- 极致本土化用语，韩国消费者使用的自然表达\n"
        "- 包含高流量关键词\n"
        "- 禁止：年/月/日、品牌名、违禁词\n"
        "- {color_note}\n"
        "- 只输出标题文本本身，不要任何解释。输出后请自行检查字符数。\n"
        "\n"
        "产品：{product_title}\n"
        "违禁词列表：{banned_words}"
    ),
    "generic": (
        "Professional e-commerce product photo, natural everyday setting, subtle elevated angle. "
        "Remove ALL watermarks, store logos, brand tags, and overlay text from the photo background. "
        "But KEEP any logos or text that are naturally part of the product's design itself. "
        "Product shape, color, and material must stay 100% identical. Natural warm tones, not over-styled."
    ),
}


def load_prompts():
    """Read prompts.yaml and return a dict.

    Search order: SKILL_DIR -> parent directory.
    If the file is missing or unparseable, return the built-in DEFAULT_PROMPTS.
    User-supplied keys override defaults; missing keys are filled from defaults.
    """
    path = _find_file("prompts.yaml")
    if path is None:
        return dict(DEFAULT_PROMPTS)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return dict(DEFAULT_PROMPTS)

    if not isinstance(data, dict):
        return dict(DEFAULT_PROMPTS)

    # Merge: user values override defaults, defaults fill missing keys
    result = dict(DEFAULT_PROMPTS)
    result.update(data)
    return result


def save_prompts(prompts):
    """Write *prompts* dict to prompts.yaml under SKILL_DIR."""
    path = os.path.join(SKILL_DIR, "prompts.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(prompts, f, allow_unicode=True, default_flow_style=False)


# ============================================================
# 3. Category code table
# ============================================================

def _cell_str(sh, row, col):
    """Read an xlrd cell as a clean string.

    Numeric cells that hold whole numbers are returned without a trailing ".0".
    """
    import xlrd

    ctype = sh.cell_type(row, col)
    value = sh.cell_value(row, col)

    if ctype == xlrd.XL_CELL_EMPTY:
        return ""
    if ctype == xlrd.XL_CELL_NUMBER:
        if value == int(value):
            return str(int(value))
        return str(value)
    return str(value).strip()


def load_categories(path):
    """Parse 카테고리목록(类别代码K列).xls and return a category lookup dict.

    Returns
    -------
    dict
        ``{category_path: {"esm_code": str, "auction": str, "gmarket": str}}``

    Column layout (0-indexed, verified from original file):
        col 0 — ESM名称 (category full path, e.g. "패션의류>여성의류>원피스")
        col 2 — site indicator ("A옥션" or "G마켓")
        col 3 — ESM代码
        col 4 — site code (A옥션 or G마켓 code)
    """
    import xlrd

    wb = xlrd.open_workbook(path)
    sh = wb.sheet_by_index(0)

    categories = {}

    for r in range(1, sh.nrows):
        esm_name = _cell_str(sh, r, 0)   # ESM名称
        site     = _cell_str(sh, r, 2)   # site indicator
        esm_code = _cell_str(sh, r, 3)   # ESM代码
        site_code = _cell_str(sh, r, 4)  # A옥션 / G마켓 code

        if not esm_name:
            continue

        if esm_name not in categories:
            categories[esm_name] = {"esm_code": "", "auction": "", "gmarket": ""}

        if esm_code and not categories[esm_name]["esm_code"]:
            categories[esm_name]["esm_code"] = esm_code

        if site == "A옥션" and site_code:
            categories[esm_name]["auction"] = site_code
        elif site == "G마켓" and site_code:
            categories[esm_name]["gmarket"] = site_code

    return categories


# ============================================================
# 4. Banned words
# ============================================================

def load_banned_words(path):
    """Read a banned-words text file (one word per line).

    Returns
    -------
    list[str]
        Stripped, non-empty lines.
    """
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


# ============================================================
# 5. Proxy auto-detection
# ============================================================

def detect_proxy():
    """Auto-detect the HTTP proxy address.

    Priority (first match wins):
        1. Windows system proxy (registry
           ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings``,
           only when ProxyEnable == 1).
        2. Environment variables ``HTTP_PROXY`` / ``HTTPS_PROXY`` / ``http_proxy``.
        3. ``proxy`` field in config.yaml.
        4. None — direct connection.

    Returns
    -------
    str or None
        Proxy URL in the form ``"http://127.0.0.1:7897"``, or ``None``.
    """
    # ---- 1. Windows registry ----
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            proxy_enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if proxy_enable:
                proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
                proxy_server = str(proxy_server).strip()
                if proxy_server:
                    # Registry value may be "http=127.0.0.1:7897;https=..."
                    # or just "127.0.0.1:7897".  Take the first entry.
                    server = proxy_server.split(";")[0].strip()
                    if "=" in server:
                        server = server.split("=", 1)[1].strip()
                    if server and "://" not in server:
                        server = "http://" + server
                    if server:
                        return server
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass

    # ---- 2. Environment variables ----
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy"):
        val = os.environ.get(var)
        if val:
            return val

    # ---- 3. config.yaml ----
    cfg = load_config()
    proxy = cfg.get("proxy")
    if proxy:
        return str(proxy)

    # ---- 4. No proxy ----
    return None


# ============================================================
# 6. Chinese category cache
# ============================================================

def load_category_zh():
    """Load Korean→Chinese category translation cache."""
    import json
    path = _find_file("categories_zh.json")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_category_zh(cache):
    """Save Korean→Chinese category translation cache."""
    import json
    path = os.path.join(SKILL_DIR, "categories_zh.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return None
