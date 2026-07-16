"""Extraccion del contenido principal de una pagina HTML.

Principio rector: FIDELIDAD. No se inventa texto, no se completa lo cortado,
no se resume y no se reformula. Si el contenido no se puede recuperar con
claridad, la pagina se rechaza con un motivo.

Estrategia:
  - trafilatura (extractor principal) detecta el contenido y devuelve XML
    estructurado; aqui solo se renderiza ese XML a texto.
  - BeautifulSoup es el respaldo cuando trafilatura no devuelve suficiente,
    y es tambien el renderizador de las paginas de preguntas frecuentes
    (los acordeones suelen confundir a los extractores automaticos).

Convenciones del texto de salida (texto plano con marcas minimas):
  - Encabezado:            "## Titulo de seccion"
  - Elemento de lista:     "- item"     /  "1. paso"
  - Fila de tabla:         "col a | col b"
  - Pregunta frecuente:    "Pregunta: ...\\n\\nRespuesta: ..."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import trafilatura
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from lxml import etree

# Motivos de rechazo (se escriben tal cual en rejected.jsonl).
REASON_INSUFFICIENT = "insufficient_text"
REASON_EXTERNAL_DOC = "external_document_only"
REASON_COMPLEX_TABLE = "complex_table"
REASON_CAPTCHA = "captcha"
REASON_LOGIN = "login"
REASON_EXTRACTION = "extraction_error"

# Etiquetas que nunca aportan contenido. <button> NO se elimina: los acordeones
# de preguntas frecuentes suelen poner la pregunta dentro de un boton.
DROP_TAGS = ["script", "style", "noscript", "template", "svg", "form", "input", "select", "textarea"]

# Estructura del sitio. Ojo: no se filtra por [aria-hidden=true] porque los
# acordeones cerrados marcan asi su respuesta.
DROP_SELECTORS = [
    "nav", "header", "footer", "aside",
    "[role=navigation]", "[role=banner]", "[role=contentinfo]", "[role=search]", "[role=dialog]",
]

# Clases/ids de ruido. Se comparan token por token para no borrar por accidente
# contenedores legitimos como "card-header" o "accordion-header".
NOISE_EXACT_CLASSES = {"header", "footer", "search", "tabs", "aside", "menu", "nav"}
NOISE_CLASS_TOKENS = (
    "navbar", "navigation", "megamenu", "main-menu", "block-menu", "menu-item", "menu-principal",
    "breadcrumb", "migas", "site-header", "page-header", "region-header", "global-header",
    "site-footer", "page-footer", "region-footer", "global-footer",
    "banner", "carousel", "slider", "modal", "popup", "overlay", "cookie", "consent",
    "social", "compartir", "redes-sociales", "sidebar", "related", "relacionad",
    "popular", "mas-visitado", "mas-populares", "destacado", "toolbar", "pager", "pagination",
    "skip-link", "back-to-top", "volver-arriba", "buscador", "search-form", "searchform",
    "login", "feedback", "encuesta", "calificanos",
)

# Contenedores estructurales: jamas se eliminan por su class/id. Drupal marca el
# <body> con clases de maquetacion como "layout-one-sidebar", y sin esta guarda
# el token "sidebar" borraria la pagina entera.
PROTECTED_TAGS = {"[document]", "html", "body", "main", "article"}

BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "dl", "table", "blockquote", "pre"]
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
MEDIA_TAGS = {"img", "figure", "picture", "video", "audio", "iframe", "canvas", "object", "embed", "map"}
QUESTION_HOST_TAGS = ["h2", "h3", "h4", "h5", "h6", "summary", "button", "a", "span", "strong"]

# Etiquetas de la salida XML de trafilatura: bloques que se emiten, y
# contenedores cuyo interior ya queda incluido al emitirlos.
TRAFILATURA_BLOCKS = {"head", "p", "quote", "list", "table"}
TRAFILATURA_CONTAINERS = {"list", "table", "quote"}

ACCORDION_RE = re.compile(r"accordion|acordeon|collaps|panel|faq|pregunta|toggle", re.I)
PDF_HREF_RE = re.compile(r"\.pdf($|\?|#)", re.I)
ORDERED_ITEM_RE = re.compile(r"^\d+\.\s")
PASSWORD_INPUT_RE = re.compile(r"""type\s*=\s*["']?password""", re.I)

CAPTCHA_MARKERS = ("g-recaptcha", "h-captcha", "hcaptcha", "recaptcha", "captcha")
SERVER_ERROR_MARKERS = (
    "internal server error", "error 500", "503 service", "service unavailable",
    "servicio no disponible", "servicio temporalmente no disponible", "error del servidor",
)


@dataclass(frozen=True)
class Faq:
    question: str
    answer: str


@dataclass
class ExtractResult:
    ok: bool
    title: str = ""
    text: str = ""
    breadcrumb: str = ""
    updated_at: str | None = None
    faqs: list[Faq] = field(default_factory=list)
    reason: str | None = None


# --------------------------------------------------------------------------
# Utilidades de texto
# --------------------------------------------------------------------------

def clean_text(value: str) -> str:
    """Normaliza espacios sin alterar las palabras.

    Se aplica a cada nodo por separado, asi que colapsar los saltos de linea del
    HTML es seguro: la estructura del texto la ponen los renderizadores al unir
    los bloques, no los saltos del codigo fuente.
    """
    value = value.replace("\xa0", " ").replace("​", "").replace("\xad", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _join_blocks(blocks: list[str]) -> str:
    return "\n\n".join(b for b in (block.strip() for block in blocks) if b)


# --------------------------------------------------------------------------
# Limpieza del DOM
# --------------------------------------------------------------------------

def _is_noise_value(value) -> bool:
    """True si algun token de class/id corresponde a ruido de plantilla."""
    if not value:
        return False
    tokens = value if isinstance(value, list) else str(value).split()
    for token in tokens:
        token = token.lower()
        if token in NOISE_EXACT_CLASSES:
            return True
        if any(noise in token for noise in NOISE_CLASS_TOKENS):
            return True
    return False


def _drop_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for selector in DROP_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()
    for attr in ("class", "id"):
        for tag in soup.find_all(attrs={attr: _is_noise_value}):
            if tag.name not in PROTECTED_TAGS:
                tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()


def _find_main(soup: BeautifulSoup) -> Tag:
    """Contenedor del contenido principal, con respaldos habituales de Drupal."""
    for selector in ("main", "[role=main]", "article", "#content", "#main-content",
                     ".region-content", ".node__content", ".field--name-body", "#block-system-main"):
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    return soup.body or soup


def _breadcrumb(soup: BeautifulSoup) -> str:
    """Se lee ANTES de limpiar: sirve para clasificar audiencia y tema."""
    for selector in ("nav.breadcrumb", ".breadcrumb", "[aria-label=breadcrumb]", ".migas", "#breadcrumb"):
        node = soup.select_one(selector)
        if node:
            parts = [clean_text(x) for x in node.stripped_strings]
            joined = " > ".join(p for p in parts if p and p not in {">", "/", "|", "»"})
            if joined:
                return joined
    return ""


def _title(soup: BeautifulSoup, main: Tag) -> str:
    h1 = main.find("h1") or soup.find("h1")
    if h1:
        text = clean_text(h1.get_text(" ", strip=True))
        if text:
            return text
    if soup.title and soup.title.string:
        return clean_text(soup.title.string.split("|")[0])  # "Pagina | SUNAT" -> "Pagina"
    return ""


def _updated_at(soup: BeautifulSoup) -> str | None:
    for attrs in ({"property": "article:modified_time"}, {"name": "last-modified"}, {"itemprop": "dateModified"}):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            return clean_text(meta["content"])
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        return clean_text(time_tag["datetime"])
    return None


# --------------------------------------------------------------------------
# Tablas
# --------------------------------------------------------------------------

def _is_complex_table(table: Tag) -> bool:
    """Tabla que no se puede pasar a texto sin perder o inventar informacion."""
    if table.find("table"):  # anidada
        return True
    for cell in table.find_all(["td", "th"]):
        for attr in ("colspan", "rowspan"):
            if cell.has_attr(attr):
                try:
                    if int(cell[attr]) > 1:
                        return True
                except (TypeError, ValueError):
                    return True
    widths = {len(row.find_all(["td", "th"], recursive=False)) for row in table.find_all("tr")}
    widths.discard(0)
    if len(widths) > 1:  # filas con distinto numero de celdas
        return True
    if widths and max(widths) > 10:  # demasiado ancha: depende del formato visual
        return True
    return False


def _render_table(table: Tag) -> str:
    rows: list[str] = []
    for row in table.find_all("tr"):
        cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"], recursive=False)]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Preguntas frecuentes
# --------------------------------------------------------------------------

def _is_question(text: str) -> bool:
    text = clean_text(text)
    if not (8 <= len(text) <= 300):
        return False
    return text.endswith("?") or text.startswith("¿")


@dataclass
class _FaqNodes:
    question: str
    question_node: Tag
    answer_nodes: list[Tag]


def _following_answer_nodes(heading: Tag) -> list[Tag]:
    """Hermanos posteriores a un encabezado, hasta el siguiente encabezado."""
    level = int(heading.name[1])
    nodes: list[Tag] = []
    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if sibling.name in HEADING_TAGS and int(sibling.name[1]) <= level:
            break
        nodes.append(sibling)
    return nodes


def _detect_faq_nodes(main: Tag) -> list[_FaqNodes]:
    """Detecta pares pregunta/respuesta reales del DOM. Nunca inventa preguntas."""
    found: list[_FaqNodes] = []

    # A. Listas de definiciones: <dl><dt>pregunta</dt><dd>respuesta</dd></dl>
    for dl in main.find_all("dl"):
        for dt in dl.find_all("dt", recursive=False):
            dd = dt.find_next_sibling("dd")
            if dd is not None and _is_question(dt.get_text()):
                found.append(_FaqNodes(clean_text(dt.get_text(" ", strip=True)), dt, [dd]))

    # B. <details><summary>pregunta</summary>respuesta</details>
    for details in main.find_all("details"):
        summary = details.find("summary")
        if summary is None or not _is_question(summary.get_text()):
            continue
        answers = [c for c in details.find_all(recursive=False) if c is not summary]
        if answers:
            found.append(_FaqNodes(clean_text(summary.get_text(" ", strip=True)), summary, answers))

    # C. Acordeones: la pregunta va en una cabecera y la respuesta en el cuerpo.
    for item in main.find_all(attrs={"class": ACCORDION_RE}):
        questions_inside = [n for n in item.find_all(QUESTION_HOST_TAGS) if _is_question(n.get_text())]
        # Con varias preguntas dentro, `item` es el contenedor del acordeon, no un item.
        if len(questions_inside) != 1:
            continue
        head = questions_inside[0]
        if any(head is faq.question_node for faq in found):
            continue
        answers = [c for c in item.find_all(recursive=False) if c is not head and head not in c.descendants]
        if answers:
            found.append(_FaqNodes(clean_text(head.get_text(" ", strip=True)), head, answers))

    # D. Encabezados con forma de pregunta: la respuesta son los hermanos siguientes.
    for heading in main.find_all(sorted(HEADING_TAGS)):
        if not _is_question(heading.get_text()):
            continue
        if any(heading is faq.question_node for faq in found):
            continue
        answers = _following_answer_nodes(heading)
        if answers:
            found.append(_FaqNodes(clean_text(heading.get_text(" ", strip=True)), heading, answers))

    # Orden del documento y sin preguntas repetidas.
    order = {id(node): i for i, node in enumerate(main.descendants)}
    found.sort(key=lambda faq: order.get(id(faq.question_node), 0))

    unique: list[_FaqNodes] = []
    seen: set[str] = set()
    for faq in found:
        key = faq.question.lower()
        if key not in seen:
            seen.add(key)
            unique.append(faq)
    return unique


# --------------------------------------------------------------------------
# Renderizador BeautifulSoup
# --------------------------------------------------------------------------

@dataclass
class _RenderContext:
    faq_by_question_node: dict[int, _FaqNodes] = field(default_factory=dict)
    # Ancestros de las preguntas: hay que descender hasta ellas aunque el
    # contenedor no tenga descendientes de bloque (p. ej. un div con un boton).
    faq_ancestors: set[int] = field(default_factory=set)
    consumed: set[int] = field(default_factory=set)
    complex_tables: int = 0
    faqs: list[Faq] = field(default_factory=list)


def _has_block_descendant(node: Tag) -> bool:
    return node.find(BLOCK_TAGS) is not None


def _int_attr(node: Tag, attr: str, default: int) -> int:
    try:
        return int(node[attr])
    except (KeyError, TypeError, ValueError):
        return default


def _render_list(node: Tag) -> str:
    """Renderiza una lista respetando `<ol start>` y `<li value>`.

    La numeracion se toma del DOM: una lista que en la pagina empieza en 2 debe
    empezar en 2 aqui. Inventar un "1." seria alterar el contenido.
    """
    lines: list[str] = []
    ordered = node.name == "ol"
    index = _int_attr(node, "start", 1)
    for item in node.find_all("li", recursive=False):
        if ordered and item.has_attr("value"):
            index = _int_attr(item, "value", index)
        text = clean_text(item.get_text(" ", strip=True))
        if text:
            lines.append(f"{index}. {text}" if ordered else f"- {text}")
        index += 1
    return "\n".join(lines)


def _has_custom_list_numbering(main: Tag) -> bool:
    """True si alguna lista numera con `start`/`value`.

    trafilatura descarta esos atributos y reinicia toda lista en 1, asi que en
    estas paginas el texto lo arma BeautifulSoup, que si respeta la numeracion.
    """
    return main.find("ol", attrs={"start": True}) is not None or main.find("li", attrs={"value": True}) is not None


def _render_node(node: Tag, ctx: _RenderContext) -> list[str]:
    if id(node) in ctx.consumed or node.name in MEDIA_TAGS:
        return []

    # Pregunta frecuente: se emite junto a su respuesta y se marca como consumida.
    faq_nodes = ctx.faq_by_question_node.get(id(node))
    if faq_nodes is not None:
        ctx.consumed.add(id(node))
        answer_blocks: list[str] = []
        for answer_node in faq_nodes.answer_nodes:
            answer_blocks.extend(_render_node(answer_node, ctx))
            ctx.consumed.add(id(answer_node))
        # La respuesta se une con saltos simples: asi el par pregunta/respuesta
        # queda en un unico bloque y el fragmentador no puede separarlo.
        answer = "\n".join(b for b in (block.strip() for block in answer_blocks) if b)
        if not answer:
            return []
        ctx.faqs.append(Faq(question=faq_nodes.question, answer=answer))
        return [f"Pregunta: {faq_nodes.question}\n\nRespuesta: {answer}"]

    if node.name in HEADING_TAGS:
        text = clean_text(node.get_text(" ", strip=True))
        return [f"## {text}"] if text else []

    if node.name in ("ul", "ol"):
        rendered = _render_list(node)
        return [rendered] if rendered else []

    if node.name == "table":
        if _is_complex_table(node):
            ctx.complex_tables += 1
            return []
        rendered = _render_table(node)
        return [rendered] if rendered else []

    # Hoja de texto, salvo que dentro haya una pregunta que hay que alcanzar.
    if node.name in ("p", "blockquote", "pre") or (
        not _has_block_descendant(node) and id(node) not in ctx.faq_ancestors
    ):
        text = clean_text(node.get_text(" ", strip=True))
        return [text] if text else []

    blocks: list[str] = []
    for child in node.children:
        if isinstance(child, Tag):
            blocks.extend(_render_node(child, ctx))
        elif isinstance(child, NavigableString) and not isinstance(child, Comment):
            text = clean_text(str(child))
            if text:
                blocks.append(text)
    return blocks


def render_soup(main: Tag, faq_nodes: list[_FaqNodes] | None = None) -> tuple[str, _RenderContext]:
    faq_nodes = faq_nodes or []
    ancestors: set[int] = set()
    for faq in faq_nodes:
        for parent in faq.question_node.parents:
            ancestors.add(id(parent))
    ctx = _RenderContext(
        faq_by_question_node={id(f.question_node): f for f in faq_nodes},
        faq_ancestors=ancestors,
    )
    blocks = _render_node(main, ctx)
    return _join_blocks(blocks), ctx


# --------------------------------------------------------------------------
# Renderizador de la salida XML de trafilatura
# --------------------------------------------------------------------------

def _element_text(element) -> str:
    return clean_text(" ".join(element.itertext()))


def _tag_name(element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _inside_container(element) -> bool:
    """True si el elemento cuelga de una lista, tabla o cita ya emitidas.

    Sin esta guarda, un `<li><p>texto</p></li>` se emitiria dos veces: una como
    elemento de la lista y otra como parrafo suelto.
    """
    parent = element.getparent()
    while parent is not None:
        if _tag_name(parent) in TRAFILATURA_CONTAINERS:
            return True
        parent = parent.getparent()
    return False


def _render_trafilatura_xml(xml: str) -> str:
    try:
        root = etree.fromstring(xml.encode("utf-8"), parser=etree.XMLParser(recover=True))
    except etree.XMLSyntaxError:
        return ""
    if root is None:
        return ""

    blocks: list[str] = []
    body = root.find("main")
    for element in (body if body is not None else root).iter():
        tag = _tag_name(element)
        if tag not in TRAFILATURA_BLOCKS or _inside_container(element):
            continue
        if tag == "head":
            text = _element_text(element)
            if text:
                blocks.append(f"## {text}")
        elif tag in ("p", "quote"):
            text = _element_text(element)
            if text:
                blocks.append(text)
        elif tag == "list":
            ordered = element.get("rend") == "ol"
            items = []
            for index, item in enumerate(element.findall("item"), start=1):
                text = _element_text(item)
                if text:
                    items.append(f"{index}. {text}" if ordered else f"- {text}")
            if items:
                blocks.append("\n".join(items))
        elif tag == "table":
            rows = []
            for row in element.iter():
                if _tag_name(row) == "row":
                    cells = [_element_text(cell) for cell in row]
                    if any(cells):
                        rows.append(" | ".join(cells))
            if rows:
                blocks.append("\n".join(rows))
    return _join_blocks(blocks)


def _trafilatura_text(html: str, url: str | None) -> str:
    xml = trafilatura.extract(
        html,
        url=url,
        output_format="xml",
        include_tables=True,
        include_formatting=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        favor_precision=True,
        deduplicate=True,
    )
    return _render_trafilatura_xml(xml) if xml else ""


# --------------------------------------------------------------------------
# Controles de calidad
# --------------------------------------------------------------------------

def _link_ratio(main: Tag) -> float:
    total = len(clean_text(main.get_text(" ", strip=True)))
    if total == 0:
        return 1.0
    link_chars = sum(len(clean_text(a.get_text(" ", strip=True))) for a in main.find_all("a"))
    return link_chars / total


def _looks_fragmented(text: str) -> bool:
    """Detecta texto que quedo en trozos: menu mezclado o frases cortadas."""
    blocks = [b for b in text.split("\n\n") if b.strip()]
    if len(blocks) < 5:
        return False
    fragments = 0
    for block in blocks:
        if block.startswith(("## ", "- ", "Pregunta:")) or " | " in block or ORDERED_ITEM_RE.match(block):
            continue
        if len(block.split()) <= 3 and not block.rstrip().endswith((".", "!", "?", ":")):
            fragments += 1
    return fragments / len(blocks) > 0.7


def _short_text_reason(html: str, soup: BeautifulSoup, main: Tag, ctx: _RenderContext | None) -> str:
    """Motivo preciso cuando el texto extraido no alcanza el minimo.

    Los marcadores de captcha y login se buscan en el HTML original porque la
    limpieza ya elimino formularios y campos.
    """
    lowered = html.lower()
    if any(marker in lowered for marker in CAPTCHA_MARKERS):
        return REASON_CAPTCHA
    if PASSWORD_INPUT_RE.search(html):
        return REASON_LOGIN
    if any(marker in lowered for marker in SERVER_ERROR_MARKERS):
        return REASON_EXTRACTION
    if ctx is not None and ctx.complex_tables > 0:
        return REASON_COMPLEX_TABLE
    if main.find("a", href=PDF_HREF_RE):
        return REASON_EXTERNAL_DOC
    if main.find(["iframe", "object", "embed", "canvas"]):
        return REASON_EXTERNAL_DOC
    return REASON_INSUFFICIENT


# --------------------------------------------------------------------------
# API publica
# --------------------------------------------------------------------------

def extract(html: str, url: str | None = None, min_chars: int = 400, max_link_ratio: float = 0.5) -> ExtractResult:
    """Extrae el contenido principal, o rechaza la pagina con un motivo."""
    if not html or not html.strip():
        return ExtractResult(ok=False, reason=REASON_INSUFFICIENT)

    soup = BeautifulSoup(html, "lxml")
    breadcrumb = _breadcrumb(soup)
    updated_at = _updated_at(soup)
    _drop_noise(soup)
    main = _find_main(soup)
    title = _title(soup, main)

    faq_nodes = _detect_faq_nodes(main)

    # BeautifulSoup toma el relevo en las dos estructuras que trafilatura estropea:
    #  - preguntas frecuentes: los acordeones rompen el par pregunta/respuesta;
    #  - listas con numeracion propia: trafilatura descarta `start`/`value`.
    if len(faq_nodes) >= 2 or _has_custom_list_numbering(main):
        text, ctx = render_soup(main, faq_nodes)
        faqs = ctx.faqs
    else:
        text = _trafilatura_text(html, url)
        ctx = None
        faqs = []
        if len(text) < min_chars:  # respaldo con BeautifulSoup
            fallback_text, ctx = render_soup(main)
            # El contexto del respaldo se conserva siempre: sabe si se descarto
            # una tabla compleja, y eso decide el motivo del rechazo.
            if len(fallback_text) > len(text):
                text = fallback_text

    if len(text) < min_chars:
        return ExtractResult(
            ok=False,
            reason=_short_text_reason(html, soup, main, ctx),
            title=title,
            breadcrumb=breadcrumb,
        )

    if _link_ratio(main) > max_link_ratio and len(main.find_all("a")) >= 5:
        return ExtractResult(ok=False, reason=REASON_INSUFFICIENT, title=title, breadcrumb=breadcrumb)

    if _looks_fragmented(text):
        return ExtractResult(ok=False, reason=REASON_EXTRACTION, title=title, breadcrumb=breadcrumb)

    return ExtractResult(
        ok=True,
        title=title,
        text=text,
        breadcrumb=breadcrumb,
        updated_at=updated_at,
        faqs=faqs,
    )
