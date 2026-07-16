"""Extraccion, limpieza, preguntas frecuentes y motivos de rechazo."""

from __future__ import annotations

from conftest import fixture_text

from sunat_scraper import extractor

URL = "https://orientacion.sunat.gob.pe/ruc-personas/inscripcion"


# -- extraccion basica ------------------------------------------------------

def test_extrae_una_pagina_html():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert result.ok
    assert result.title == "Inscripción en el RUC para personas naturales"
    assert "Registro Único de Contribuyentes" in result.text
    assert len(result.text) > 400


def test_lee_el_breadcrumb_antes_de_limpiarlo():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert "RUC Personas" in result.breadcrumb
    # El breadcrumb sirve para clasificar, pero no forma parte del texto.
    assert "Inicio > RUC Personas" not in result.text


def test_lee_la_fecha_de_actualizacion():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert result.updated_at == "2026-05-10T12:00:00-05:00"


# -- limpieza ---------------------------------------------------------------

def test_una_clase_de_maquetacion_en_el_body_no_borra_la_pagina():
    """Drupal marca el <body> con clases como "layout-one-sidebar".

    Sin proteger los contenedores estructurales, el token "sidebar" eliminaba el
    body completo y la pagina se perdia entera.
    """
    html = fixture_text("pagina_normal.html").replace(
        "<body>", '<body class="layout-one-sidebar layout-sidebar-first path-node">'
    )

    result = extractor.extract(html, url=URL)

    assert result.ok
    assert "Registro Único de Contribuyentes" in result.text


def test_elimina_menus_cabecera_pie_y_bloques_repetidos():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert "MENU ADUANAS" not in result.text
    assert "ENLACE DE CABECERA" not in result.text
    assert "PIE DE PAGINA" not in result.text
    assert "CONTENIDO RELACIONADO" not in result.text
    assert "BLOQUE DE MAS POPULAR" not in result.text
    assert "AVISO DE COOKIES" not in result.text
    assert "script de analitica" not in result.text


def test_conserva_los_pasos_numerados():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert "1. Reúne los documentos requeridos" in result.text
    assert "5. Confirma la información" in result.text


def test_convierte_una_tabla_simple_a_texto():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert "Régimen | Comprobantes permitidos" in result.text
    assert "Nuevo RUS | Boletas de venta" in result.text


def test_marca_los_encabezados():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert "## Pasos para inscribirte en el RUC" in result.text


# -- numeracion de listas ---------------------------------------------------

def test_respeta_la_numeracion_declarada_con_start():
    """La pagina muestra "2. Presencialmente": inventar un "1." seria alterarla."""
    result = extractor.extract(fixture_text("pagina_pasos.html"), url=URL)

    assert result.ok
    assert "1. Virtual:" in result.text
    assert "2. Presencialmente:" in result.text
    assert "1. Presencialmente:" not in result.text


def test_respeta_el_atributo_value_de_un_elemento():
    result = extractor.extract(fixture_text("pagina_pasos.html"), url=URL)

    assert "3. Tercera categoría" in result.text
    assert "7. Séptima categoría" in result.text
    # La numeracion continua desde el value declarado.
    assert "8. Octava categoría" in result.text


def test_no_duplica_los_items_de_lista_que_llevan_parrafo_dentro():
    """Marcado real de SUNAT: <ul><li><p>texto</p></li></ul>.

    El item no debe salir dos veces (una como lista y otra como parrafo suelto).
    """
    html = """<html><body><main><h1>Inscripción en el RUC</h1>
    <p>Si deseas generar tu RUC para realizar actividades economicas de manera habitual,
    revisa las siguientes opciones disponibles para tu caso particular y elige la que
    corresponda a la actividad que vas a desarrollar en el pais.</p>
    <ul><li><p>Prestar servicios de manera independiente como trabajador.</p></li></ul>
    <ul><li><p>Alquilar un bien inmueble de tu propiedad a un tercero.</p></li></ul>
    <p>Como persona natural puedes hacerlo desde la aplicación móvil siguiendo los pasos
    que el sistema te vaya indicando durante todo el procedimiento de inscripción.</p>
    </main></body></html>"""

    result = extractor.extract(html, url=URL)

    assert result.ok
    assert result.text.count("Prestar servicios de manera independiente") == 1
    assert result.text.count("Alquilar un bien inmueble") == 1
    assert "- Prestar servicios de manera independiente" in result.text


def test_no_duplica_el_contenido_de_una_tabla():
    html = """<html><body><main><h1>Regímenes</h1>
    <p>La elección del régimen tributario depende del nivel de ingresos proyectado y del
    tipo de comprobante de pago que necesites emitir a tus clientes de manera habitual.
    Cada régimen establece sus propias obligaciones de declaración y de pago mensual.</p>
    <table>
      <tr><th><p>Régimen</p></th><th><p>Comprobantes</p></th></tr>
      <tr><td><p>Nuevo RUS</p></td><td><p>Boletas de venta</p></td></tr>
    </table>
    <p>Al inscribirte deberás elegir el régimen tributario al que te vas a acoger según
    las condiciones establecidas para cada uno de ellos en la normativa vigente. La
    elección se puede modificar posteriormente en los plazos que establece la norma,
    siempre que se cumplan los requisitos exigidos para el cambio de régimen.</p>
    </main></body></html>"""

    result = extractor.extract(html, url=URL)

    assert result.ok
    assert result.text.count("Nuevo RUS") == 1


# -- preguntas frecuentes ---------------------------------------------------

def test_extrae_las_faqs_reales_de_la_pagina():
    result = extractor.extract(fixture_text("pagina_faq.html"), url=URL)

    assert result.ok
    preguntas = [faq.question for faq in result.faqs]
    assert "¿Cómo actualizo mi domicilio fiscal?" in preguntas          # acordeon
    assert "¿Cuánto cuesta inscribirse en el RUC?" in preguntas         # acordeon
    assert "¿Puedo darme de baja del RUC si dejo de operar?" in preguntas  # details/summary
    assert "¿Qué documentos necesito para inscribirme?" in preguntas    # encabezado


def test_la_respuesta_acompana_a_su_pregunta():
    result = extractor.extract(fixture_text("pagina_faq.html"), url=URL)

    faq = next(f for f in result.faqs if f.question == "¿Cómo actualizo mi domicilio fiscal?")

    assert "Para actualizar el domicilio fiscal" in faq.answer
    assert "recibo de servicio público" in faq.answer  # incluye la lista de la respuesta
    # No arrastra la respuesta de la siguiente pregunta.
    assert "completamente gratuita" not in faq.answer


def test_el_texto_usa_el_formato_pregunta_respuesta():
    result = extractor.extract(fixture_text("pagina_faq.html"), url=URL)

    assert "Pregunta: ¿Cómo actualizo mi domicilio fiscal?" in result.text
    assert "Respuesta: Para actualizar el domicilio fiscal" in result.text


def test_no_inventa_faqs_en_una_pagina_sin_preguntas():
    result = extractor.extract(fixture_text("pagina_normal.html"), url=URL)

    assert result.faqs == []


def test_una_pagina_de_faq_no_duplica_el_contenido():
    result = extractor.extract(fixture_text("pagina_faq.html"), url=URL)

    assert result.text.count("Para actualizar el domicilio fiscal") == 1


# -- rechazos ---------------------------------------------------------------

def test_rechaza_una_pagina_que_solo_enlaza_pdfs():
    result = extractor.extract(fixture_text("pagina_solo_pdf.html"), url=URL)

    assert not result.ok
    assert result.reason == "external_document_only"


def test_rechaza_una_pagina_con_poco_texto():
    result = extractor.extract(fixture_text("pagina_corta.html"), url=URL)

    assert not result.ok
    assert result.reason == "insufficient_text"


def test_rechaza_una_pagina_cuya_tabla_principal_es_compleja():
    result = extractor.extract(fixture_text("pagina_tabla_compleja.html"), url=URL)

    assert not result.ok
    assert result.reason == "complex_table"


def test_rechaza_html_vacio():
    assert not extractor.extract("", url=URL).ok


def test_rechaza_un_captcha():
    html = """<html><body><main><h1>Verificación</h1>
    <div class="g-recaptcha" data-sitekey="abc"></div>
    <p>Confirme que no es un robot.</p></main></body></html>"""

    result = extractor.extract(html, url=URL)

    assert not result.ok
    assert result.reason == "captcha"


def test_rechaza_una_pantalla_de_login():
    html = """<html><body><main><h1>Iniciar sesión</h1>
    <form><input type="text" name="usuario"><input type="password" name="clave"></form>
    </main></body></html>"""

    result = extractor.extract(html, url=URL)

    assert not result.ok
    assert result.reason == "login"


def test_rechaza_un_error_del_servidor():
    html = "<html><body><main><h1>Error 500</h1><p>Internal Server Error</p></main></body></html>"

    result = extractor.extract(html, url=URL)

    assert not result.ok
    assert result.reason == "extraction_error"


def test_rechaza_una_pagina_hecha_solo_de_enlaces():
    enlaces = "".join(f'<li><a href="/s{i}">Sección número {i} del portal institucional</a></li>' for i in range(40))
    html = f"<html><body><main><h1>Índice</h1><ul>{enlaces}</ul></main></body></html>"

    result = extractor.extract(html, url=URL)

    assert not result.ok
    assert result.reason == "insufficient_text"


def test_rechaza_un_visor_incrustado_sin_texto():
    html = """<html><body><main><h1>Documento</h1>
    <iframe src="/visor?doc=1"></iframe><p>Vea el documento.</p></main></body></html>"""

    result = extractor.extract(html, url=URL)

    assert not result.ok
    assert result.reason == "external_document_only"


# -- fidelidad --------------------------------------------------------------

def test_el_texto_no_agrega_palabras_que_no_estan_en_la_pagina():
    html_text = fixture_text("pagina_normal.html")
    result = extractor.extract(html_text, url=URL)

    for line in result.text.splitlines():
        contenido = line.lstrip("#- ").split(". ", 1)[-1]
        if len(contenido) > 30 and " | " not in contenido:
            # Cada frase extraida debe existir en el HTML original.
            assert contenido.split(".")[0][:40] in html_text.replace("\n    ", " ").replace("\n", " ")
