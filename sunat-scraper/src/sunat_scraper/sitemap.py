"""Descubrimiento de URLs.

Via principal: el sitemap. Soporta <urlset>, <sitemapindex> y sitemaps anidados;
rss.xml se usa solo como respaldo cuando no hay sitemap util.

Via secundaria: `discover_links`, un recorrido de enlaces acotado para las fuentes
que NO publican sitemap (es el caso de orientacion.sunat.gob.pe y
emprender.sunat.gob.pe: ambos devuelven 404 en /sitemap.xml). No hay logica
especifica de ningun sitio: se siguen enlaces y deciden los mismos filtros.
"""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup
from lxml import etree

# Un "fetcher" recibe una URL y devuelve los bytes, o None si fallo.
# Se inyecta para que las pruebas no dependan de internet.
Fetcher = Callable[[str], bytes | None]
HtmlFetcher = Callable[[str], str | None]
UrlPredicate = Callable[[str], bool]

MAX_DEPTH = 3
MAX_CRAWL_PAGES = 400


@dataclass(frozen=True)
class DiscoveredUrl:
    loc: str
    lastmod: str | None = None
    from_sitemap: str | None = None


def _decode(content: bytes) -> bytes:
    """Descomprime sitemaps .xml.gz de forma transparente."""
    if content[:2] == b"\x1f\x8b":
        return gzip.decompress(content)
    return content


def _localname(element) -> str:
    """Nombre de la etiqueta sin namespace (los sitemaps usan varios)."""
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element, name: str) -> str | None:
    for child in element:
        if _localname(child) == name and child.text:
            return child.text.strip()
    return None


def parse_sitemap(content: bytes) -> tuple[list[DiscoveredUrl], list[str]]:
    """Devuelve (urls, sitemaps_anidados) de un documento XML.

    Un <urlset> aporta URLs; un <sitemapindex> aporta sitemaps anidados.
    Un <rss> aporta URLs desde <item><link>.
    """
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(_decode(content), parser=parser)
    except etree.XMLSyntaxError:
        return [], []
    if root is None:
        return [], []

    urls: list[DiscoveredUrl] = []
    nested: list[str] = []

    for element in root.iter():
        name = _localname(element)
        if name == "url":
            loc = _child_text(element, "loc")
            if loc:
                urls.append(DiscoveredUrl(loc=loc, lastmod=_child_text(element, "lastmod")))
        elif name == "sitemap":
            loc = _child_text(element, "loc")
            if loc:
                nested.append(loc)
        elif name == "item":  # respaldo RSS
            link = _child_text(element, "link")
            if link:
                urls.append(DiscoveredUrl(loc=link, lastmod=_child_text(element, "pubdate")))

    return urls, nested


def discover(sitemap_url: str, fetch: Fetcher, max_depth: int = MAX_DEPTH) -> list[DiscoveredUrl]:
    """Recorre un sitemap y sus anidados. Devuelve URLs unicas en orden de aparicion."""
    pending: list[tuple[str, int]] = [(sitemap_url, 0)]
    visited_sitemaps: set[str] = set()
    seen_locs: set[str] = set()
    found: list[DiscoveredUrl] = []

    while pending:
        current, depth = pending.pop(0)
        if current in visited_sitemaps or depth > max_depth:
            continue
        visited_sitemaps.add(current)

        content = fetch(current)
        if not content:
            continue

        urls, nested = parse_sitemap(content)
        for url in urls:
            if url.loc not in seen_locs:
                seen_locs.add(url.loc)
                found.append(DiscoveredUrl(url.loc, url.lastmod, from_sitemap=current))
        for nested_url in nested:
            if nested_url not in visited_sitemaps:
                pending.append((nested_url, depth + 1))

    return found


def rss_fallback_url(sitemap_url: str) -> str:
    """URL de rss.xml equivalente, usada solo si el sitemap no dio resultados."""
    base = sitemap_url.rsplit("/", 1)[0]
    return f"{base}/rss.xml"


# --------------------------------------------------------------------------
# Recorrido de enlaces (para fuentes sin sitemap)
# --------------------------------------------------------------------------

def extract_links(html: str, base_url: str) -> list[str]:
    """Enlaces <a href> de una pagina, resueltos a URL absoluta y sin fragmento."""
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url, _ = urldefrag(urljoin(base_url, href))
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def discover_links(
    start_urls: list[str],
    fetch_html: HtmlFetcher,
    follow: UrlPredicate,
    candidate: UrlPredicate,
    max_pages: int = MAX_CRAWL_PAGES,
    max_depth: int = MAX_DEPTH,
) -> list[DiscoveredUrl]:
    """Recorrido en anchura desde `start_urls`, acotado por paginas y profundidad.

    Devuelve TODAS las URLs candidatas que vio, aceptadas o no: filtrar es tarea
    de quien llama, igual que con un sitemap. Asi el dry-run puede informar de lo
    rechazado y de la regla aplicada.

    `candidate` decide que URLs se reportan (normalmente, las del dominio);
    `follow`, cuales se visitan. Son distintas a proposito: una pagina indice se
    visita aunque su contenido no sirva, y una rama excluida no se visita.
    """
    pending: list[tuple[str, int]] = [(url, 0) for url in start_urls]
    visited: set[str] = set()
    seen: dict[str, DiscoveredUrl] = {}
    fetched = 0

    def remember(url: str) -> None:
        # Reportar no requiere descargar: basta con haber visto el enlace. Asi un
        # presupuesto pequeno de descargas no tira URLs ya conocidas.
        if candidate(url):
            seen.setdefault(url, DiscoveredUrl(loc=url))

    for url in start_urls:
        remember(url)

    while pending and fetched < max_pages:
        current, depth = pending.pop(0)
        if current in visited or depth >= max_depth or not follow(current):
            continue
        visited.add(current)

        html = fetch_html(current)
        fetched += 1
        if not html:
            continue

        for link in extract_links(html, current):
            remember(link)
            if link not in visited:
                pending.append((link, depth + 1))

    return list(seen.values())
