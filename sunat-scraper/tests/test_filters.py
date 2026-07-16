"""Filtrado de URLs: orden de las reglas, exclusiones y formatos no HTML."""

from __future__ import annotations

import pytest

from sunat_scraper.filters import UrlFilter, canonicalize, filter_for

BASE = "https://orientacion.sunat.gob.pe"
EMPRENDER = "https://emprender.sunat.gob.pe"


@pytest.fixture
def url_filter(config):
    return filter_for(config, "orientacion")


# -- canonicalizacion -------------------------------------------------------

@pytest.mark.parametrize(
    "entrada, esperado",
    [
        (f"{BASE}/ruc-personas/", f"{BASE}/ruc-personas"),
        (f"{BASE}/ruc-personas#seccion", f"{BASE}/ruc-personas"),
        ("HTTPS://Orientacion.SUNAT.gob.PE/ruc-personas", f"{BASE}/ruc-personas"),
        (f"{BASE}/ruc-personas?utm_source=google&utm_campaign=x", f"{BASE}/ruc-personas"),
        (f"{BASE}/ruc-personas?id=2&a=1", f"{BASE}/ruc-personas?a=1&id=2"),
    ],
)
def test_canonicalize(entrada, esperado):
    assert canonicalize(entrada) == esperado


# -- inclusiones ------------------------------------------------------------

def test_incluye_por_prefijo(url_filter):
    result = url_filter.decide(f"{BASE}/ruc-personas/inscripcion")

    assert result.accepted
    assert result.rule == "include_prefix:/ruc-personas"


def test_incluye_por_url_exacta(url_filter):
    result = url_filter.decide(f"{BASE}/02-obtencion-de-clave-sol")

    assert result.accepted
    assert result.rule == "include_url:/02-obtencion-de-clave-sol"


def test_incluye_por_patron(url_filter):
    result = url_filter.decide(f"{BASE}/otra-seccion/preguntas-frecuentes-varias")

    assert result.accepted
    assert result.rule == "include_pattern:preguntas-frecuentes"


def test_url_exacta_ignora_la_barra_final(url_filter):
    assert url_filter.decide(f"{BASE}/04-manuales/").accepted


# -- exclusiones ------------------------------------------------------------

def test_excluye_por_patron_de_la_fuente(url_filter):
    result = url_filter.decide(f"{BASE}/aduanas/viajeros")

    assert not result.accepted
    assert result.rule == "exclude_pattern:aduana"
    assert result.reason == "excluded_by_rule"


def test_excluye_por_patron_global(url_filter):
    result = url_filter.decide(f"{BASE}/ruc-personas/consulta-ruc")

    assert not result.accepted
    assert result.reason == "excluded_by_rule"
    assert result.rule.startswith("global_exclude:")


def test_la_exclusion_gana_sobre_la_inclusion(url_filter):
    """`/ruc-personas` esta incluido por prefijo, pero `aduana` esta excluido."""
    result = url_filter.decide(f"{BASE}/ruc-personas/aduanas")

    assert not result.accepted
    assert result.rule == "exclude_pattern:aduana"


def test_rechazo_por_defecto(url_filter):
    result = url_filter.decide(f"{BASE}/una-seccion-cualquiera")

    assert not result.accepted
    assert result.rule == "default_reject"
    assert result.reason == "excluded_by_rule"


def test_rechaza_otro_dominio(url_filter):
    result = url_filter.decide("https://www.google.com/ruc-personas")

    assert not result.accepted
    assert result.rule.startswith("wrong_domain:")


def test_rechaza_zona_autenticada(url_filter):
    result = url_filter.decide(f"{BASE}/user/login")

    assert not result.accepted
    assert result.reason == "login"


# -- formatos ---------------------------------------------------------------

def test_rechaza_pdf(url_filter):
    result = url_filter.decide(f"{BASE}/ruc-personas/manual.pdf")

    assert not result.accepted
    assert result.reason == "pdf"


def test_el_pdf_se_rechaza_aunque_coincida_con_una_inclusion(url_filter):
    """El formato se evalua antes que cualquier regla de inclusion."""
    assert not url_filter.decide(f"{BASE}/04-manuales.pdf").accepted


@pytest.mark.parametrize("ruta", ["/ruc-personas/f.docx", "/ruc-personas/f.xlsx", "/ruc-personas/img.jpg",
                                  "/ruc-personas/v.mp4", "/ruc-personas/d.zip"])
def test_rechaza_formatos_no_html(url_filter, ruta):
    result = url_filter.decide(BASE + ruta)

    assert not result.accepted
    assert result.reason == "non_html"


# -- otra fuente ------------------------------------------------------------

def test_las_reglas_son_por_fuente(config):
    emprender_filter = filter_for(config, "emprender")

    assert emprender_filter.decide(f"{EMPRENDER}/ruc/inscripcion").accepted
    assert not emprender_filter.decide(f"{EMPRENDER}/videos/tutorial").accepted
    # Una URL de emprender no pasa el filtro de orientacion: distinto dominio.
    assert not filter_for(config, "orientacion").decide(f"{EMPRENDER}/ruc/inscripcion").accepted


def test_una_fuente_nueva_no_requiere_tocar_el_codigo():
    from sunat_scraper.config import Source

    nueva = Source(name="prueba", domain="ejemplo.pe", include_prefixes=["/guias"])
    url_filter = UrlFilter(nueva, global_exclude_patterns=["/login"])

    assert url_filter.decide("https://ejemplo.pe/guias/uno").accepted
    assert not url_filter.decide("https://ejemplo.pe/otros").accepted
