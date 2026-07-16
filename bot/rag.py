"""
Núcleo RAG: ChromaDB persistente + embeddings y chat de OpenAI.

Diseñado para crecer: `ingestar_jsonl()` acepta cualquier archivo con el
formato {id, page_content, metadata} — cuando lleguen las nuevas fuentes,
solo se agregan más rutas a FUENTES.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
CHROMA_DIR = str(RAIZ / "chroma_db")
COLECCION = "tupa_sunat"

MODELO_EMBED = "text-embedding-3-small"
MODELO_CHAT = "gpt-4o-mini"
TOP_K = 6

# Fuentes a ingestar. Agrega aquí los JSONL de las nuevas fuentes.
FUENTES = [
    RAIZ / "tupa_rag_pipeline_code" / "output_tupa" / "rag_ready" / "chunks_ready.jsonl",
]

_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_chroma = chromadb.PersistentClient(path=CHROMA_DIR)

SYSTEM_PROMPT = """Eres un asistente virtual que orienta a ciudadanos peruanos sobre los \
procedimientos administrativos del TUPA de la SUNAT (Texto Único de Procedimientos Administrativos, 2018).

Reglas:
1. Responde SOLO con la información del CONTEXTO. Si no está ahí, di claramente que no lo \
encuentras en el TUPA y sugiere consultar sunat.gob.pe o la central 0-801-12-100.
2. Nunca inventes requisitos, plazos, costos ni códigos de procedimiento.
3. Cita siempre el código y nombre del procedimiento en el que te basas.
4. Responde en español, claro y breve, con viñetas cuando haya requisitos o pasos.
5. JAMÁS pidas al usuario su Clave SOL, contraseña, DNI, RUC, tarjeta ni ningún dato personal. \
Si lo necesitara para un trámite, explica que ese dato se ingresa únicamente en los canales \
oficiales de SUNAT, nunca en este chat.
6. Máximo ~1500 caracteres (es un chat de Telegram)."""


def _embed(textos: list[str]) -> list[list[float]]:
    resp = _openai.embeddings.create(model=MODELO_EMBED, input=textos)
    return [d.embedding for d in resp.data]


def _plano(metadata: dict) -> dict:
    """Chroma solo acepta str/int/float/bool en metadata."""
    salida = {}
    for k, v in metadata.items():
        if v is None:
            continue
        salida[k] = v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)
    return salida


def ingestar_jsonl(rutas: list[Path] | None = None, lote: int = 100) -> int:
    """(Re)construye la colección desde los JSONL. Idempotente."""
    rutas = rutas or FUENTES
    try:
        _chroma.delete_collection(COLECCION)
    except Exception:
        pass
    col = _chroma.create_collection(COLECCION, metadata={"hnsw:space": "cosine"})

    total = 0
    for ruta in rutas:
        if not ruta.exists():
            print(f"  ⚠️  No encontrado, se omite: {ruta}")
            continue

        registros = [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"  📄 {ruta.name}: {len(registros)} chunks")

        for i in range(0, len(registros), lote):
            trozo = registros[i : i + lote]
            docs = [r["page_content"] for r in trozo]
            col.add(
                ids=[r["id"] for r in trozo],
                documents=docs,
                embeddings=_embed(docs),
                metadatas=[_plano(r.get("metadata", {})) for r in trozo],
            )
            total += len(trozo)
            print(f"     {total} embebidos…", end="\r")

    print(f"\n✅ {total} chunks en ChromaDB → {CHROMA_DIR}")
    return total


def _coleccion():
    return _chroma.get_collection(COLECCION)


def recuperar(pregunta: str, k: int = TOP_K) -> list[dict]:
    res = _coleccion().query(
        query_embeddings=_embed([pregunta]),
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"texto": d, "metadata": m, "distancia": dist}
        for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])
    ]


def _formatear_contexto(hits: list[dict]) -> str:
    partes = []
    for i, h in enumerate(hits, 1):
        m = h["metadata"]
        cab = f"[Fuente {i}] Procedimiento {m.get('codigo_tupa', '?')} — {m.get('procedimiento', '')}"
        if m.get("content_label"):
            cab += f" | {m['content_label']}"
        partes.append(f"{cab}\n{h['texto']}")
    return "\n\n---\n\n".join(partes)


def responder(pregunta: str, historial: list[dict] | None = None) -> str:
    """Pregunta -> respuesta con citas. Asume que el escudo ya aprobó el texto."""
    hits = recuperar(pregunta)
    if not hits:
        return ("No encontré información sobre eso en el TUPA de SUNAT. "
                "Te sugiero revisar sunat.gob.pe o llamar al 0-801-12-100.")

    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensajes += (historial or [])[-4:]
    mensajes.append({
        "role": "user",
        "content": f"CONTEXTO:\n{_formatear_contexto(hits)}\n\nPREGUNTA DEL CIUDADANO: {pregunta}",
    })

    r = _openai.chat.completions.create(
        model=MODELO_CHAT, messages=mensajes, temperature=0.2, max_tokens=700
    )
    return r.choices[0].message.content.strip()


if __name__ == "__main__":
    ingestar_jsonl()
