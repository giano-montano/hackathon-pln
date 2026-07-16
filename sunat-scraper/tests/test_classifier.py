"""Clasificacion de audiencia y tema mediante reglas (sin LLM)."""

from __future__ import annotations

import pytest

from sunat_scraper.classifier import classify_audience, classify_topic, normalize

BASE = "https://orientacion.sunat.gob.pe"
EMPRENDER = "https://emprender.sunat.gob.pe"


def test_normalize_quita_tildes_y_mayusculas():
    assert normalize("Inscripción en el RUC") == "inscripcion en el ruc"


def test_los_patrones_coinciden_al_inicio_de_palabra(classification, orientacion):
    """`isc` no debe activar el tema `igv` dentro de `discapacidad`."""
    assert classify_topic(f"{BASE}/discapacidad", "Discapacidad", "", classification, orientacion) != "igv"
    assert classify_topic(f"{BASE}/igv-e-isc", "ISC", "", classification, orientacion) == "igv"


def test_los_patrones_toleran_el_plural(classification, orientacion):
    """`comprobante` debe coincidir en `comprobantes`."""
    tema = classify_topic(f"{BASE}/comprobantes-de-pago", "Comprobantes de pago", "", classification, orientacion)

    assert tema == "comprobantes"


# -- audiencia --------------------------------------------------------------

@pytest.mark.parametrize(
    "url, titulo, esperado",
    [
        (f"{BASE}/ruc-personas", "Registro Único de Contribuyentes", "personas"),
        (f"{BASE}/ruc-empresas", "RUC para empresas", "empresas"),
        (f"{BASE}/declaracion-y-pago-empresas", "Declaración", "empresas"),
        (f"{BASE}/impuesto-a-la-renta-personas", "Renta", "personas"),
        (f"{EMPRENDER}/ruc/inscripcion", "Inscríbete en el RUC", "emprendedor"),
    ],
)
def test_clasifica_la_audiencia_por_url(classification, config, url, titulo, esperado):
    source = config.source("emprender" if "emprender" in url else "orientacion")

    assert classify_audience(url, titulo, "", classification, source) == esperado


def test_clasifica_la_audiencia_por_titulo(classification, orientacion):
    audiencia = classify_audience(f"{BASE}/una-guia", "Guía para personas naturales", "", classification, orientacion)

    assert audiencia == "personas"


def test_clasifica_la_audiencia_por_breadcrumb(classification, orientacion):
    audiencia = classify_audience(f"{BASE}/una-guia", "Guía", "Inicio > Empresas > IGV", classification, orientacion)

    assert audiencia == "empresas"


def test_usa_el_dominio_cuando_no_hay_otra_senal(classification, emprender):
    assert classify_audience(f"{EMPRENDER}/algo", "Algo", "", classification, emprender) == "emprendedor"


def test_cae_en_la_audiencia_por_defecto_de_la_fuente(classification, orientacion):
    assert classify_audience(f"{BASE}/algo-generico", "Algo", "", classification, orientacion) == "todas"


# -- tema -------------------------------------------------------------------

@pytest.mark.parametrize(
    "url, titulo, esperado",
    [
        (f"{BASE}/ruc-personas", "Inscripción en el RUC", "ruc"),
        (f"{BASE}/02-obtencion-de-clave-sol", "Clave SOL", "clave_sol"),
        (f"{BASE}/comprobantes-de-pago/factura", "Factura electrónica", "comprobantes"),
        (f"{BASE}/comprobantes-de-pago/recibo-por-honorarios", "Recibo por honorarios", "recibos_honorarios"),
        (f"{BASE}/igv-e-isc", "Impuesto General a las Ventas", "igv"),
        (f"{BASE}/impuesto-a-la-renta-personas", "Impuesto a la Renta", "impuesto_renta"),
        (f"{BASE}/multa-por-no-declarar", "Multa por no presentar la declaración", "multas"),
        (f"{BASE}/gradualidad", "Régimen de gradualidad", "gradualidad"),
        (f"{BASE}/fraccionamiento", "Fraccionamiento tributario", "fraccionamiento"),
        (f"{BASE}/devoluciones", "Devoluciones", "devoluciones"),
        (f"{BASE}/cobranza", "Cobranza coactiva", "cobranza"),
        (f"{BASE}/detracciones", "Detracciones", "detracciones"),
        (f"{BASE}/retenciones", "Régimen de retenciones", "retenciones"),
        (f"{BASE}/percepciones", "Régimen de percepciones", "percepciones"),
    ],
)
def test_clasifica_el_tema(classification, orientacion, url, titulo, esperado):
    assert classify_topic(url, titulo, "", classification, orientacion) == esperado


def test_el_tema_mas_especifico_gana_sobre_el_generico(classification, orientacion):
    """Un recibo por honorarios es un comprobante, pero el tema preciso manda."""
    tema = classify_topic(
        f"{BASE}/comprobantes-de-pago/recibo-por-honorarios",
        "Recibo por honorarios electrónico",
        "Inicio > Comprobantes de pago",
        classification,
        orientacion,
    )

    assert tema == "recibos_honorarios"


def test_la_url_pesa_mas_que_el_titulo(classification, orientacion):
    tema = classify_topic(f"{BASE}/detracciones", "Sistema aplicable a la venta", "", classification, orientacion)

    assert tema == "detracciones"


def test_cae_en_el_tema_por_defecto_de_la_fuente(classification, emprender):
    assert classify_topic(f"{EMPRENDER}/algo-generico", "Algo", "", classification, emprender) == "formalizacion"


def test_tema_otros_sin_fuente(classification):
    assert classify_topic(f"{BASE}/algo-generico", "Algo", "", classification) == "otros"
