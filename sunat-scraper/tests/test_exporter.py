"""Ids de documento, deduplicacion y escritura de JSONL."""

from __future__ import annotations

import json

from sunat_scraper.exporter import (
    Deduplicator,
    Document,
    JsonlWriter,
    make_document_id,
    now_lima,
    read_jsonl,
    text_hash,
)

BASE = "https://orientacion.sunat.gob.pe"


# -- ids --------------------------------------------------------------------

def test_id_del_documento():
    assert make_document_id("orientacion", f"{BASE}/ruc-personas") == "orientacion-ruc-personas"


def test_el_id_ignora_tildes_y_barras():
    assert make_document_id("emprender", "https://emprender.sunat.gob.pe/ruc/inscripción/") == "emprender-ruc-inscripcion"


def test_el_id_de_la_raiz_no_queda_vacio():
    assert make_document_id("orientacion", BASE) == "orientacion-inicio"


# -- deduplicacion ----------------------------------------------------------

def test_deduplica_por_url_canonica():
    dedup = Deduplicator()

    assert not dedup.seen_url(f"{BASE}/ruc-personas")
    assert dedup.seen_url(f"{BASE}/ruc-personas/")
    assert dedup.seen_url(f"{BASE}/ruc-personas#seccion")
    assert dedup.seen_url(f"{BASE}/ruc-personas?utm_source=x")


def test_deduplica_por_hash_exacto_del_texto():
    dedup = Deduplicator()
    texto = "El Registro Único de Contribuyentes es el padrón de la administración tributaria."

    assert dedup.seen_text(texto, "doc-1") is None
    # El mismo texto en otra URL: se reporta el documento original.
    assert dedup.seen_text(texto, "doc-2") == "doc-1"


def test_el_hash_ignora_espacios_al_borde_pero_no_el_contenido():
    dedup = Deduplicator()

    assert dedup.seen_text("  mismo texto  ", "doc-1") is None
    assert dedup.seen_text("mismo texto", "doc-2") == "doc-1"
    assert dedup.seen_text("texto distinto", "doc-3") is None


def test_textos_distintos_no_se_deduplican():
    dedup = Deduplicator()

    assert dedup.seen_text("texto uno", "doc-1") is None
    assert dedup.seen_text("texto dos", "doc-2") is None


def test_text_hash_es_estable():
    assert text_hash("hola") == text_hash("hola")
    assert text_hash("hola") != text_hash("chau")


def test_los_ids_repetidos_reciben_un_sufijo():
    dedup = Deduplicator()

    primero = dedup.unique_id("orientacion-ruc", f"{BASE}/a")
    segundo = dedup.unique_id("orientacion-ruc", f"{BASE}/b")

    assert primero == "orientacion-ruc"
    assert segundo.startswith("orientacion-ruc-")
    assert segundo != primero


# -- escritura --------------------------------------------------------------

def test_jsonl_omite_los_campos_opcionales_vacios(tmp_path):
    documento = Document(
        id="orientacion-ruc-personas",
        url=f"{BASE}/ruc-personas",
        title="Registro Único de Contribuyentes",
        audience="personas",
        topic="ruc",
        text="Texto limpio.",
        collected_at=now_lima(),
    )
    destino = tmp_path / "documents.jsonl"

    with JsonlWriter(destino) as writer:
        writer.write(documento)

    registro = json.loads(destino.read_text(encoding="utf-8").strip())
    assert list(registro) == ["id", "url", "title", "audience", "topic", "text", "collected_at"]
    assert "Único" in registro["title"]  # sin escapes unicode


def test_read_jsonl_de_ida_y_vuelta(tmp_path):
    destino = tmp_path / "datos.jsonl"

    with JsonlWriter(destino) as writer:
        writer.write({"url": f"{BASE}/a", "reason": "pdf"})
        writer.write({"url": f"{BASE}/b", "reason": "insufficient_text"})

    assert read_jsonl(destino) == [
        {"url": f"{BASE}/a", "reason": "pdf"},
        {"url": f"{BASE}/b", "reason": "insufficient_text"},
    ]


def test_read_jsonl_de_un_archivo_inexistente(tmp_path):
    assert read_jsonl(tmp_path / "no-existe.jsonl") == []


def test_collected_at_usa_la_hora_de_lima():
    assert now_lima().endswith("-05:00")
