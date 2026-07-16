#!/usr/bin/env python3
"""
Postprocesamiento y validación del corpus TUPA para RAG.

Qué hace:
1. Valida JSONL, IDs y referencias padre-hijo.
2. Normaliza el estado de vigencia.
3. Separa procedimientos/chunks activos e inactivos.
4. Fusiona chunks que contienen solo encabezados como "REQUISITOS ESPECÍFICOS"
   con el chunk siguiente del mismo procedimiento y tipo.
5. Conserva chunks breves pero informativos, como "NO APLICA" o
   "SOLICITUD EN FORMATO LIBRE".
6. Genera archivos listos para indexación y un reporte de auditoría.

Uso:
python postprocess_tupa_rag.py --input-dir output_tupa
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


HEADING_ONLY_PATTERNS = [
    re.compile(r"^\s*REQUISITOS\s+GENERALES\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^\s*REQUISITOS\s+ESPEC[IÍ]FICOS\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^\s*DOCUMENTACI[OÓ]N\s*-\s*REQUISITOS\s+GENERALES\s*:?\s*$", re.IGNORECASE),
    re.compile(r"^\s*DOCUMENTACI[OÓ]N\s*-\s*REQUISITOS\s+ESPEC[IÍ]FICOS\s*:?\s*$", re.IGNORECASE),
]

INACTIVE_MARKERS = ("ELIMINADO", "MODIFICADO A SERVICIO PRESTADO EN EXCLUSIVIDAD")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida y postprocesa procedures.jsonl y chunks.jsonl del TUPA."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("output_tupa"),
        help="Carpeta que contiene procedures.jsonl y chunks.jsonl.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Carpeta de salida. Por defecto: <input-dir>/rag_ready",
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Incluye registros eliminados/modificados en los archivos *_ready.jsonl.",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo: {path}")

    records: list[dict[str, Any]] = []
    errors: list[str] = []

    with path.open("r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"Línea {line_number}: {exc}")
                continue
            if not isinstance(value, dict):
                errors.append(f"Línea {line_number}: el valor no es un objeto JSON")
                continue
            records.append(value)

    if errors:
        preview = "\n".join(errors[:20])
        raise ValueError(f"Errores al leer {path}:\n{preview}")

    return records


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalize_status(status: Any) -> tuple[bool, str, str | None]:
    if status is None or not str(status).strip():
        return True, "vigente_en_documento", None

    detail = str(status).strip()
    upper = detail.upper()

    if "ELIMINADO" in upper:
        return False, "eliminado", detail

    if "MODIFICADO A SERVICIO PRESTADO EN EXCLUSIVIDAD" in upper:
        return False, "reemplazado_por_servicio", detail

    return True, "estado_no_clasificado", detail


def apply_status_metadata(record: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(record.get("metadata") or {})
    is_active, status_type, status_detail = normalize_status(metadata.get("status"))

    metadata["is_active"] = is_active
    metadata["status_type"] = status_type
    metadata["status_detail"] = status_detail

    result = dict(record)
    result["metadata"] = metadata
    return result


def is_heading_only(text: str) -> bool:
    normalized = text.strip()
    return any(pattern.fullmatch(normalized) for pattern in HEADING_ONLY_PATTERNS)


def estimate_tokens(text: str) -> int:
    # Aproximación conservadora para español. No sustituye el tokenizer
    # específico del modelo de embeddings.
    words = re.findall(r"\S+", text)
    return max(1, round(len(words) * 1.30))


def build_page_content(metadata: dict[str, Any], raw_text: str) -> str:
    lines = [
        "SUNAT - TUPA 2018",
        f"Procedimiento TUPA: {metadata.get('codigo_tupa', '')}",
        f"Nombre: {metadata.get('procedimiento', '')}",
    ]

    if metadata.get("section"):
        lines.append(f"Sección: {metadata['section']}")
    if metadata.get("category"):
        lines.append(f"Categoría: {metadata['category']}")
    if metadata.get("content_label"):
        lines.append(f"Tipo de contenido: {metadata['content_label']}")
    if metadata.get("subprocedure_code"):
        lines.append(f"Subprocedimiento: {metadata['subprocedure_code']}")
    if metadata.get("subprocedure_title"):
        lines.append(f"Nombre del subprocedimiento: {metadata['subprocedure_title']}")

    lines.append("")
    lines.append(raw_text.strip())
    return "\n".join(lines).strip()


def merge_heading_only_chunks(
    chunks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """
    Fusiona un chunk que contiene solo un encabezado con el siguiente chunk
    del mismo parent_id y content_type.

    Se conserva el ID del chunk siguiente porque contiene el contenido principal.
    """
    result: list[dict[str, Any]] = []
    merges: list[dict[str, str]] = []
    index = 0

    while index < len(chunks):
        current = chunks[index]
        raw_text = str(current.get("raw_text") or "").strip()

        if is_heading_only(raw_text) and index + 1 < len(chunks):
            following = chunks[index + 1]
            current_meta = current.get("metadata") or {}
            following_meta = following.get("metadata") or {}

            same_parent = current.get("parent_id") == following.get("parent_id")
            same_type = current_meta.get("content_type") == following_meta.get("content_type")

            if same_parent and same_type:
                merged = dict(following)
                merged_meta = dict(following_meta)
                merged_raw = f"{raw_text}\n\n{str(following.get('raw_text') or '').strip()}"

                merged["raw_text"] = merged_raw
                merged["page_content"] = build_page_content(merged_meta, merged_raw)
                merged_meta["approx_tokens"] = estimate_tokens(merged["page_content"])
                merged_meta["merged_heading_chunk_id"] = current.get("id")
                merged["metadata"] = merged_meta

                merges.append(
                    {
                        "removed_heading_chunk": str(current.get("id")),
                        "merged_into": str(following.get("id")),
                    }
                )
                result.append(merged)
                index += 2
                continue

        result.append(current)
        index += 1

    return result, merges


def resequence_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Recalcula chunk_sequence por procedimiento y content_type sin cambiar IDs.
    """
    counters: Counter[tuple[str, str]] = Counter()
    output: list[dict[str, Any]] = []

    for chunk in chunks:
        metadata = dict(chunk.get("metadata") or {})
        key = (
            str(chunk.get("parent_id") or ""),
            str(metadata.get("content_type") or ""),
        )
        counters[key] += 1
        metadata["chunk_sequence"] = counters[key]

        updated = dict(chunk)
        updated["metadata"] = metadata
        output.append(updated)

    return output


def find_duplicates(records: list[dict[str, Any]], key: str) -> list[str]:
    values = [str(record.get(key)) for record in records]
    return sorted(value for value, count in Counter(values).items() if count > 1)


def audit_integrity(
    procedures: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    procedure_ids = {str(p.get("id")) for p in procedures}
    missing_parent_refs = [
        str(c.get("id"))
        for c in chunks
        if str(c.get("parent_id")) not in procedure_ids
    ]

    empty_page_content = [
        str(c.get("id")) for c in chunks if not str(c.get("page_content") or "").strip()
    ]
    empty_raw_text = [
        str(c.get("id")) for c in chunks if not str(c.get("raw_text") or "").strip()
    ]

    heading_only = [
        str(c.get("id"))
        for c in chunks
        if is_heading_only(str(c.get("raw_text") or ""))
    ]

    short_chunks = [
        {
            "id": str(c.get("id")),
            "tokens": int((c.get("metadata") or {}).get("approx_tokens") or 0),
            "content_type": (c.get("metadata") or {}).get("content_type"),
            "raw_text": str(c.get("raw_text") or "")[:300],
        }
        for c in chunks
        if int((c.get("metadata") or {}).get("approx_tokens") or 0) < 50
    ]

    return {
        "procedure_count": len(procedures),
        "chunk_count": len(chunks),
        "duplicate_procedure_ids": find_duplicates(procedures, "id"),
        "duplicate_chunk_ids": find_duplicates(chunks, "id"),
        "missing_parent_references": missing_parent_refs,
        "empty_page_content": empty_page_content,
        "empty_raw_text": empty_raw_text,
        "heading_only_chunks_remaining": heading_only,
        "chunks_under_50_approx_tokens": short_chunks,
    }


def main() -> int:
    args = parse_args()
    input_dir: Path = args.input_dir.resolve()
    output_dir: Path = (
        args.output_dir.resolve()
        if args.output_dir
        else (input_dir / "rag_ready").resolve()
    )

    procedures_path = input_dir / "procedures.jsonl"
    chunks_path = input_dir / "chunks.jsonl"

    procedures = [apply_status_metadata(record) for record in read_jsonl(procedures_path)]
    chunks = [apply_status_metadata(record) for record in read_jsonl(chunks_path)]

    pre_audit = audit_integrity(procedures, chunks)

    if pre_audit["duplicate_procedure_ids"]:
        raise ValueError(
            f"Hay IDs de procedimientos duplicados: "
            f"{pre_audit['duplicate_procedure_ids'][:10]}"
        )
    if pre_audit["duplicate_chunk_ids"]:
        raise ValueError(
            f"Hay IDs de chunks duplicados: {pre_audit['duplicate_chunk_ids'][:10]}"
        )
    if pre_audit["missing_parent_references"]:
        raise ValueError(
            "Hay chunks con parent_id inexistente: "
            f"{pre_audit['missing_parent_references'][:10]}"
        )

    chunks, merges = merge_heading_only_chunks(chunks)
    chunks = resequence_chunks(chunks)

    active_procedures = [
        record for record in procedures if (record.get("metadata") or {}).get("is_active")
    ]
    inactive_procedures = [
        record for record in procedures if not (record.get("metadata") or {}).get("is_active")
    ]
    active_parent_ids = {str(record.get("id")) for record in active_procedures}

    active_chunks = [
        record
        for record in chunks
        if (record.get("metadata") or {}).get("is_active")
        and str(record.get("parent_id")) in active_parent_ids
    ]
    inactive_chunks = [
        record
        for record in chunks
        if not (record.get("metadata") or {}).get("is_active")
        or str(record.get("parent_id")) not in active_parent_ids
    ]

    ready_procedures = procedures if args.include_inactive else active_procedures
    ready_chunks = chunks if args.include_inactive else active_chunks

    post_audit = audit_integrity(ready_procedures, ready_chunks)

    output_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl(output_dir / "procedures_ready.jsonl", ready_procedures)
    write_jsonl(output_dir / "chunks_ready.jsonl", ready_chunks)
    write_jsonl(output_dir / "procedures_inactive.jsonl", inactive_procedures)
    write_jsonl(output_dir / "chunks_inactive.jsonl", inactive_chunks)

    report = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "configuration": {
            "include_inactive_in_ready": bool(args.include_inactive),
        },
        "before": pre_audit,
        "transformations": {
            "heading_only_merges": merges,
            "heading_only_merge_count": len(merges),
            "active_procedure_count": len(active_procedures),
            "inactive_procedure_count": len(inactive_procedures),
            "active_chunk_count": len(active_chunks),
            "inactive_chunk_count": len(inactive_chunks),
        },
        "after": post_audit,
        "recommended_index_file": str(output_dir / "chunks_ready.jsonl"),
    }

    with (output_dir / "postprocess_report.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    print("Postprocesamiento completado.")
    print(f"- Procedimientos listos: {len(ready_procedures)}")
    print(f"- Chunks listos: {len(ready_chunks)}")
    print(f"- Procedimientos inactivos separados: {len(inactive_procedures)}")
    print(f"- Chunks inactivos separados: {len(inactive_chunks)}")
    print(f"- Encabezados fusionados: {len(merges)}")
    print(f"- Archivo para indexar: {output_dir / 'chunks_ready.jsonl'}")
    print(f"- Reporte: {output_dir / 'postprocess_report.json'}")

    if post_audit["duplicate_chunk_ids"] or post_audit["missing_parent_references"]:
        print("ADVERTENCIA: la auditoría final encontró errores.", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())