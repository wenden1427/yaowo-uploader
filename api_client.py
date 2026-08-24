# Author: Administrator
# Created: 2026-05-25
"""Unified external API client for 耀我科技上传器 v2.0.

Manages all outbound HTTP calls:
  - DeepSeek (chat + translate)
  - routeapi (image generation)
  - Image download (via proxy)
  - Storage upload (Cloudinary, extensible via StorageProvider ABC)

Proxy rules (per task-03 spec):
  - DeepSeek ............ DIRECT (no proxy)
  - routeapi ............ USE proxy
  - download_image ...... USE proxy
  - storage upload ...... USE proxy
"""

import json
import time
import hashlib
import hmac
import base64
import io
import re
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

from image_utils import ensure_marketplace_image_spec
from secure_credentials import get_credential

DEFAULT_TENCENT_COS_BUCKET = "yaowoo-1443995558"
DEFAULT_TENCENT_COS_REGION = "ap-hongkong"
DEFAULT_TENCENT_COS_PREFIX = "gmarket/uploads"
DEFAULT_TENCENT_COS_BASE_URL = (
    "https://yaowoo-1443995558.cos.ap-hongkong.myqcloud.com"
)

# ============================================================
# Internal helpers — config & proxy
# ============================================================

def _get_config():
    """Lazy-load config on first access (avoids import-time side-effects)."""
    from config_manager import load_config
    return load_config()


def _make_opener(use_proxy):
    """Return a urllib opener.

    Direct mode explicitly disables urllib's implicit environment proxy.
    Proxy mode follows the centralized explicit/system/environment decision.
    """
    from network_utils import build_network_opener
    return build_network_opener("proxy" if use_proxy else "direct")


def _format_http_error(label, err, channel=None):
    """Return a compact API error message that includes the response body."""
    body = ""
    try:
        raw = err.read()
        if raw:
            body = raw.decode("utf-8", errors="replace")
    except Exception:
        body = ""
    finally:
        try:
            err.close()
        except Exception:
            pass
    body = " ".join(body.split())
    if len(body) > 500:
        body = body[:500] + "..."
    via = f" via {channel}" if channel else ""
    detail = body or getattr(err, "reason", "") or str(err)
    formatted = RuntimeError(f"{label} HTTP {err.code}{via}: {detail}")
    formatted.status_code = err.code
    return formatted


# ============================================================
# 1. DeepSeek — chat
# ============================================================

def _deepseek_chat_legacy(prompt, max_tokens=500, temp=0.7):
    """Call the DeepSeek chat-completion API.

    Parameters
    ----------
    prompt : str
        The user message sent to the model.
    max_tokens : int
        Maximum tokens in the response (default 500).
    temp : float
        Sampling temperature (default 0.7).

    Returns
    -------
    str
        The model's text reply (``choices[0].message.content``).

    Raises
    ------
    Exception
        After 2 retries the last error is re-raised.
    """
    cfg = _get_config()
    url = cfg.get("deepseek_url", "https://api.deepseek.com/v1/chat/completions")
    api_key = cfg.get("deepseek_key", "")

    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temp,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    opener = _make_opener(use_proxy=False)  # DIRECT

    last_err = None
    for attempt in range(3):  # initial + 2 retries
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with opener.open(req, timeout=120) as r:
                data = json.loads(r.read())
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1)
    raise last_err


BAILIAN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_BAILIAN_MODEL = "qwen3.7-flash"


def _bailian_chat(prompt, max_tokens=500, temp=0.7, model=None):
    cfg = _get_config()
    api_key = get_credential("bailian_api_key")
    if not api_key:
        raise RuntimeError("未配置百炼 API Key")
    body = json.dumps({
        "model": model or cfg.get("text_model", DEFAULT_BAILIAN_MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temp,
        "max_completion_tokens": max_tokens,
        "enable_thinking": False,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "YaoWoUploader/2",
    }
    opener = _make_opener(use_proxy=False)
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                cfg.get("bailian_url", BAILIAN_URL),
                data=body,
                headers=headers,
            )
            with opener.open(request, timeout=120) as response:
                payload = json.loads(response.read())
            return payload["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)
    raise last_error


def text_chat(prompt, max_tokens=500, temp=0.7, model=None):
    """Prefer Bailian and preserve existing DeepSeek configs as a safe fallback."""
    cfg = _get_config()
    bailian_error = None
    if get_credential("bailian_api_key"):
        try:
            return _bailian_chat(
                prompt, max_tokens=max_tokens, temp=temp, model=model
            )
        except Exception as exc:
            bailian_error = exc
    if str(cfg.get("deepseek_key", "") or "").strip():
        return _deepseek_chat_legacy(prompt, max_tokens=max_tokens, temp=temp)
    if bailian_error is not None:
        raise bailian_error
    raise RuntimeError("未配置百炼 API Key，也没有可用的 DeepSeek 兼容配置")


def deepseek_chat(prompt, max_tokens=500, temp=0.7, model=None):
    """Compatibility name retained for existing processor and extension modules."""
    return text_chat(prompt, max_tokens=max_tokens, temp=temp, model=model)


# ============================================================
# 2. DeepSeek — translate
# ============================================================

def deepseek_translate(text, target="ko"):
    """Translate product-category keywords via DeepSeek.

    Parameters
    ----------
    text : str
        Source text (typically Chinese product keywords).
    target : str
        Target language code (default ``"ko"`` for Korean).

    Returns
    -------
    str
        Translated text.

    Raises
    ------
    Exception
        After 2 retries the last error is re-raised.
    """
    prompt = (
        f"将以下品类关键词翻译为{target}文，只输出翻译结果，不要解释：{text}"
    )
    return deepseek_chat(prompt, max_tokens=150, temp=0.3)


# ============================================================
# 3. routeapi image generation
# ============================================================

ROUTEAPI_IMAGE_BASE_URL = "https://api.1route.dev/api/v1/images"
ROUTEAPI_DEFAULT_MODEL = "openai/gpt-image-2"


def _routeapi_base_url(configured_url):
    """Normalize legacy endpoints and base URLs to the v2 image-job base URL."""
    raw = str(configured_url or "").strip()
    if not raw:
        return ROUTEAPI_IMAGE_BASE_URL
    if "://" not in raw:
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    scheme = parts.scheme or "https"
    netloc = parts.netloc
    if (parts.hostname or "").lower() in {"api.1route.dev", "image-api.1route.dev"}:
        netloc = "api.1route.dev"
    path = (parts.path or "").rstrip("/")
    if "/api/v1/images" in path:
        path = path.split("/api/v1/images", 1)[0] + "/api/v1/images"
    elif "/v1/images" in path:
        path = path.split("/v1/images", 1)[0] + "/api/v1/images"
    else:
        path = path + "/api/v1/images"
    return urllib.parse.urlunsplit((scheme, netloc, path, "", ""))


def _routeapi_model_name(model):
    value = str(model or "").strip()
    if not value or value in {"gpt-image-2", "gpt-image-2-1k"}:
        return ROUTEAPI_DEFAULT_MODEL
    if value.startswith(("openai/", "google/")):
        return value
    return "openai/" + value.removesuffix("-1k")


def _routeapi_request(opener, url, api_key, payload=None, method=None, timeout=60):
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise _format_http_error("routeapi", exc, "proxy")
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _routeapi_error_message(snapshot):
    error = snapshot.get("error") if isinstance(snapshot, dict) else None
    if not isinstance(error, dict):
        return str(snapshot.get("message", "") if isinstance(snapshot, dict) else "")
    parts = [error.get("code"), error.get("type"), error.get("message")]
    return ": ".join(str(part) for part in parts if part)


def _routeapi_retryable_error(exc):
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status in {408, 409, 425, 429, 500, 502, 503, 504}
    return isinstance(exc, (OSError, TimeoutError, urllib.error.URLError))


def _routeapi_reference_data_urls(img_urls):
    refs = []
    skipped = []
    for img_url in img_urls[:6]:
        try:
            image_bytes = download_image(img_url)
            image_bytes = ensure_marketplace_image_spec(image_bytes)
            encoded = base64.b64encode(image_bytes).decode("ascii")
            refs.append({"dataUrl": f"data:image/jpeg;base64,{encoded}"})
        except Exception as exc:
            skipped.append((img_url, str(exc)))
    if not refs:
        detail = "; ".join(f"{url}: {error}" for url, error in skipped[:3])
        raise ValueError(f"no reference images could be downloaded: {detail}")
    return refs


def routeapi_generate(prompt, img_urls=None, model=ROUTEAPI_DEFAULT_MODEL,
                      size="1024x1024"):
    """Generate a product photo through 1Route's asynchronous image-job API.

    Parameters
    ----------
    prompt : str
        Generation prompt describing the desired output.
    img_urls : str | list[str] | None
        One or more reference image URLs (max 6 used).
    model : str
        Model name (default ``"openai/gpt-image-2"``).
    size : str
        Output size (default ``"1024x1024"``).

    Returns
    -------
    bytes
        Decoded bytes from the first completed job image.

    Raises
    ------
    Exception
        If submission, polling, job execution, or result retrieval fails.
    """
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    elif img_urls is None:
        img_urls = []

    cfg = _get_config()
    api_key = str(cfg.get("routeapi_key", "") or "").strip()
    if not api_key:
        raise RuntimeError("未配置 routeapi Key")
    base_url = _routeapi_base_url(cfg.get("routeapi_url"))
    refs = _routeapi_reference_data_urls(img_urls)
    payload = {
        "model": _routeapi_model_name(model),
        "prompt": str(prompt or "").strip(),
        "size": str(size or "auto"),
        "images": refs,
    }
    opener = _make_opener(use_proxy=True)
    submitted = _routeapi_request(
        opener,
        f"{base_url}/edits/jobs",
        api_key,
        payload=payload,
        method="POST",
        timeout=120,
    )
    job_id = str(submitted.get("jobId", "") or "").strip()
    if not job_id:
        raise ValueError("routeapi job submission response missing jobId")

    guidance = submitted.get("durationGuidance") or {}
    try:
        guided_timeout = float(guidance.get("timeoutSeconds", 450) or 450)
    except (TypeError, ValueError):
        guided_timeout = 450
    deadline = time.monotonic() + max(180, min(guided_timeout + 60, 900))
    status_url = f"{base_url}/jobs/{urllib.parse.quote(job_id, safe='')}"
    poll_interval = 2.0
    snapshot = submitted
    poll_failures = 0
    while time.monotonic() < deadline:
        try:
            snapshot = _routeapi_request(opener, status_url, api_key, timeout=60)
            poll_failures = 0
        except Exception as exc:
            poll_failures += 1
            if poll_failures <= 5 and _routeapi_retryable_error(exc):
                time.sleep(min(poll_interval * poll_failures, 10))
                continue
            raise RuntimeError(
                f"routeapi job status polling failed: {exc}"
            ) from exc
        phase = str(snapshot.get("phase", "") or "").lower()
        if phase == "completed":
            break
        if phase in {"failed", "cancelled"}:
            detail = _routeapi_error_message(snapshot) or phase
            raise RuntimeError(f"routeapi job {phase}: {detail}")
        if phase not in {"queued", "running"}:
            raise ValueError(f"routeapi job returned unknown phase: {phase or '<empty>'}")
        time.sleep(poll_interval)
    else:
        raise TimeoutError(f"routeapi job timed out after {int(max(180, min(guided_timeout + 60, 900)))}s")

    result = None
    for result_attempt in range(6):
        try:
            result = _routeapi_request(
                opener, f"{status_url}/result", api_key, timeout=120
            )
            break
        except Exception as exc:
            if result_attempt < 5 and _routeapi_retryable_error(exc):
                time.sleep(min(poll_interval * (result_attempt + 1), 10))
                continue
            raise RuntimeError(
                f"routeapi job result retrieval failed: {exc}"
            ) from exc
    images = result.get("images") if isinstance(result, dict) else None
    if not isinstance(images, list) or not images:
        raise ValueError("routeapi result response missing images")
    first = images[0] if isinstance(images[0], dict) else {}
    encoded = str(first.get("base64", "") or "")
    if encoded:
        if "," in encoded and encoded.lstrip().lower().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        return base64.b64decode(encoded)
    result_url = str(first.get("url", "") or "").strip()
    if result_url:
        return download_image(urllib.parse.urljoin(base_url + "/", result_url))
    raise ValueError("routeapi result image missing both base64 and url")


# ============================================================
# 4. hfsyapi image generation (JSON-based, reference_images)
# ============================================================

def hfsyapi_generate(prompt, img_urls=None, model="gpt-image-2",
                     size="1024x1024", response_format="b64_json"):
    """Generate image via hfsyapi.cn — JSON-based, supports reference_images.

    Unlike routeapi (multipart), this API accepts a plain JSON body with
    ``reference_images`` as an array of ``data:`` URLs (base64).  We download
    each reference image ourselves (through proxy) and embed it as base64 so
    the hfsyapi server never needs to reach the original hosts.

    Retries 3 times (initial + 2 retries), 3s interval.

    Parameters
    ----------
    prompt : str
        Image generation prompt.
    img_urls : str or list[str] or None
        Reference image URL(s) for image-to-image generation.
    model : str
        Model name. Default ``gpt-image-2``, also supports ``gpt-image-2pro``.
    size : str
        Output resolution. Default ``1024x1024``.
    response_format : str
        ``b64_json`` or ``url``. Default ``b64_json`` (returns decoded bytes).

    Returns
    -------
    bytes
        Decoded image bytes (when ``response_format="b64_json"``).
    str
        Image URL (when ``response_format="url"``).

    Raises
    ------
    Exception
        After 3 attempts the last error is re-raised.
    """
    if isinstance(img_urls, str):
        img_urls = [img_urls]
    elif img_urls is None:
        img_urls = []

    # Download reference images ourselves and embed as data URLs.
    # hfsyapi cannot reach Shein/1688 hosts, but we can through proxy.
    data_refs = []
    for u in (img_urls or [])[:6]:
        try:
            img_bytes = download_image(u)
            b64 = base64.b64encode(img_bytes).decode()
            ext = "jpg"
            if u.lower().endswith(".png"):
                ext = "png"
            elif u.lower().endswith(".webp"):
                ext = "webp"
            data_refs.append(f"data:image/{ext};base64,{b64}")
        except Exception:
            continue  # skip unreachable reference images

    cfg = _get_config()
    api_url = cfg.get("hfsyapi_url", "https://www.hfsyapi.cn/v1/images/generations")
    api_key = cfg.get("hfsyapi_key", "")

    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": response_format,
    }
    if data_refs:
        body["reference_images"] = data_refs

    json_data = json.dumps(body).encode("utf-8")

    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(api_url, data=json_data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            })
            # hfsyapi is China-based; try direct, fall back to proxy
            opener = _make_opener(use_proxy=(attempt >= 1))
            with opener.open(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            if response_format == "b64_json":
                b64_data = result["data"][0].get("b64_json", "")
                if b64_data:
                    return base64.b64decode(b64_data)
                img_url = result["data"][0].get("url", "")
                if img_url:
                    return download_image(img_url)
            else:
                return result["data"][0].get("url", "")

        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(3)
    raise last_err


# ============================================================
# 5. Unified image generation — dispatch by api_choice
# ============================================================

def generate_image(prompt, img_urls=None, api_choice=None,
                   model=None, size="1024x1024"):
    """Generate an image using the configured API.

    Parameters
    ----------
    prompt : str
        Generation prompt.
    img_urls : str or list[str] or None
        Reference image URLs.
    api_choice : str or None
        ``"routeapi"`` or ``"hfsyapi"``.  Defaults to ``config.image_api``.
    model : str or None
        Model override (uses each API's default when None).
    size : str
        Output size (default ``1024x1024``).

    Returns
    -------
    bytes
        Decoded image bytes.
    """
    if api_choice is None:
        api_choice = _get_config().get("image_api", "routeapi")

    if api_choice == "hfsyapi":
        m = model or "gpt-image-2"
        return hfsyapi_generate(prompt, img_urls, model=m, size=size)
    else:
        m = model or ROUTEAPI_DEFAULT_MODEL
        return routeapi_generate(prompt, img_urls, model=m, size=size)


# ============================================================
# 5b. Image download
# ============================================================

def download_image(url):
    """Download raw image bytes — direct first, proxy fallback.

    Parameters
    ----------
    url : str
        Image URL.

    Returns
    -------
    bytes
        Raw image bytes.

    Raises
    ------
    Exception
        On network error or timeout (30 s).
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    # Try direct first (Shein/1688 images work direct from China)
    try:
        with _make_opener(use_proxy=False).open(req, timeout=30) as r:
            return r.read()
    except Exception:
        pass
    # Fall back to proxy
    with _make_opener(use_proxy=True).open(req, timeout=30) as r:
        return r.read()


# ============================================================
# 6. StorageProvider interface + CloudinaryProvider
# ============================================================

class StorageProvider(ABC):
    """Abstract interface for image storage backends."""

    @abstractmethod
    def upload(self, image_bytes, filename="image.jpg"):
        """Upload an image and return its public URL.

        Parameters
        ----------
        image_bytes : bytes
            Raw image data.
        filename : str
            Logical file name (may be used to infer format / public-id).

        Returns
        -------
        str
            Publicly accessible URL of the uploaded image.
        """
        ...


class CloudinaryProvider(StorageProvider):
    """Cloudinary storage backend using sha1 signature authentication.

    Config keys read from ``config["storage"]``:

    - cloud_name
    - api_key
    - api_secret

    Behaviour
    ---------
    - Images larger than 2 MB are compressed to JPEG quality 60 via PIL
      before upload.
    - The returned URL includes ``q_auto:best,e_sharpen:100`` transforms.
    - All requests go through the proxy.
    - Retries 3 times with a 2-second interval on failure.
    """

    def __init__(self, cloud_name, api_key, api_secret):
        self.cloud_name = cloud_name
        self.api_key = api_key
        self.api_secret = api_secret
        self._upload_url = (
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        )

    def upload(self, image_bytes, filename="image.jpg"):
        """Upload to Cloudinary, returning the enhanced secure URL."""
        # --- Meet marketplace upload constraints before storing ---
        data = self._ensure_upload_spec(image_bytes)

        # --- Build signed form body ---
        b64 = base64.b64encode(data).decode()
        timestamp = str(int(time.time()))
        # Cloudinary signature: SHA1("timestamp={timestamp}" + api_secret)
        signature = hashlib.sha1(
            f"timestamp={timestamp}{self.api_secret}".encode()
        ).hexdigest()

        from urllib.parse import urlencode
        body = urlencode({
            "file": f"data:image/jpeg;base64,{b64}",
            "api_key": self.api_key,
            "timestamp": timestamp,
            "signature": signature,
        }).encode()

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        opener = _make_opener(use_proxy=True)  # USE proxy

        last_err = None
        for attempt in range(4):  # initial + 3 retries
            try:
                req = urllib.request.Request(
                    self._upload_url, data=body, headers=headers
                )
                with opener.open(req, timeout=60) as r:
                    resp = json.loads(r.read())
                if resp.get("error"):
                    raise Exception(resp["error"].get("message", "Cloudinary upload failed"))
                url = resp.get("secure_url", "")
                if not url:
                    raise Exception("Cloudinary response missing secure_url")
                # Apply free Cloudinary enhancements
                url = url.replace(
                    "/upload/", "/upload/q_auto:best/e_sharpen:100/"
                )
                return url
            except Exception as e:
                last_err = e
                if attempt < 3:
                    time.sleep(2)
        raise last_err

    @staticmethod
    def _ensure_upload_spec(image_bytes):
        """Ensure product images are JPEG, >=600x600 and under 300 KB."""
        return ensure_marketplace_image_spec(image_bytes)

    @staticmethod
    def _ensure_min_dimensions(image_bytes):
        """Resize image so both width and height are at least 600px."""
        return ensure_marketplace_image_spec(image_bytes)

    @staticmethod
    def _ensure_under_2mb(image_bytes):
        """Backward-compatible helper; now targets 300 KB."""
        return ensure_marketplace_image_spec(image_bytes)


class TencentCOSProvider(StorageProvider):
    """Tencent Cloud COS storage backend using XML API v5 signing.

    Config keys read from ``config["storage"]``:

    - secret_id
    - secret_key
    - bucket, for example ``yaowoo-1443995558``
    - region, for example ``ap-hongkong``
    - prefix, optional object prefix
    - base_url, optional public base URL

    Uploads are stored as JPEG objects and return public COS URLs.  The bucket
    must be configured as public-read for marketplace crawlers to fetch them.
    """

    def __init__(self, secret_id, secret_key, bucket, region,
                 prefix="gmarket/uploads", base_url=""):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region
        self.prefix = str(prefix or "").strip("/")
        self.host = f"{bucket}.cos.{region}.myqcloud.com"
        self.base_url = (base_url or f"https://{self.host}").rstrip("/")

    def upload(self, image_bytes, filename="image.jpg"):
        """Upload to Tencent COS, returning the public JPEG URL."""
        if not all([self.secret_id, self.secret_key, self.bucket, self.region]):
            raise ValueError("Tencent COS storage requires secret_id, secret_key, bucket and region")

        data = CloudinaryProvider._ensure_upload_spec(image_bytes)
        key = self._build_object_key(data, filename)
        target_url = f"https://{self.host}/{urllib.parse.quote(key, safe='/')}"
        headers = {
            "Host": self.host,
            "Content-Type": "image/jpeg",
            "Content-Length": str(len(data)),
            "Authorization": self._authorization("PUT", key, "image/jpeg"),
        }
        opener = _make_opener(use_proxy=True)  # storage upload USE proxy

        last_err = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    target_url, data=data, headers=headers, method="PUT"
                )
                with opener.open(req, timeout=60) as resp:
                    resp.read()
                    if getattr(resp, "status", 200) not in (200, 201):
                        raise Exception(f"Tencent COS upload failed, status={getattr(resp, 'status', None)}")
                return f"{self.base_url}/{urllib.parse.quote(key, safe='/')}"
            except urllib.error.HTTPError as e:
                last_err = _format_http_error("Tencent COS upload", e, "proxy")
            except Exception as e:
                last_err = e
            if attempt < 3:
                time.sleep(2)
        raise last_err

    def _build_object_key(self, image_bytes, filename):
        safe_name = str(filename or "image.jpg").replace("\\", "/").split("/")[-1]
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_name).strip("._-")
        stem = safe_name.rsplit(".", 1)[0] if safe_name else "image"
        if not stem:
            stem = "image"
        digest = hashlib.sha1(image_bytes).hexdigest()[:12]
        stamp = int(time.time() * 1000)
        object_name = f"{stamp}_{digest}_{stem}.jpg"
        if self.prefix:
            return f"{self.prefix}/{object_name}"
        return object_name

    def _authorization(self, method, key, content_type):
        now = int(time.time())
        key_time = f"{now};{now + 600}"
        sign_key = hmac.new(
            self.secret_key.encode("utf-8"),
            key_time.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()
        path = "/" + urllib.parse.quote(key, safe="/")
        sign_headers = {
            "content-type": content_type,
            "host": self.host,
        }
        header_items = sorted(sign_headers.items())
        header_list = ";".join(k for k, _ in header_items)
        http_headers = "&".join(
            f"{self._quote_sign(k)}={self._quote_sign(v)}"
            for k, v in header_items
        )
        http_string = f"{method.lower()}\n{path}\n\n{http_headers}\n"
        string_to_sign = "sha1\n{}\n{}\n".format(
            key_time,
            hashlib.sha1(http_string.encode("utf-8")).hexdigest(),
        )
        signature = hmac.new(
            sign_key.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).hexdigest()
        return (
            "q-sign-algorithm=sha1"
            f"&q-ak={self.secret_id}"
            f"&q-sign-time={key_time}"
            f"&q-key-time={key_time}"
            f"&q-header-list={header_list}"
            "&q-url-param-list="
            f"&q-signature={signature}"
        )

    @staticmethod
    def _quote_sign(value):
        return urllib.parse.quote(str(value), safe="")


# ============================================================
# 7. Factory
# ============================================================

def create_storage_provider(config=None):
    """Create a :class:`StorageProvider` instance from configuration.

    Reads ``config["storage"]``.  Falls back to ``load_config()`` when
    *config* is not supplied.

    Parameters
    ----------
    config : dict or None
        Full configuration dict (as returned by ``config_manager.load_config()``).

    Returns
    -------
    StorageProvider
        Defaults to :class:`CloudinaryProvider`.
    """
    if config is None:
        config = _get_config()

    storage_cfg = config.get("storage", {})
    provider_name = storage_cfg.get("provider", "cloudinary")

    if provider_name == "cloudinary":
        return CloudinaryProvider(
            cloud_name=storage_cfg.get("cloud_name", ""),
            api_key=storage_cfg.get("api_key", ""),
            api_secret=storage_cfg.get("api_secret", ""),
        )

    if provider_name in ("tencent_cos", "cos"):
        return TencentCOSProvider(
            secret_id=storage_cfg.get("secret_id", ""),
            secret_key=storage_cfg.get("secret_key", ""),
            bucket=storage_cfg.get("bucket", DEFAULT_TENCENT_COS_BUCKET),
            region=storage_cfg.get("region", DEFAULT_TENCENT_COS_REGION),
            prefix=storage_cfg.get("prefix", DEFAULT_TENCENT_COS_PREFIX),
            base_url=storage_cfg.get("base_url", DEFAULT_TENCENT_COS_BASE_URL),
        )

    raise ValueError(f"Unknown storage provider: {provider_name}")
