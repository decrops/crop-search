"""Phase B1 — shared HTTP client with JSON + binary cache modes.

Replaces the bare ``requests.get`` calls in the discovery connectors and the
JSON-only ``backfill.http_get_with_retry``. Provides:

- **JSON mode** (``get_json``) for discovery APIs — caches the parsed payload.
- **Binary mode** (``get_binary``) for HTML/PDF fetches — caches raw bytes plus a
  sidecar of ``status``, ``headers``, final ``url``, ``content_type``.

Both modes share exponential backoff + retry on 429/5xx and a per-host on-disk
cache keyed by ``method+url+sorted-params``. Cached responses round-trip
``text``, ``content``, ``headers``, and ``url`` so binary fetches work, unlike
the old JSON-only cached response. The deferred-retry queue itself is owned by
the discovery runner (it catches :class:`HttpError` and enqueues), keeping this
client a pure request/cache primitive.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode, urlparse

import requests

RETRY_STATUSES = (429, 500, 502, 503, 504)


class HttpError(Exception):
    """Raised when a request exhausts retries or returns a non-retryable error."""


class CachedResponse:
    """A response that round-trips text/content/headers/url, from net or cache."""

    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        content: Optional[bytes] = None,
        text: Optional[str] = None,
        json_payload: Any = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = dict(headers or {})
        self._content = content
        self._text = text
        self._json = json_payload

    @property
    def content(self) -> bytes:
        if self._content is not None:
            return self._content
        if self._text is not None:
            return self._text.encode("utf-8")
        return b""

    @property
    def text(self) -> str:
        if self._text is not None:
            return self._text
        if self._content is not None:
            return self._content.decode("utf-8", "replace")
        return ""

    def json(self) -> Any:
        if self._json is not None:
            return self._json
        return json.loads(self.text)


def _cache_key(method: str, url: str, params: Optional[Dict[str, Any]]) -> str:
    raw = "{0} {1} {2}".format(method.upper(), url, _stable_params(params))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _stable_params(params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return ""
    if isinstance(params, dict):
        return urlencode(sorted(params.items()), doseq=True)
    return urlencode(sorted(params), doseq=True)


def _host(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    return netloc or "nohost"


class HttpClient:
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        *,
        max_retries: int = 4,
        base_delay: float = 0.5,
        retry_statuses=RETRY_STATUSES,
        getter: Optional[Callable[..., Any]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.retry_statuses = tuple(retry_statuses)
        # ``getter`` is injectable so tests can stub the network without touching
        # ``requests``; default delegates to ``requests.get``.
        self.getter = getter or requests.get
        self.sleeper = sleeper

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_json(self, url, params=None, headers=None, timeout: int = 20) -> CachedResponse:
        return self._get(url, params, headers, timeout, mode="json")

    def get_binary(self, url, params=None, headers=None, timeout: int = 30) -> CachedResponse:
        return self._get(url, params, headers, timeout, mode="binary")

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _cache_paths(self, mode: str, key: str, host: str):
        if not self.cache_dir:
            return None, None
        base = self.cache_dir / host
        if mode == "json":
            return base / "{0}.json".format(key), None
        return base / "{0}.bin".format(key), base / "{0}.meta.json".format(key)

    def _read_cache(self, mode: str, key: str, host: str, url: str) -> Optional[CachedResponse]:
        data_path, meta_path = self._cache_paths(mode, key, host)
        if not data_path or not data_path.exists():
            return None
        if mode == "json":
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            return CachedResponse(status_code=200, url=url, json_payload=payload)
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path and meta_path.exists() else {}
        return CachedResponse(
            status_code=meta.get("status", 200),
            url=meta.get("url", url),
            headers=meta.get("headers", {}),
            content=data_path.read_bytes(),
        )

    def _write_cache(self, mode: str, key: str, host: str, response: CachedResponse) -> None:
        data_path, meta_path = self._cache_paths(mode, key, host)
        if not data_path:
            return
        data_path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "json":
            data_path.write_text(json.dumps(response.json()), encoding="utf-8")
            return
        data_path.write_bytes(response.content)
        meta_path.write_text(
            json.dumps(
                {
                    "status": response.status_code,
                    "url": response.url,
                    "headers": response.headers,
                }
            ),
            encoding="utf-8",
        )

    def _get(self, url, params, headers, timeout, mode) -> CachedResponse:
        key = _cache_key("GET", url, params)
        host = _host(url)
        cached = self._read_cache(mode, key, host, url)
        if cached is not None:
            return cached

        last_error = "no attempts made"
        for attempt in range(self.max_retries):
            try:
                raw = self.getter(url, params=params, headers=headers, timeout=timeout)
            except Exception as exc:  # network error -> retry
                last_error = str(exc)
                self._backoff(attempt)
                continue
            status = getattr(raw, "status_code", 200)
            if status in self.retry_statuses:
                last_error = "HTTP {0}".format(status)
                self._backoff(attempt)
                continue
            if status >= 400:
                raise HttpError("HTTP {0} for {1}".format(status, url))
            response = self._materialize(raw, url, mode)
            self._write_cache(mode, key, host, response)
            return response
        raise HttpError("exhausted retries for {0}: {1}".format(url, last_error))

    def _materialize(self, raw, url, mode) -> CachedResponse:
        final_url = getattr(raw, "url", url) or url
        headers = dict(getattr(raw, "headers", {}) or {})
        status = getattr(raw, "status_code", 200)
        if mode == "json":
            return CachedResponse(status_code=status, url=final_url, headers=headers, json_payload=raw.json())
        content = getattr(raw, "content", None)
        if content is None:
            content = getattr(raw, "text", "").encode("utf-8")
        return CachedResponse(status_code=status, url=final_url, headers=headers, content=content)

    def _backoff(self, attempt: int) -> None:
        self.sleeper(self.base_delay * (2 ** attempt))
