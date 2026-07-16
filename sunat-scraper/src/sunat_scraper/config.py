"""Carga y validacion de config/sources.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

Audience = Literal["personas", "empresas", "emprendedor", "todas"]

TOPICS = [
    "ruc",
    "clave_sol",
    "comprobantes",
    "recibos_honorarios",
    "declaraciones",
    "regimenes_tributarios",
    "igv",
    "impuesto_renta",
    "multas",
    "gradualidad",
    "fraccionamiento",
    "devoluciones",
    "cobranza",
    "detracciones",
    "retenciones",
    "percepciones",
    "formalizacion",
    "libros_electronicos",
    "otros",
]


class Defaults(BaseModel):
    """Ajustes globales de descarga y procesamiento."""

    user_agent: str = "sunat-scraper/0.1 (+corpus RAG educativo)"
    timeout: float = 30.0
    requests_per_second: float = 1.0
    max_retries: int = 3
    respect_robots: bool = True
    min_text_chars: int = 400
    max_link_ratio: float = 0.5
    chunk_min_words: int = 250
    chunk_max_words: int = 450
    chunk_overlap_words: int = 40


class Source(BaseModel):
    """Una fuente configurable. Agregar fuentes no requiere tocar el codigo."""

    name: str
    domain: str
    sitemap: str | None = None
    manual_urls: list[str] = Field(default_factory=list)
    # Recorrido de enlaces: respaldo para las fuentes que no publican sitemap.
    crawl_links: bool = False
    crawl_start_urls: list[str] = Field(default_factory=list)
    crawl_max_pages: int = 400
    crawl_max_depth: int = 3
    include_prefixes: list[str] = Field(default_factory=list)
    include_urls: list[str] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    default_audience: Audience = "todas"
    default_topic: str = "otros"


class AudienceRule(BaseModel):
    audience: Audience
    patterns: list[str]


class TopicRule(BaseModel):
    topic: str
    patterns: list[str]


class Classification(BaseModel):
    audience_rules: list[AudienceRule] = Field(default_factory=list)
    audience_by_domain: dict[str, Audience] = Field(default_factory=dict)
    topic_rules: list[TopicRule] = Field(default_factory=list)


class Config(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    global_exclude_patterns: list[str] = Field(default_factory=list)
    sources: list[Source]
    classification: Classification = Field(default_factory=Classification)

    def source(self, name: str) -> Source:
        for src in self.sources:
            if src.name == name:
                return src
        known = ", ".join(s.name for s in self.sources)
        raise KeyError(f"Fuente desconocida: {name!r}. Disponibles: {known}")


def _env_overrides(defaults: dict) -> dict:
    """Variables de entorno con prefijo SUNAT_SCRAPER_ pisan el YAML."""
    mapping = {
        "SUNAT_SCRAPER_USER_AGENT": ("user_agent", str),
        "SUNAT_SCRAPER_TIMEOUT": ("timeout", float),
        "SUNAT_SCRAPER_REQUESTS_PER_SECOND": ("requests_per_second", float),
    }
    for env_key, (field, cast) in mapping.items():
        raw = os.getenv(env_key)
        if raw:
            defaults[field] = cast(raw)
    return defaults


def load_config(path: str | Path = "config/sources.yaml") -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data["defaults"] = _env_overrides(data.get("defaults") or {})
    return Config.model_validate(data)


def data_dir() -> Path:
    return Path(os.getenv("SUNAT_SCRAPER_DATA_DIR", "data"))
