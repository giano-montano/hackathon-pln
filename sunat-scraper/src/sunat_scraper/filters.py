"""Canonicalizacion de URLs y decision de inclusion/exclusion.

El orden de decision esta fijado por la configuracion (ver config/sources.yaml):
formato -> dominio -> exclusiones -> URLs exactas -> prefijos -> patrones -> rechazo.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import Config, Source

# Motivos de rechazo (los mismos que se escriben en rejected.jsonl).
REASON_EXCLUDED = "excluded_by_rule"
REASON_PDF = "pdf"
REASON_NON_HTML = "non_html"
REASON_LOGIN = "login"

PDF_EXTENSIONS = {".pdf"}
NON_HTML_EXTENSIONS = {
    # Ofimatica
    ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".rtf", ".odt", ".csv",
    # Comprimidos y binarios
    ".zip", ".rar", ".7z", ".gz", ".tar", ".exe", ".msi", ".apk", ".dmg",
    # Imagenes / audio / video
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tif", ".tiff",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".mkv", ".webm", ".flv",
    # Recursos y datos
    ".css", ".js", ".json", ".xml", ".txt", ".rss",
}

# Parametros de tracking: se eliminan al canonicalizar para no duplicar paginas.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_ga",
}

# Rutas de zonas autenticadas: nunca se descargan.
LOGIN_MARKERS = ("/login", "/signin", "/user/login", "/user/password", "iniciar-sesion")


@dataclass(frozen=True)
class FilterResult:
    """Decision sobre una URL, con la regla que la produjo (para el dry-run)."""

    url: str
    accepted: bool
    rule: str
    reason: str | None = None


def canonicalize(url: str) -> str:
    """URL canonica: sin fragmento, sin tracking, host en minusculas, sin '/' final.

    Es la primera capa de deduplicacion (la segunda es el hash del texto limpio).
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if (scheme == "https" and netloc.endswith(":443")) or (scheme == "http" and netloc.endswith(":80")):
        netloc = netloc.rsplit(":", 1)[0]

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS)
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def _extension(path: str) -> str:
    last = path.rsplit("/", 1)[-1]
    return "." + last.rsplit(".", 1)[-1].lower() if "." in last else ""


def _haystack(url: str) -> str:
    """Parte de la URL sobre la que se buscan patrones: ruta + query, en minusculas."""
    parts = urlsplit(url)
    return (parts.path + ("?" + parts.query if parts.query else "")).lower()


class UrlFilter:
    """Aplica las reglas de una fuente a una URL."""

    def __init__(self, source: Source, global_exclude_patterns: list[str] | None = None):
        self.source = source
        self.global_exclude_patterns = [p.lower() for p in (global_exclude_patterns or [])]

    def decide(self, url: str) -> FilterResult:
        canon = canonicalize(url)
        parts = urlsplit(canon)
        path = _haystack(canon)
        ext = _extension(parts.path)

        # 1. Formato: solo se procesa HTML. Los PDF no se descargan nunca.
        if ext in PDF_EXTENSIONS:
            return FilterResult(canon, False, "pdf_extension", REASON_PDF)
        if ext in NON_HTML_EXTENSIONS:
            return FilterResult(canon, False, f"non_html_extension:{ext}", REASON_NON_HTML)

        # 2. Dominio de la fuente (evita salir del sitio configurado).
        if parts.netloc != self.source.domain.lower():
            return FilterResult(canon, False, f"wrong_domain:{parts.netloc}", REASON_EXCLUDED)

        # 3. Zonas autenticadas.
        if any(marker in path for marker in LOGIN_MARKERS):
            return FilterResult(canon, False, "login_area", REASON_LOGIN)

        # 4. Exclusiones (globales y de la fuente). Siempre ganan.
        for pattern in self.global_exclude_patterns:
            if pattern in path:
                return FilterResult(canon, False, f"global_exclude:{pattern}", REASON_EXCLUDED)
        for pattern in self.source.exclude_patterns:
            if pattern.lower() in path:
                return FilterResult(canon, False, f"exclude_pattern:{pattern}", REASON_EXCLUDED)

        # 5. URLs exactas incluidas (se compara solo la ruta, sin query).
        for exact in self.source.include_urls:
            if canonicalize_path(parts.path) == canonicalize_path(exact):
                return FilterResult(canon, True, f"include_url:{exact}")

        # 6. Prefijos incluidos.
        for prefix in self.source.include_prefixes:
            if path.startswith(prefix.lower()):
                return FilterResult(canon, True, f"include_prefix:{prefix}")

        # 7. Patrones incluidos.
        for pattern in self.source.include_patterns:
            if pattern.lower() in path:
                return FilterResult(canon, True, f"include_pattern:{pattern}")

        # 8. Rechazo por defecto.
        return FilterResult(canon, False, "default_reject", REASON_EXCLUDED)


    def followable(self, url: str) -> bool:
        """True si la pagina se puede VISITAR para descubrir enlaces.

        Es distinto de aceptar su contenido: una pagina indice no pasa los
        filtros pero enlaza a las que si. Solo se visita lo que quedo en el
        rechazo por defecto; lo excluido por una regla no se recorre, y asi las
        ramas fuera de alcance (aduanas, noticias...) se podan de raiz.
        """
        result = self.decide(url)
        return result.accepted or result.rule == "default_reject"


def canonicalize_path(path: str) -> str:
    """Normaliza una ruta de config (include_urls) para compararla con la URL."""
    path = path.strip().lower()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def filter_for(config: Config, source_name: str) -> UrlFilter:
    return UrlFilter(config.source(source_name), config.global_exclude_patterns)
