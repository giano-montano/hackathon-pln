"""Utilidades comunes. Ninguna prueba usa internet: todo sale de tests/fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from sunat_scraper.config import Classification, Source, load_config

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_PATH = Path(__file__).parents[1] / "config" / "sources.yaml"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def config():
    return load_config(CONFIG_PATH)


@pytest.fixture
def classification(config) -> Classification:
    return config.classification


@pytest.fixture
def orientacion(config) -> Source:
    return config.source("orientacion")


@pytest.fixture
def emprender(config) -> Source:
    return config.source("emprender")
