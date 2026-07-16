"""Descubrimiento de URLs desde sitemaps. Sin red: el fetcher es un diccionario."""

from __future__ import annotations

from conftest import fixture_bytes

from sunat_scraper import sitemap

BASE = "https://orientacion.sunat.gob.pe"


def make_fetcher(mapping: dict[str, str]):
    """Devuelve un fetcher que sirve fixtures locales."""

    def fetch(url: str) -> bytes | None:
        name = mapping.get(url)
        return fixture_bytes(name) if name else None

    return fetch


def test_sitemap_simple_devuelve_todas_las_urls():
    urls, nested = sitemap.parse_sitemap(fixture_bytes("sitemap_simple.xml"))

    assert nested == []
    assert [u.loc for u in urls] == [
        f"{BASE}/ruc-personas",
        f"{BASE}/comprobantes-de-pago/recibo-por-honorarios",
        f"{BASE}/aduanas/viajeros",
        f"{BASE}/manuales/guia.pdf",
    ]
    assert urls[0].lastmod == "2026-05-10"


def test_sitemap_index_declara_sus_sitemaps_anidados():
    urls, nested = sitemap.parse_sitemap(fixture_bytes("sitemap_index.xml"))

    assert urls == []
    assert nested == [f"{BASE}/sitemap-1.xml", f"{BASE}/sitemap-2.xml"]


def test_discover_recorre_el_indice_y_los_sitemaps_anidados():
    fetch = make_fetcher(
        {
            f"{BASE}/sitemap.xml": "sitemap_index.xml",
            f"{BASE}/sitemap-1.xml": "sitemap_child_1.xml",
            f"{BASE}/sitemap-2.xml": "sitemap_child_2.xml",
            f"{BASE}/sitemap-anidado.xml": "sitemap_anidado.xml",
        }
    )

    found = sitemap.discover(f"{BASE}/sitemap.xml", fetch)
    locs = [u.loc for u in found]

    # Incluye el segundo nivel de anidamiento.
    assert f"{BASE}/gradualidad" in locs
    assert set(locs) == {
        f"{BASE}/ruc-personas",
        f"{BASE}/igv-e-isc",
        f"{BASE}/ruc-empresas",
        f"{BASE}/gradualidad",
    }


def test_discover_no_repite_urls_presentes_en_varios_sitemaps():
    fetch = make_fetcher(
        {
            f"{BASE}/sitemap.xml": "sitemap_index.xml",
            f"{BASE}/sitemap-1.xml": "sitemap_child_1.xml",
            f"{BASE}/sitemap-2.xml": "sitemap_child_2.xml",
            f"{BASE}/sitemap-anidado.xml": "sitemap_anidado.xml",
        }
    )

    locs = [u.loc for u in sitemap.discover(f"{BASE}/sitemap.xml", fetch)]

    assert locs.count(f"{BASE}/ruc-personas") == 1


def test_discover_tolera_un_sitemap_inaccesible():
    fetch = make_fetcher({f"{BASE}/sitemap.xml": "sitemap_index.xml", f"{BASE}/sitemap-1.xml": "sitemap_child_1.xml"})

    locs = [u.loc for u in sitemap.discover(f"{BASE}/sitemap.xml", fetch)]

    assert locs == [f"{BASE}/ruc-personas", f"{BASE}/igv-e-isc"]


def test_rss_se_usa_como_respaldo():
    urls, _ = sitemap.parse_sitemap(fixture_bytes("rss.xml"))

    assert [u.loc for u in urls] == [f"{BASE}/detracciones", f"{BASE}/fraccionamiento"]


def test_xml_invalido_no_lanza_excepcion():
    assert sitemap.parse_sitemap(b"esto no es xml") == ([], [])


def test_rss_fallback_url():
    assert sitemap.rss_fallback_url(f"{BASE}/sitemap.xml") == f"{BASE}/rss.xml"


# -- recorrido de enlaces (fuentes sin sitemap) -----------------------------

PAGINAS = {
    f"{BASE}/": '<a href="/ruc-personas">RUC</a> <a href="/aduanas">Aduanas</a> <a href="/indice">Índice</a>',
    f"{BASE}/indice": '<a href="/gradualidad">Gradualidad</a> <a href="/manual.pdf">PDF</a>',
    f"{BASE}/ruc-personas": '<a href="/">Inicio</a>',
    f"{BASE}/gradualidad": '<a href="/ruc-personas">RUC</a>',
    f"{BASE}/aduanas": '<a href="/aduanas/viajeros">Viajeros</a>',
}


def fetch_pagina(url: str) -> str | None:
    cuerpo = PAGINAS.get(url)
    return f"<html><body>{cuerpo}</body></html>" if cuerpo else None


def test_extract_links_resuelve_rutas_relativas():
    html = '<a href="/uno">1</a><a href="dos">2</a><a href="#x">x</a><a href="mailto:a@b.pe">m</a>'

    links = sitemap.extract_links(html, f"{BASE}/seccion/")

    assert links == [f"{BASE}/uno", f"{BASE}/seccion/dos"]


def test_extract_links_quita_el_fragmento_y_no_repite():
    html = '<a href="/uno#a">a</a><a href="/uno#b">b</a>'

    assert sitemap.extract_links(html, BASE) == [f"{BASE}/uno"]


def test_discover_links_registra_solo_lo_aceptado(config):
    from sunat_scraper.filters import filter_for

    url_filter = filter_for(config, "orientacion")

    found = sitemap.discover_links(
        start_urls=[f"{BASE}/"],
        fetch_html=fetch_pagina,
        accept=lambda u: url_filter.decide(u).accepted,
        follow=url_filter.followable,
    )
    locs = {u.loc for u in found}

    assert f"{BASE}/ruc-personas" in locs      # aceptada por prefijo
    assert f"{BASE}/gradualidad" in locs       # aceptada por patron, a 2 saltos
    assert f"{BASE}/" not in locs              # indice: se visita, no se registra
    assert f"{BASE}/aduanas" not in locs       # excluida por regla
    assert f"{BASE}/manual.pdf" not in locs    # PDF


def test_discover_links_no_recorre_las_ramas_excluidas(config):
    """Lo excluido por una regla se poda: no se visita ni se expande."""
    from sunat_scraper.filters import filter_for

    url_filter = filter_for(config, "orientacion")
    visitadas: list[str] = []

    def fetch(url: str) -> str | None:
        visitadas.append(url)
        return fetch_pagina(url)

    sitemap.discover_links(
        start_urls=[f"{BASE}/"],
        fetch_html=fetch,
        accept=lambda u: url_filter.decide(u).accepted,
        follow=url_filter.followable,
    )

    assert f"{BASE}/aduanas" not in visitadas


def test_discover_links_respeta_el_limite_de_paginas(config):
    from sunat_scraper.filters import filter_for

    url_filter = filter_for(config, "orientacion")
    visitadas: list[str] = []

    def fetch(url: str) -> str | None:
        visitadas.append(url)
        return fetch_pagina(url)

    sitemap.discover_links(
        start_urls=[f"{BASE}/"],
        fetch_html=fetch,
        accept=lambda u: url_filter.decide(u).accepted,
        follow=url_filter.followable,
        max_pages=2,
    )

    assert len(visitadas) <= 2


def test_el_limite_de_paginas_no_descarta_urls_ya_vistas_en_los_enlaces(config):
    """Con presupuesto de UNA descarga, las URLs aceptadas de esa pagina se registran igual.

    Decidir solo necesita la URL: registrar no cuesta una descarga.
    """
    from sunat_scraper.filters import filter_for

    url_filter = filter_for(config, "orientacion")

    found = sitemap.discover_links(
        start_urls=[f"{BASE}/"],
        fetch_html=fetch_pagina,
        accept=lambda u: url_filter.decide(u).accepted,
        follow=url_filter.followable,
        max_pages=1,
    )

    assert f"{BASE}/ruc-personas" in {u.loc for u in found}


def test_discover_links_no_entra_en_bucle(config):
    """Las paginas se enlazan entre si: cada una se visita una sola vez."""
    from sunat_scraper.filters import filter_for

    url_filter = filter_for(config, "orientacion")
    visitadas: list[str] = []

    def fetch(url: str) -> str | None:
        visitadas.append(url)
        return fetch_pagina(url)

    sitemap.discover_links(
        start_urls=[f"{BASE}/"],
        fetch_html=fetch,
        accept=lambda u: url_filter.decide(u).accepted,
        follow=url_filter.followable,
    )

    assert len(visitadas) == len(set(visitadas))


def test_discover_links_respeta_la_profundidad(config):
    from sunat_scraper.filters import filter_for

    url_filter = filter_for(config, "orientacion")

    found = sitemap.discover_links(
        start_urls=[f"{BASE}/"],
        fetch_html=fetch_pagina,
        accept=lambda u: url_filter.decide(u).accepted,
        follow=url_filter.followable,
        max_depth=1,
    )
    locs = {u.loc for u in found}

    assert f"{BASE}/ruc-personas" in locs   # profundidad 1
    assert f"{BASE}/gradualidad" not in locs  # profundidad 2
