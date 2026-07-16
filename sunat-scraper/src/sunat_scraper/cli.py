"""Interfaz de linea de comandos.

    python -m sunat_scraper discover [--dry-run]
    python -m sunat_scraper crawl
    python -m sunat_scraper process
    python -m sunat_scraper run --source orientacion --max-pages 150 --resume
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

import typer

from . import chunker, classifier, extractor, sitemap
from .config import Config, Source, data_dir, load_config
from .downloader import Downloader
from .exporter import (
    Chunk,
    Deduplicator,
    Document,
    FaqRecord,
    JsonlWriter,
    Rejected,
    make_document_id,
    now_lima,
    read_jsonl,
    write_summary,
)
from .filters import UrlFilter

app = typer.Typer(add_completion=False, help="Scraper de contenido oficial HTML de SUNAT para un corpus RAG.")

DEFAULT_CONFIG = "config/sources.yaml"

# Rutas relativas al directorio de datos.
URLS_FILE = "raw/urls.jsonl"
DISCOVERY_FILE = "raw/discovery.json"
FAILURES_FILE = "raw/failures.jsonl"
EXCLUDED_FILE = "raw/excluded_urls.jsonl"
HTML_DIR = "raw/html"
DOCUMENTS_FILE = "processed/documents.jsonl"
CHUNKS_FILE = "processed/chunks.jsonl"
FAQS_FILE = "processed/faqs.jsonl"
REJECTED_FILE = "processed/rejected.jsonl"
SUMMARY_FILE = "reports/summary.json"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _selected_sources(config: Config, source: str | None) -> list[Source]:
    return [config.source(source)] if source else config.sources


def _load_urls(base: Path, source: str | None, max_pages: int | None) -> list[dict]:
    """URLs aceptadas, filtradas por fuente y limitadas POR FUENTE.

    El limite es por fuente y no global: en una corrida con varias fuentes, un
    limite global se gastaria entero en la primera.
    """
    urls = read_jsonl(base / URLS_FILE)
    if source:
        urls = [u for u in urls if u["source"] == source]
    if not max_pages:
        return urls

    per_source = Counter()
    limited = []
    for item in urls:
        if per_source[item["source"]] < max_pages:
            per_source[item["source"]] += 1
            limited.append(item)
    return limited


def _downloader(config: Config, base: Path, resume: bool, requests_per_second: float | None) -> Downloader:
    defaults = config.defaults.model_copy()
    if requests_per_second:
        defaults.requests_per_second = requests_per_second
    return Downloader(defaults, base / HTML_DIR, resume=resume)


# --------------------------------------------------------------------------
# discover
# --------------------------------------------------------------------------

def _discover_source(
    source: Source,
    url_filter: UrlFilter,
    downloader: Downloader,
    max_discovery_pages: int | None = None,
) -> list[sitemap.DiscoveredUrl]:
    """URLs de una fuente: sitemap (con anidados), o enlaces, mas la lista manual."""
    found: list[sitemap.DiscoveredUrl] = []

    if source.sitemap:
        found = sitemap.discover(source.sitemap, downloader.fetch_bytes)
        if not found:
            # rss.xml solo como respaldo, cuando no hay sitemap util.
            fallback = sitemap.rss_fallback_url(source.sitemap)
            typer.echo(f"  sitemap sin resultados; probando {fallback}")
            found = sitemap.discover(fallback, downloader.fetch_bytes)
        typer.echo(f"  sitemap: {len(found)} URLs")

    if not found and source.crawl_links:
        # Sin sitemap util: se recorren los enlaces con los mismos filtros.
        start = source.crawl_start_urls or [f"https://{source.domain}/"]
        budget = max_discovery_pages or source.crawl_max_pages
        typer.echo(f"  sin sitemap; recorriendo enlaces (max {budget} paginas, prof. {source.crawl_max_depth})")

        def fetch_html(url: str) -> str | None:
            result = downloader.fetch(url)
            return result.html if result.ok else None

        found = sitemap.discover_links(
            start_urls=start,
            fetch_html=fetch_html,
            follow=url_filter.followable,
            candidate=url_filter.same_domain,
            max_pages=budget,
            max_depth=source.crawl_max_depth,
        )

    known = {u.loc for u in found}
    found.extend(sitemap.DiscoveredUrl(loc=u) for u in source.manual_urls if u not in known)
    return found


def _run_discover(
    config: Config,
    base: Path,
    source: str | None,
    dry_run: bool,
    requests_per_second: float | None,
    resume: bool = True,
    max_discovery_pages: int | None = None,
) -> dict:
    sources = _selected_sources(config, source)
    accepted: list[dict] = []
    excluded: list[dict] = []
    stats = {"discovered": 0, "accepted": 0, "rejected": 0, "by_source": {}}
    rules = Counter()
    audiences = Counter()
    topics = Counter()

    # El descubrimiento por enlaces ya descarga HTML: con la cache activa,
    # `crawl --resume` lo reutiliza y no se pide dos veces la misma pagina.
    with _downloader(config, base, resume=resume, requests_per_second=requests_per_second) as downloader:
        for src in sources:
            typer.echo(f"\n[{src.name}] descubriendo URLs...")
            url_filter = UrlFilter(src, config.global_exclude_patterns)
            discovered = _discover_source(src, url_filter, downloader, max_discovery_pages)

            source_accepted = 0
            for item in discovered:
                result = url_filter.decide(item.loc)
                rules[result.rule] += 1
                if result.accepted:
                    source_accepted += 1
                    accepted.append(
                        {"url": result.url, "source": src.name, "rule": result.rule, "lastmod": item.lastmod}
                    )
                    # En el descubrimiento solo se conoce la URL; el titulo y el
                    # breadcrumb afinaran la clasificacion en `process`.
                    audiences[classifier.classify_audience(result.url, "", "", config.classification, src)] += 1
                    topics[classifier.classify_topic(result.url, "", "", config.classification, src)] += 1
                else:
                    excluded.append({"url": result.url, "reason": result.reason, "rule": result.rule})

            stats["by_source"][src.name] = {"discovered": len(discovered), "accepted": source_accepted}
            typer.echo(f"  descubiertas: {len(discovered)} | aceptadas: {source_accepted}")

    stats["discovered"] = sum(s["discovered"] for s in stats["by_source"].values())
    stats["accepted"] = len(accepted)
    stats["rejected"] = len(excluded)

    if dry_run:
        _print_dry_run(stats, rules, audiences, topics, accepted, excluded)
        return stats

    with JsonlWriter(base / URLS_FILE) as writer:
        writer.write_all(accepted)
    with JsonlWriter(base / EXCLUDED_FILE) as writer:
        writer.write_all(excluded)
    write_summary(base / DISCOVERY_FILE, stats)
    typer.echo(f"\nURLs aceptadas -> {base / URLS_FILE} ({len(accepted)})")
    return stats


def _print_dry_run(stats, rules, audiences, topics, accepted, excluded) -> None:
    typer.echo("\n=== DRY RUN ===")
    typer.echo(f"URLs descubiertas : {stats['discovered']}")
    typer.echo(f"URLs aceptadas    : {stats['accepted']}")
    typer.echo(f"URLs rechazadas   : {stats['rejected']}")

    typer.echo("\n-- Por fuente --")
    for name, values in stats["by_source"].items():
        typer.echo(f"  {name:14} descubiertas={values['discovered']:5}  aceptadas={values['accepted']:5}")

    typer.echo("\n-- Regla aplicada --")
    for rule, count in rules.most_common():
        typer.echo(f"  {count:5}  {rule}")

    typer.echo("\n-- Aceptadas por audiencia --")
    for audience, count in audiences.most_common():
        typer.echo(f"  {count:5}  {audience}")

    typer.echo("\n-- Aceptadas por tema --")
    for topic, count in topics.most_common():
        typer.echo(f"  {count:5}  {topic}")

    typer.echo("\n-- Ejemplos aceptados --")
    for item in accepted[:10]:
        typer.echo(f"  + {item['url']}  [{item['rule']}]")

    typer.echo("\n-- Ejemplos rechazados --")
    for item in excluded[:10]:
        typer.echo(f"  - {item['url']}  [{item['rule']} -> {item['reason']}]")
    typer.echo("\n(dry-run: no se escribio ningun archivo)")


# --------------------------------------------------------------------------
# crawl
# --------------------------------------------------------------------------

def _run_crawl(
    config: Config,
    base: Path,
    source: str | None,
    max_pages: int | None,
    resume: bool,
    requests_per_second: float | None,
) -> dict:
    urls = _load_urls(base, source, max_pages)
    if not urls:
        typer.echo("No hay URLs que descargar. Ejecuta primero `discover`.")
        raise typer.Exit(code=1)

    downloaded = 0
    cached = 0
    failures: list[dict] = []

    with _downloader(config, base, resume=resume, requests_per_second=requests_per_second) as downloader:
        with typer.progressbar(urls, label="Descargando") as progress:
            for item in progress:
                result = downloader.fetch(item["url"])
                if result.ok:
                    downloaded += 1
                    cached += int(result.from_cache)
                else:
                    reason = "non_html" if "non_html" in (result.error or "") else "download_error"
                    failures.append({"url": item["url"], "reason": reason, "error": result.error})

    with JsonlWriter(base / FAILURES_FILE) as writer:
        writer.write_all(failures)

    typer.echo(f"Descargadas: {downloaded} (desde cache: {cached}) | fallidas: {len(failures)}")
    return {"downloaded": downloaded, "from_cache": cached, "failed": len(failures)}


# --------------------------------------------------------------------------
# process
# --------------------------------------------------------------------------

def _run_process(config: Config, base: Path, source: str | None, max_pages: int | None) -> dict:
    urls = _load_urls(base, source, max_pages)
    if not urls:
        typer.echo("No hay URLs que procesar. Ejecuta primero `discover` y `crawl`.")
        raise typer.Exit(code=1)

    defaults = config.defaults
    dedup = Deduplicator()
    counts = Counter()
    audiences = Counter()
    topics = Counter()
    reasons = Counter()

    documents_out = JsonlWriter(base / DOCUMENTS_FILE)
    chunks_out = JsonlWriter(base / CHUNKS_FILE)
    faqs_out = JsonlWriter(base / FAQS_FILE)
    rejected_out = JsonlWriter(base / REJECTED_FILE)

    # Las exclusiones por regla y los errores de descarga tambien son rechazos.
    for record in read_jsonl(base / EXCLUDED_FILE):
        rejected_out.write(Rejected(url=record["url"], reason=record["reason"]))
        reasons[record["reason"]] += 1
    for record in read_jsonl(base / FAILURES_FILE):
        rejected_out.write(Rejected(url=record["url"], reason=record["reason"]))
        reasons[record["reason"]] += 1
        counts["failed"] += 1

    downloader = Downloader(defaults, base / HTML_DIR, resume=True)
    try:
        with typer.progressbar(urls, label="Procesando ") as progress:
            for item in progress:
                url = item["url"]
                src = config.source(item["source"])

                cached = downloader.cached(url)
                if cached is None or not cached.html:
                    rejected_out.write(Rejected(url=url, reason="download_error"))
                    reasons["download_error"] += 1
                    counts["failed"] += 1
                    continue
                counts["downloaded"] += 1

                if dedup.seen_url(url):
                    rejected_out.write(Rejected(url=url, reason="duplicate"))
                    reasons["duplicate"] += 1
                    continue

                result = extractor.extract(
                    cached.html,
                    url=url,
                    min_chars=defaults.min_text_chars,
                    max_link_ratio=defaults.max_link_ratio,
                )
                if not result.ok:
                    rejected_out.write(Rejected(url=url, reason=result.reason or "extraction_error"))
                    reasons[result.reason or "extraction_error"] += 1
                    counts["rejected"] += 1
                    continue

                document_id = dedup.unique_id(make_document_id(src.name, url), url)
                duplicate_of = dedup.seen_text(result.text, document_id)
                if duplicate_of:
                    rejected_out.write(Rejected(url=url, reason="duplicate"))
                    reasons["duplicate"] += 1
                    continue

                audience = classifier.classify_audience(url, result.title, result.breadcrumb, config.classification, src)
                topic = classifier.classify_topic(url, result.title, result.breadcrumb, config.classification, src)
                audiences[audience] += 1
                topics[topic] += 1

                documents_out.write(
                    Document(
                        id=document_id,
                        url=url,
                        title=result.title,
                        audience=audience,
                        topic=topic,
                        text=result.text,
                        collected_at=now_lima(),
                        updated_at=result.updated_at or item.get("lastmod"),
                        source=src.name,
                    )
                )
                counts["documents"] += 1

                for chunk_id, text in chunker.chunk_document(
                    document_id,
                    result.text,
                    min_words=defaults.chunk_min_words,
                    max_words=defaults.chunk_max_words,
                    overlap_words=defaults.chunk_overlap_words,
                ):
                    chunks_out.write(
                        Chunk(id=chunk_id, document_id=document_id, text=text, audience=audience, topic=topic, url=url)
                    )
                    counts["chunks"] += 1

                for faq in result.faqs:
                    faqs_out.write(
                        FaqRecord(question=faq.question, answer=faq.answer, audience=audience, topic=topic, url=url)
                    )
                    counts["faqs"] += 1
    finally:
        downloader.close()
        for writer in (documents_out, chunks_out, faqs_out, rejected_out):
            writer.close()

    discovery = _read_discovery(base)
    summary = {
        "sources": len(_selected_sources(config, source)),
        "discovered_urls": discovery.get("discovered", len(urls)),
        "accepted_urls": discovery.get("accepted", len(urls)),
        "downloaded_pages": counts["downloaded"],
        "rejected_pages": counts["rejected"],
        "failed_pages": counts["failed"],
        "documents": counts["documents"],
        "faqs": counts["faqs"],
        "chunks": counts["chunks"],
        "by_audience": dict(audiences.most_common()),
        "by_topic": dict(topics.most_common()),
        "rejected_by_reason": dict(reasons.most_common()),
        "generated_at": now_lima(),
    }
    write_summary(base / SUMMARY_FILE, summary)

    typer.echo(
        f"\nDocumentos: {counts['documents']} | fragmentos: {counts['chunks']} | "
        f"FAQs: {counts['faqs']} | rechazadas: {counts['rejected']}"
    )
    typer.echo(f"Reporte -> {base / SUMMARY_FILE}")
    return summary


def _read_discovery(base: Path) -> dict:
    path = base / DISCOVERY_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

@app.command()
def discover(
    config: str = typer.Option(DEFAULT_CONFIG, "--config", help="Ruta de sources.yaml."),
    source: str = typer.Option(None, "--source", help="Procesar solo esta fuente."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostrar el resultado sin escribir archivos."),
    requests_per_second: float = typer.Option(None, "--requests-per-second", help="Limite de solicitudes por segundo."),
    max_discovery_pages: int = typer.Option(
        None, "--max-discovery-pages", help="Paginas a visitar en el recorrido de enlaces (fuentes sin sitemap)."
    ),
    data: str = typer.Option(None, "--data-dir", help="Directorio de datos (por defecto: data/)."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Lee los sitemaps (o recorre enlaces) y aplica los filtros a las URLs."""
    _setup_logging(verbose)
    _run_discover(
        load_config(config),
        Path(data) if data else data_dir(),
        source,
        dry_run,
        requests_per_second,
        max_discovery_pages=max_discovery_pages,
    )


@app.command()
def crawl(
    config: str = typer.Option(DEFAULT_CONFIG, "--config"),
    source: str = typer.Option(None, "--source"),
    max_pages: int = typer.Option(None, "--max-pages", help="Maximo de paginas a descargar."),
    requests_per_second: float = typer.Option(None, "--requests-per-second"),
    resume: bool = typer.Option(False, "--resume", help="Reutiliza el HTML ya descargado."),
    data: str = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Descarga el HTML de las URLs aceptadas y lo guarda en data/raw/html/."""
    _setup_logging(verbose)
    _run_crawl(load_config(config), Path(data) if data else data_dir(), source, max_pages, resume, requests_per_second)


@app.command()
def process(
    config: str = typer.Option(DEFAULT_CONFIG, "--config"),
    source: str = typer.Option(None, "--source"),
    max_pages: int = typer.Option(None, "--max-pages"),
    data: str = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Extrae, limpia, clasifica, fragmenta y exporta el corpus."""
    _setup_logging(verbose)
    _run_process(load_config(config), Path(data) if data else data_dir(), source, max_pages)


@app.command()
def run(
    config: str = typer.Option(DEFAULT_CONFIG, "--config"),
    source: str = typer.Option(None, "--source"),
    max_pages: int = typer.Option(None, "--max-pages"),
    requests_per_second: float = typer.Option(None, "--requests-per-second"),
    max_discovery_pages: int = typer.Option(None, "--max-discovery-pages"),
    resume: bool = typer.Option(False, "--resume"),
    data: str = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Ejecuta el flujo completo: discover -> crawl -> process."""
    _setup_logging(verbose)
    cfg = load_config(config)
    base = Path(data) if data else data_dir()
    _run_discover(
        cfg, base, source, dry_run=False, requests_per_second=requests_per_second,
        max_discovery_pages=max_discovery_pages,
    )
    _run_crawl(cfg, base, source, max_pages, resume, requests_per_second)
    _run_process(cfg, base, source, max_pages)


if __name__ == "__main__":
    app()
