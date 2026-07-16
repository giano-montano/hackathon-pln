"""Division del texto de una pagina en fragmentos para RAG.

Reglas:
  - Entre `min_words` y `max_words` palabras (250-450 por defecto).
  - Solape maximo de 40 palabras, siempre en un limite de bloque.
  - Nunca se separa una pregunta de su respuesta.
  - Nunca se corta una lista de pasos.
  - Nunca se mezcla contenido de paginas distintas: se fragmenta pagina por pagina,
    asi la audiencia y el tema del documento se heredan intactos.
  - Se prefieren los encabezados como limite de corte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bloques que no se pueden partir: par pregunta/respuesta y listas de pasos.
ATOMIC_PREFIXES = ("Pregunta:", "- ", "1. ")
SENTENCE_END_RE = re.compile(r"(?<=[.!?:])\s+")


@dataclass(frozen=True)
class Block:
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def is_heading(self) -> bool:
        return self.text.startswith("## ")

    @property
    def is_atomic(self) -> bool:
        """No debe partirse aunque supere el maximo de palabras."""
        return self.text.startswith(ATOMIC_PREFIXES) or " | " in self.text


def split_blocks(text: str) -> list[Block]:
    """Separa el texto en bloques por linea en blanco.

    El formato exigido para las preguntas frecuentes lleva una linea en blanco
    entre la pregunta y la respuesta, asi que "Respuesta: ..." se vuelve a unir
    con su "Pregunta: ...": es un unico bloque indivisible.
    """
    blocks: list[Block] = []
    for raw in text.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        if blocks and raw.startswith("Respuesta:") and blocks[-1].text.startswith("Pregunta:"):
            blocks[-1] = Block(f"{blocks[-1].text}\n\n{raw}")
        else:
            blocks.append(Block(raw))
    return blocks


def _split_long_paragraph(block: Block, max_words: int) -> list[Block]:
    """Parte un parrafo muy largo por frases. Nunca se aplica a bloques atomicos."""
    if block.is_atomic or block.words <= max_words:
        return [block]

    parts: list[Block] = []
    current: list[str] = []
    count = 0
    for sentence in SENTENCE_END_RE.split(block.text):
        words = len(sentence.split())
        if current and count + words > max_words:
            parts.append(Block(" ".join(current)))
            current, count = [], 0
        current.append(sentence)
        count += words
    if current:
        parts.append(Block(" ".join(current)))
    return parts


def _tail_overlap(blocks: list[Block], overlap_words: int) -> list[Block]:
    """Solape: se reutiliza el ultimo bloque solo si es corto y no es atomico.

    Preferimos solapar en un limite de bloque antes que cortar una frase por la
    mitad; por eso el solape puede ser de 0 palabras.
    """
    if not blocks or overlap_words <= 0:
        return []
    last = blocks[-1]
    if last.is_atomic or last.words > overlap_words:
        return []
    # Un encabezado suelto al final se arrastra para no dejarlo huerfano.
    return [last]


def chunk_text(
    text: str,
    min_words: int = 250,
    max_words: int = 450,
    overlap_words: int = 40,
) -> list[str]:
    """Divide el texto de UNA pagina en fragmentos."""
    if not text or not text.strip():
        return []

    blocks: list[Block] = []
    for block in split_blocks(text):
        blocks.extend(_split_long_paragraph(block, max_words))

    chunks: list[str] = []
    current: list[Block] = []
    words = 0

    def flush() -> None:
        nonlocal current, words
        if current:
            chunks.append("\n\n".join(b.text for b in current))
        carry = _tail_overlap(current, overlap_words)
        current = list(carry)
        words = sum(b.words for b in current)

    for block in blocks:
        # Un encabezado es un limite natural: se corta antes si ya hay suficiente.
        if block.is_heading and words >= min_words:
            flush()
        # Cerrar antes de excederse, siempre que el fragmento ya tenga cuerpo.
        elif current and words + block.words > max_words and words >= min_words:
            flush()

        current.append(block)
        words += block.words

    if current:
        # El resto se une al anterior si es demasiado corto para sostenerse solo.
        # Se permite pasarse del maximo unicamente cuando el fragmento anterior ya
        # se habia pasado por un bloque indivisible: ahi el limite ya estaba cedido,
        # y arrastrar la cola es mejor que dejar un fragmento de dos lineas.
        rest = "\n\n".join(b.text for b in current)
        previous_words = len(chunks[-1].split()) if chunks else 0
        fits = previous_words + words <= max_words or previous_words > max_words
        if chunks and words < min_words // 3 and fits:
            chunks[-1] = chunks[-1] + "\n\n" + rest
        else:
            chunks.append(rest)

    # Un encabezado suelto arrastrado como solape no puede ser un fragmento entero.
    return [c for c in chunks if c.strip() and not (len(c.split()) <= 3 and c.startswith("## "))]


def chunk_document(
    document_id: str,
    text: str,
    min_words: int = 250,
    max_words: int = 450,
    overlap_words: int = 40,
) -> list[tuple[str, str]]:
    """Devuelve pares (chunk_id, texto) con ids correlativos: `<document_id>-001`."""
    pieces = chunk_text(text, min_words, max_words, overlap_words)
    return [(f"{document_id}-{i:03d}", piece) for i, piece in enumerate(pieces, start=1)]
