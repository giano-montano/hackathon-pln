"""Descarga de HTML con cache local, limite de velocidad y respeto a robots.txt.

Solo se descargan paginas HTML publicas. No se accede a zonas autenticadas,
no se evaden captchas y nunca se envian credenciales.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import Defaults
from .filters import canonicalize

log = logging.getLogger(__name__)

RETRY_STATUS = {408, 429, 500, 502, 503, 504}


class TransientHttpError(Exception):
    """Error que amerita reintento (5xx, 429, timeouts)."""


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    content_type: str | None = None
    html: str | None = None
    from_cache: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.html is not None and self.error is None


def url_key(url: str) -> str:
    """Nombre de archivo estable y corto para la cache."""
    return hashlib.sha1(canonicalize(url).encode("utf-8")).hexdigest()[:16]


class RateLimiter:
    """Pausa entre solicitudes para no exceder N solicitudes por segundo."""

    def __init__(self, requests_per_second: float):
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


class RobotsCache:
    """robots.txt por host, consultado una sola vez."""

    def __init__(self, client: httpx.Client, user_agent: str, enabled: bool = True):
        self.client = client
        self.user_agent = user_agent
        self.enabled = enabled
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._parsers:
            self._parsers[host] = self._load(host)
        parser = self._parsers[host]
        if parser is None:  # sin robots.txt legible -> se permite
            return True
        return parser.can_fetch(self.user_agent, url)

    def _load(self, host: str):
        try:
            response = self.client.get(f"{host}/robots.txt", timeout=10.0)
        except httpx.HTTPError as exc:
            log.debug("robots.txt no accesible en %s: %s", host, exc)
            return None
        if response.status_code >= 400:
            return None
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


class Downloader:
    """Descarga y cachea HTML. Con `resume`, no vuelve a pedir lo ya guardado."""

    def __init__(self, defaults: Defaults, cache_dir: Path, resume: bool = False):
        self.defaults = defaults
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.limiter = RateLimiter(defaults.requests_per_second)
        self.client = httpx.Client(
            headers={
                "User-Agent": defaults.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-PE,es;q=0.9",
            },
            timeout=defaults.timeout,
            follow_redirects=True,
        )
        self.robots = RobotsCache(self.client, defaults.user_agent, defaults.respect_robots)

    # -- cache ---------------------------------------------------------------

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = url_key(url)
        return self.cache_dir / f"{key}.html", self.cache_dir / f"{key}.json"

    def cached(self, url: str) -> FetchResult | None:
        html_path, meta_path = self._paths(url)
        if not (html_path.exists() and meta_path.exists()):
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return FetchResult(
                url=url,
                final_url=meta.get("final_url"),
                status=meta.get("status"),
                content_type=meta.get("content_type"),
                html=html_path.read_text(encoding="utf-8"),
                from_cache=True,
            )
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Cache ilegible para %s: %s", url, exc)
            return None

    def _store(self, result: FetchResult) -> None:
        html_path, meta_path = self._paths(result.url)
        html_path.write_text(result.html or "", encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "url": result.url,
                    "final_url": result.final_url,
                    "status": result.status,
                    "content_type": result.content_type,
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- descarga ------------------------------------------------------------

    def fetch(self, url: str) -> FetchResult:
        url = canonicalize(url)

        if self.resume:
            hit = self.cached(url)
            if hit:
                return hit

        if not self.robots.allowed(url):
            return FetchResult(url=url, error="robots_disallowed")

        try:
            response = self._request(url)
        except TransientHttpError as exc:
            return FetchResult(url=url, error=f"http_error: {exc}")
        except httpx.HTTPError as exc:
            return FetchResult(url=url, error=f"transport_error: {exc}")

        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        result = FetchResult(
            url=url,
            final_url=str(response.url),
            status=response.status_code,
            content_type=content_type,
        )

        if response.status_code >= 400:
            result.error = f"status_{response.status_code}"
            return result

        # Solo HTML: si el servidor devuelve PDF u otro formato, se descarta.
        if content_type and not content_type.startswith(("text/html", "application/xhtml")):
            result.error = f"non_html_content_type: {content_type}"
            return result

        result.html = response.text
        self._store(result)
        return result

    def fetch_bytes(self, url: str) -> bytes | None:
        """Descarga sin cache ni filtro de tipo. Se usa para los sitemaps (XML)."""
        if not self.robots.allowed(url):
            log.warning("robots.txt no permite %s", url)
            return None
        try:
            response = self._request(url)
        except (TransientHttpError, httpx.HTTPError) as exc:
            log.warning("No se pudo descargar %s: %s", url, exc)
            return None
        if response.status_code >= 400:
            log.warning("%s devolvio %s", url, response.status_code)
            return None
        return response.content

    def _request(self, url: str) -> httpx.Response:
        """Peticion con pausa previa y reintentos con backoff exponencial."""

        @retry(
            stop=stop_after_attempt(self.defaults.max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception_type((TransientHttpError, httpx.TimeoutException, httpx.TransportError)),
            reraise=True,
        )
        def _do() -> httpx.Response:
            self.limiter.wait()
            response = self.client.get(url)
            if response.status_code in RETRY_STATUS:
                raise TransientHttpError(f"status {response.status_code}")
            return response

        return _do()

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Downloader":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
