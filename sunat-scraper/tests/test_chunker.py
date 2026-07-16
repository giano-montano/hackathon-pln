"""Fragmentacion para RAG: tamanos, solape y bloques que no se pueden partir."""

from __future__ import annotations

from sunat_scraper.chunker import chunk_document, chunk_text

PARRAFO = (
    "El contribuyente debe presentar la declaración mensual dentro del plazo establecido "
    "según el último dígito de su número de registro, considerando el cronograma vigente "
    "y las obligaciones propias del régimen al que se encuentra acogido en cada periodo. "
)


def parrafos(n: int) -> str:
    return "\n\n".join(f"{PARRAFO} Párrafo número {i}." for i in range(n))


def test_texto_vacio_no_genera_fragmentos():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


def test_un_texto_corto_produce_un_solo_fragmento():
    text = parrafos(3)

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert "Párrafo número 0." in chunks[0]


def test_respeta_el_maximo_de_palabras():
    chunks = chunk_text(parrafos(40), min_words=250, max_words=450)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.split()) <= 450


def test_los_fragmentos_intermedios_alcanzan_el_minimo():
    chunks = chunk_text(parrafos(40), min_words=250, max_words=450)

    for chunk in chunks[:-1]:
        assert len(chunk.split()) >= 250


def test_no_separa_una_pregunta_de_su_respuesta():
    bloques = []
    for i in range(12):
        bloques.append(
            f"Pregunta: ¿Cómo realizo el trámite número {i}?\n\n"
            f"Respuesta: {PARRAFO} Debe seguir el procedimiento indicado para el caso {i}."
        )
    text = "\n\n".join(bloques)

    chunks = chunk_text(text, min_words=250, max_words=450)

    assert len(chunks) > 1
    for chunk in chunks:
        # Cada "Pregunta:" del fragmento conserva su "Respuesta:".
        assert chunk.count("Pregunta:") == chunk.count("Respuesta:")
    # Ningún fragmento empieza por una respuesta huérfana.
    for chunk in chunks:
        assert not chunk.lstrip().startswith("Respuesta:")


def test_no_corta_una_lista_de_pasos():
    pasos = "\n".join(f"{i}. Paso número {i} del procedimiento de inscripción en el registro." for i in range(1, 31))
    text = parrafos(6) + "\n\n" + pasos + "\n\n" + parrafos(6)

    chunks = chunk_text(text, min_words=250, max_words=450)

    # La lista aparece completa dentro de un unico fragmento.
    con_pasos = [c for c in chunks if "1. Paso número 1" in c]
    assert len(con_pasos) == 1
    assert "30. Paso número 30" in con_pasos[0]


def test_no_corta_una_lista_con_vinetas():
    items = "\n".join(f"- Documento requerido número {i} para completar el trámite." for i in range(1, 41))
    text = parrafos(5) + "\n\n" + items + "\n\n" + parrafos(5)

    chunks = chunk_text(text, min_words=250, max_words=450)

    con_lista = [c for c in chunks if "- Documento requerido número 1 " in c]
    assert len(con_lista) == 1
    assert "- Documento requerido número 40" in con_lista[0]


def test_prefiere_cortar_en_un_encabezado():
    text = ""
    for i in range(6):
        text += f"## Sección número {i}\n\n" + parrafos(4) + "\n\n"

    chunks = chunk_text(text.strip(), min_words=250, max_words=450)

    assert len(chunks) > 1
    # La mayoria de los cortes cae en un encabezado.
    inician_en_encabezado = sum(1 for c in chunks if c.lstrip().startswith("## "))
    assert inician_en_encabezado >= len(chunks) - 1


def test_el_solape_no_supera_el_maximo():
    chunks = chunk_text(parrafos(40), min_words=250, max_words=450, overlap_words=40)

    for anterior, siguiente in zip(chunks, chunks[1:]):
        bloques_anterior = anterior.split("\n\n")
        bloques_siguiente = siguiente.split("\n\n")
        comunes = [b for b in bloques_siguiente if b in bloques_anterior]
        assert sum(len(b.split()) for b in comunes) <= 40


def test_sin_solape_cuando_es_cero():
    chunks = chunk_text(parrafos(40), min_words=250, max_words=450, overlap_words=0)

    for anterior, siguiente in zip(chunks, chunks[1:]):
        bloques_anterior = set(anterior.split("\n\n"))
        assert not [b for b in siguiente.split("\n\n") if b in bloques_anterior]


def test_un_bloque_indivisible_puede_exceder_el_maximo():
    """Una lista de 500 palabras no se corta: manda la regla de no partir pasos."""
    pasos = "\n".join(f"{i}. Paso número {i} del procedimiento de inscripción en el registro único." for i in range(1, 61))

    chunks = chunk_text(pasos, min_words=250, max_words=450)

    assert len(chunks) == 1
    assert "1. Paso número 1 " in chunks[0]
    assert "60. Paso número 60" in chunks[0]


def test_una_cola_minuscula_no_genera_un_fragmento_suelto():
    """Tras un bloque indivisible enorme, la ultima linea se arrastra al anterior."""
    pasos = "\n".join(f"{i}. Paso número {i} del procedimiento de inscripción en el registro único." for i in range(1, 61))
    text = pasos + "\n\nRegulado por Resolución de Superintendencia."

    chunks = chunk_text(text, min_words=250, max_words=450)

    assert all(len(c.split()) > 10 for c in chunks)
    assert "Regulado por Resolución de Superintendencia." in chunks[-1]


def test_un_parrafo_larguisimo_se_parte_por_frases():
    text = PARRAFO * 60

    chunks = chunk_text(text, min_words=250, max_words=450)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.split()) <= 450


def test_no_mezcla_contenido_de_paginas_distintas():
    """La fragmentacion es por documento: cada llamada solo ve un texto."""
    a = chunk_document("doc-a", parrafos(20) + "\n\nContenido exclusivo de la página A.")
    b = chunk_document("doc-b", parrafos(20) + "\n\nContenido exclusivo de la página B.")

    assert all(chunk_id.startswith("doc-a-") for chunk_id, _ in a)
    assert all(chunk_id.startswith("doc-b-") for chunk_id, _ in b)
    assert not any("página B" in text for _, text in a)
    assert not any("página A" in text for _, text in b)


def test_los_ids_son_correlativos():
    chunks = chunk_document("orientacion-ruc-personas", parrafos(40))

    assert chunks[0][0] == "orientacion-ruc-personas-001"
    assert chunks[1][0] == "orientacion-ruc-personas-002"


def test_conserva_las_tablas_en_un_solo_fragmento():
    tabla = "Régimen | Comprobantes\nNuevo RUS | Boletas\nRER | Facturas y boletas"
    text = parrafos(10) + "\n\n" + tabla + "\n\n" + parrafos(10)

    chunks = chunk_text(text, min_words=250, max_words=450)

    con_tabla = [c for c in chunks if "Nuevo RUS | Boletas" in c]
    assert len(con_tabla) == 1
    assert "Régimen | Comprobantes" in con_tabla[0]
