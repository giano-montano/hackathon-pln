"""Modelos de salida, deduplicacion y escritura de JSONL."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from pydantic import BaseModel

from .classifier import normalize
from .filters import canonicalize

# Peru no aplica horario de verano: el desfase es fijo.
LIMA_TZ = timezone(timedelta(hours=-5))

MAX_ID_LENGTH = 90


class Document(BaseModel):
    """Una pagina. Campos obligatorios primero; los opcionales se omiten si van vacios."""

    id: str
    url: str
    title: str
    audience: str
    topic: str
    text: str
    collected_at: str
    updated_at: str | None = None
    source: str | None = None
    subtopic: str | None = None


class Chunk(BaseModel):
    id: str
    document_id: str
    text: str
    audience: str
    topic: str
    url: str


class FaqRecord(BaseModel):
    question: str
    answer: str
    audience: str
    topic: str
    url: str


class Rejected(BaseModel):
    url: str
    reason: str


def now_lima() -> str:
    return datetime.now(LIMA_TZ).isoformat(timespec="seconds")


def text_hash(text: str) -> str:
    """Hash exacto del texto limpio: segunda capa de deduplicacion."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def make_document_id(source_name: str, url: str) -> str:
    """`orientacion-ruc-personas` a partir de la fuente y la ruta de la URL."""
    path = urlsplit(canonicalize(url)).path.strip("/")
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(path)).strip("-") or "inicio"
    return f"{source_name}-{slug}"[:MAX_ID_LENGTH].rstrip("-")


class Deduplicator:
    """Deduplicacion simple: URL canonica y hash exacto del texto limpio."""

    def __init__(self) -> None:
        self._urls: set[str] = set()
        self._hashes: dict[str, str] = {}
        self._ids: set[str] = set()

    def seen_url(self, url: str) -> bool:
        canon = canonicalize(url)
        if canon in self._urls:
            return True
        self._urls.add(canon)
        return False

    def seen_text(self, text: str, document_id: str) -> str | None:
        """Devuelve el id del documento original si el texto ya existia."""
        digest = text_hash(text)
        original = self._hashes.get(digest)
        if original is not None:
            return original
        self._hashes[digest] = document_id
        return None

    def unique_id(self, base_id: str, url: str) -> str:
        """Evita colisiones de id anadiendo un sufijo corto del hash de la URL."""
        if base_id not in self._ids:
            self._ids.add(base_id)
            return base_id
        suffix = hashlib.sha1(canonicalize(url).encode("utf-8")).hexdigest()[:6]
        unique = f"{base_id[: MAX_ID_LENGTH - 7].rstrip('-')}-{suffix}"
        self._ids.add(unique)
        return unique


class JsonlWriter:
    """Escribe JSONL en UTF-8 sin escapar los acentos."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        self.count = 0

    def write(self, record: BaseModel | dict[str, Any]) -> None:
        data = record.model_dump(exclude_none=True) if isinstance(record, BaseModel) else record
        self._handle.write(json.dumps(data, ensure_ascii=False) + "\n")
        self.count += 1

    def write_all(self, records: Iterable[BaseModel | dict[str, Any]]) -> None:
        for record in records:
            self.write(record)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    file = Path(path)
    if not file.exists():
        return []
    records = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_summary(path: str | Path, summary: dict[str, Any]) -> None:
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
