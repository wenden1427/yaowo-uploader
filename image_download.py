"""Shared image downloads using the application's configured network route."""

import hashlib
import io
import math
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from PIL import Image, features


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def image_accept_header():
    Image.init()
    supported = features.get_supported()
    formats = []
    if "AVIF" in Image.OPEN and "avif" in supported:
        formats.append("image/avif")
    if "WEBP" in Image.OPEN and "webp" in supported:
        formats.append("image/webp")
    return ",".join(formats + ["image/png", "image/jpeg", "image/gif;q=0.9"])


def retry_after_seconds(headers, wall_time):
    value = str((headers or {}).get("Retry-After", "")).strip()
    if value.isascii() and value.isdigit():
        # Saturate extreme values instead of overflowing float/time arithmetic.
        return float(min(int(value[:10]), 31536000))
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is not None:
            return max(0.0, min(date.timestamp() - wall_time, 31536000))
    except (ValueError, TypeError, OverflowError):
        pass
    return None


class ImageDownloadError(RuntimeError):
    def __init__(self, message, original=None, retry_after=None, invalid_image=False):
        super().__init__(message)
        self.original = original if original is not None else self
        self.retry_after = retry_after
        self.invalid_image = invalid_image


@dataclass
class _Flight:
    event: threading.Event = field(default_factory=threading.Event)
    data: bytes = None
    error: BaseException = None
    cacheable: bool = True


@dataclass
class _Cooldown:
    until: float
    recover_until: float
    next_start: float
    failures: int
    error: Exception


class ImageDownloader:
    def __init__(self, opener_factory, logger=None, *, cache_bytes=64 * 1024 * 1024,
                 cache_items=256, cache_ttl=300, timeout=30, budget=90,
                 clock=time.monotonic, sleep=time.sleep, wall_clock=time.time,
                 jitter=lambda: random.uniform(0.25, 1.0)):
        self._opener_factory = opener_factory
        self._logger = logger or (lambda message: None)
        self._cache_bytes, self._cache_items = cache_bytes, cache_items
        self._cache_ttl = cache_ttl
        self._timeout, self._budget = timeout, budget
        self._clock, self._sleep = clock, sleep
        self._wall_clock, self._jitter = wall_clock, jitter
        self._headers = {"User-Agent": USER_AGENT, "Accept": image_accept_header()}
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._cached_size = 0
        self._flights = {}
        self._cooldowns = OrderedDict()

    def _log(self, url, message):
        # Signed query strings, credentials and business paths never enter logs.
        host = urllib.parse.urlsplit(url).hostname or "unknown"
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        try:
            self._logger(f"IMAGE_DOWNLOAD host={host} image={key} {message}")
        except Exception:
            pass

    def _evict(self, url):
        cached = self._cache.pop(url, None)
        if cached is not None:
            self._cached_size -= len(cached[1])

    def invalidate(self, url):
        url = str(url or "").strip()
        with self._lock:
            self._evict(url)
            if url in self._flights:
                self._flights[url].cacheable = False
            # Refresh must not bypass a server's Retry-After restriction.

    def download(self, url):
        url = str(url or "").strip()
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("图片链接必须为有效的 HTTP/HTTPS 地址。")
        with self._lock:
            cached = self._cache.get(url)
            if cached and self._clock() - cached[0] < self._cache_ttl:
                self._cache.move_to_end(url)
                data = cached[1]
            else:
                self._evict(url)
                data = None
            flight = self._flights.get(url)
            leader = flight is None
            if data is None and leader:
                flight = self._flights[url] = _Flight()
        if data is not None:
            self._log(url, "cache=hit")
            return data
        if not leader:
            self._log(url, "request=shared")
            if not flight.event.wait(self._budget):
                raise TimeoutError("等待同一张图片下载超时，请稍后刷新。")
            if flight.error is not None:
                raise flight.error
            return flight.data
        try:
            flight.data = self._fetch(url)
            return flight.data
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                if (flight.error is None and flight.cacheable and
                        flight.data is not None and len(flight.data) <= self._cache_bytes):
                    now = self._clock()
                    for key, (timestamp, _) in list(self._cache.items()):
                        if now - timestamp >= self._cache_ttl:
                            self._evict(key)
                    self._cache[url] = (now, flight.data)
                    self._cached_size += len(flight.data)
                    while (len(self._cache) > self._cache_items or
                           self._cached_size > self._cache_bytes):
                        self._evict(next(iter(self._cache)))
                self._flights.pop(url, None)
                flight.event.set()

    def _rate_error(self, error, wait):
        wait = max(1, math.ceil(wait))
        return ImageDownloadError(
            f"图片服务器返回 HTTP 429，暂时拒绝请求；已暂停连续请求，"
            f"请至少 {wait} 秒后刷新。", original=error, retry_after=wait)

    def _wait_for_host(self, url, deadline):
        host = urllib.parse.urlsplit(url).hostname
        while True:
            with self._lock:
                now = self._clock()
                state = self._cooldowns.get(host)
                if state is None:
                    return
                if now >= state.recover_until:
                    self._cooldowns.pop(host, None)
                    return
                wait = max(state.until, state.next_start) - now
                if wait <= 0:
                    # Pace recovery only after this image host actually sent 429.
                    state.next_start = now + 0.5
                    return
                error = state.error
            if wait >= deadline - now:
                raise self._rate_error(error, wait)
            self._log(url, f"cooldown_wait={wait:.2f}s")
            self._sleep(wait)

    def _record_429(self, url, error):
        host = urllib.parse.urlsplit(url).hostname
        requested_wait = retry_after_seconds(error.headers, self._wall_clock())
        with self._lock:
            now = self._clock()
            previous = self._cooldowns.get(host)
            failures = (previous.failures if previous and now < previous.recover_until else 0) + 1
            delay = max(requested_wait or 0, min(4 * 2 ** min(failures - 1, 3), 30))
            delay += self._jitter()
            until = max(now + delay, previous.until if previous else 0)
            self._cooldowns[host] = _Cooldown(until, until + 60, until, failures, error)
            self._cooldowns.move_to_end(host)
            while len(self._cooldowns) > 128:
                self._cooldowns.popitem(last=False)
        self._log(url, f"status=429 retry_after={until - now:.2f}s")
        return until - now

    def _fetch(self, url):
        deadline = self._clock() + self._budget
        last_error = None
        # Re-resolve the existing proxy configuration on each attempt. Do not
        # silently bypass an enabled proxy; no-proxy/TUN is resolved centrally.
        for attempt in range(1, 4):
            self._wait_for_host(url, deadline)
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError("图片下载及等待重试超时。") from last_error
            self._log(url, f"attempt={attempt} route=configured")
            try:
                request = urllib.request.Request(url, headers=self._headers)
                with self._opener_factory().open(
                        request, timeout=min(self._timeout, remaining)) as response:
                    if response.status != 200:
                        raise urllib.error.HTTPError(url, response.status,
                                                     "Image request failed", response.headers, None)
                    data = self._read_image(response, deadline)
                self._log(url, f"result=ok bytes={len(data)}")
                return data
            except Exception as exc:
                last_error = exc
                original = getattr(exc, "original", exc)
                if isinstance(original, urllib.error.HTTPError):
                    original.close()
                    if original.code == 429:
                        wait = self._record_429(url, original)
                        if attempt == 3:
                            raise self._rate_error(original, wait) from exc
                        continue
                    if original.code in (400, 401, 403, 404):
                        raise
                if attempt >= 2:
                    raise
                delay = 1 + self._jitter()
                if self._clock() + delay >= deadline:
                    raise
                self._sleep(delay)
        raise last_error

    def _read_image(self, response, deadline):
        data = bytearray()
        while True:
            if self._clock() >= deadline:
                raise TimeoutError("图片内容下载超时。")
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ImageDownloadError("单张原始图片超过 32MB，停止下载以保护内存。")
        data = bytes(data)
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.load()
        except Exception as exc:
            raise ImageDownloadError(
                "图片响应无法解码，可能是错误页、损坏图片或本机不支持的格式。",
                original=exc, invalid_image=True) from exc
        return data
