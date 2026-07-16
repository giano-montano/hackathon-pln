"""Clasificacion de audiencia y tema mediante reglas configurables.

Sin LLM: solo dominio, URL, titulo y breadcrumb. Las reglas viven en
config/sources.yaml (bloque `classification`) y se evaluan en orden:
gana la primera que coincide.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import urlsplit

from .config import Audience, Classification, Source

# Peso de cada senal al puntuar un tema. La URL es la mas confiable.
WEIGHT_URL = 3
WEIGHT_TITLE = 2
WEIGHT_BREADCRUMB = 1


def normalize(value: str) -> str:
    """Minusculas y sin tildes, para comparar patrones de forma estable."""
    value = unicodedata.normalize("NFD", value.lower())
    return "".join(c for c in value if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=512)
def _pattern_regex(needle: str) -> re.Pattern[str]:
    # Anclado al inicio de palabra, libre al final: `isc` no coincide dentro de
    # `discapacidad`, pero `comprobante` si coincide en `comprobantes`.
    return re.compile(r"(?<![a-z0-9])" + re.escape(needle))


def matches(needle: str, haystack: str) -> bool:
    """True si el patron aparece al inicio de una palabra del texto."""
    return _pattern_regex(needle).search(haystack) is not None


def _signals(url: str, title: str, breadcrumb: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    return (
        normalize(parts.path + " " + parts.query),
        normalize(title),
        normalize(breadcrumb),
    )


def classify_audience(
    url: str,
    title: str,
    breadcrumb: str,
    classification: Classification,
    source: Source | None = None,
) -> Audience:
    """Una sola audiencia principal por pagina."""
    url_text, title_text, crumb_text = _signals(url, title, breadcrumb)
    haystack = f"{url_text} {title_text} {crumb_text}"

    for rule in classification.audience_rules:
        if any(matches(normalize(pattern), haystack) for pattern in rule.patterns):
            return rule.audience

    domain = urlsplit(url).netloc.lower()
    if domain in classification.audience_by_domain:
        return classification.audience_by_domain[domain]

    if source is not None:
        return source.default_audience
    return "todas"


def classify_topic(
    url: str,
    title: str,
    breadcrumb: str,
    classification: Classification,
    source: Source | None = None,
) -> str:
    """Tema con mayor puntaje. La URL pesa mas que el titulo, y este mas que el breadcrumb.

    Cada senal aporta su peso UNA sola vez, aunque coincidan varios patrones de la
    misma regla. Si se sumara por patron, una regla generica con muchos sinonimos
    (`comprobante`, `comprobantes`, ...) le ganaria siempre a la mas especifica
    (`recibo-por-honorarios`).
    """
    signals = zip(_signals(url, title, breadcrumb), (WEIGHT_URL, WEIGHT_TITLE, WEIGHT_BREADCRUMB))
    weighted_signals = list(signals)

    best_topic, best_score = None, 0
    for rule in classification.topic_rules:
        needles = [normalize(pattern) for pattern in rule.patterns]
        score = sum(
            weight for text, weight in weighted_signals if any(matches(needle, text) for needle in needles)
        )
        # `>` conserva el orden del YAML ante empates: gana la regla mas especifica.
        if score > best_score:
            best_topic, best_score = rule.topic, score

    if best_topic:
        return best_topic
    if source is not None:
        return source.default_topic
    return "otros"
