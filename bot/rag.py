"""
Núcleo RAG: ChromaDB persistente + embeddings y chat de OpenAI.

Diseñado para crecer: `ingestar_jsonl()` acepta cualquier archivo con el
formato {id, page_content, metadata} — cuando lleguen las nuevas fuentes,
solo se agregan más rutas a FUENTES.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from rank_bm25 import BM25Okapi

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
CHROMA_DIR = str(RAIZ / "chroma_db")
COLECCION = "tupa_sunat"

MODELO_EMBED = "text-embedding-3-small"
MODELO_CHAT = "gpt-4o-mini"
TOP_K = 6

# Fuentes a ingestar. Agrega aquí los JSONL de las nuevas fuentes.
FUENTES = [
    # Unificado por el equipo: TUPA 2018 + orientación SUNAT scrapeada.
    RAIZ / "tupa_rag_pipeline_code" / "output_tupa" / "rag_ready" / "chunks_ready_unified.jsonl",
]

_openai = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
_chroma = chromadb.PersistentClient(path=CHROMA_DIR)

SYSTEM_PROMPT = """# ROL
Eres el asistente virtual tributario de la SUNAT (Perú). Atiendes por Telegram a contribuyentes \
que consultan sobre trámites, comprobantes de pago, RUC, declaraciones, impuestos y demás \
procedimientos tributarios.

# PROBLEMA QUE RESUELVES
Cada día miles de contribuyentes hacen consultas repetitivas que hoy los obligan a navegar por \
documentación extensa y normativa compleja (el TUPA tiene cientos de procedimientos). Tu trabajo \
es darles, en segundos y en lenguaje simple, la respuesta que tardarían en encontrar leyendo \
resoluciones. Traduce la norma; no la copies.

# CÓMO RESPONDES
1. Responde SOLO con la información del CONTEXTO recuperado. Si no está ahí, dilo con claridad y \
deriva a sunat.gob.pe o a la central 0-801-12-100. Nunca inventes requisitos, plazos, costos, \
formularios ni códigos de procedimiento.
2. Cita el código y nombre del procedimiento TUPA en el que te basas.
3. Español claro y directo, sin jerga legal innecesaria. Viñetas para requisitos y pasos.
4. Ve al grano: el ciudadano quiere saber qué hacer, dónde y con qué. Máximo ~1500 caracteres \
(es un chat de Telegram).
5. Si hay PERFIL DEL CONTRIBUYENTE, personaliza con él (su régimen, actividad, estado) pero solo \
si es pertinente a lo que pregunta. No se lo recites si no viene al caso.

# SEGURIDAD (INNEGOCIABLE)
JAMÁS pidas ni aceptes la Clave SOL, contraseñas, PIN, códigos de verificación, DNI, tarjetas ni \
cuentas bancarias. Si un trámite los requiere, explica que esos datos se ingresan ÚNICAMENTE en \
los canales oficiales de SUNAT, nunca en este chat. Recuerda que SUNAT jamás pide la Clave SOL \
por chat, correo ni teléfono."""


def _embed(textos: list[str]) -> list[list[float]]:
    resp = _openai.embeddings.create(model=MODELO_EMBED, input=textos)
    return [d.embedding for d in resp.data]


def _tokenizar(texto: str) -> list[str]:
    return re.findall(r"[a-záéíóúñü0-9\.]+", texto.lower())


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


_bm25_cache: tuple[BM25Okapi, list[dict]] | None = None


def _bm25():
    """Índice BM25 sobre el mismo corpus, cargado desde Chroma una sola vez."""
    global _bm25_cache
    if _bm25_cache is None:
        datos = _coleccion().get(include=["documents", "metadatas"])
        corpus = [
            {"texto": d, "metadata": m}
            for d, m in zip(datos["documents"], datos["metadatas"])
        ]
        _bm25_cache = (BM25Okapi([_tokenizar(c["texto"]) for c in corpus]), corpus)
    return _bm25_cache


def recuperar(pregunta: str, k: int = TOP_K) -> list[dict]:
    """Recuperación híbrida: densa (ONNX) + léxica (BM25), fusionadas con RRF.

    RRF (Reciprocal Rank Fusion) combina ambos rankings sin tener que
    normalizar puntajes de escalas distintas.
    """
    puntajes: dict[str, float] = {}
    docs: dict[str, dict] = {}
    C = 60  # constante estándar de RRF

    # Rama densa
    res = _coleccion().query(
        query_embeddings=_embed([pregunta]),
        n_results=k * 3,
        include=["documents", "metadatas"],
    )
    for rank, (d, m) in enumerate(zip(res["documents"][0], res["metadatas"][0])):
        puntajes[d] = puntajes.get(d, 0) + 1 / (C + rank)
        docs[d] = {"texto": d, "metadata": m}

    # Rama léxica
    bm25, corpus = _bm25()
    tokens = _tokenizar(pregunta)
    if tokens:
        scores = bm25.get_scores(tokens)  # una sola vez: es O(corpus) por llamada
        mejores = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)[: k * 3]
        for rank, i in enumerate(mejores):
            d = corpus[i]["texto"]
            puntajes[d] = puntajes.get(d, 0) + 1 / (C + rank)
            docs.setdefault(d, corpus[i])

    orden = sorted(puntajes, key=puntajes.get, reverse=True)[:k]
    return [{**docs[d], "score": puntajes[d]} for d in orden]


def _formatear_contexto(hits: list[dict]) -> str:
    partes = []
    for i, h in enumerate(hits, 1):
        m = h["metadata"]
        cab = f"[Fuente {i}] Procedimiento {m.get('codigo_tupa', '?')} — {m.get('procedimiento', '')}"
        if m.get("content_label"):
            cab += f" | {m['content_label']}"
        partes.append(f"{cab}\n{h['texto']}")
    return "\n\n---\n\n".join(partes)


def responder(
    pregunta: str,
    historial: list[dict] | None = None,
    perfil_kyb: str | None = None,
) -> str:
    """Pregunta -> respuesta con citas. Asume que el escudo ya aprobó el texto.

    `perfil_kyb` es el resumen del 8B (ver bot/latinfo.py); se inyecta en cada
    turno para personalizar sin re-consultar la API.
    """
    hits = recuperar(pregunta)
    if not hits:
        return ("No encontré información sobre eso en el TUPA de SUNAT. "
                "Te sugiero revisar sunat.gob.pe o llamar al 0-801-12-100.")

    bloques = []
    if perfil_kyb:
        bloques.append(f"PERFIL DEL CONTRIBUYENTE (datos públicos SUNAT/OSCE/OEFA/SEACE):\n{perfil_kyb}")
    bloques.append(f"CONTEXTO RECUPERADO DEL TUPA:\n{_formatear_contexto(hits)}")
    bloques.append(f"PREGUNTA DEL CIUDADANO: {pregunta}")

    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensajes += (historial or [])[-4:]
    mensajes.append({"role": "user", "content": "\n\n".join(bloques)})

    r = _openai.chat.completions.create(
        model=MODELO_CHAT, messages=mensajes, temperature=0.2, max_tokens=700
    )
    return r.choices[0].message.content.strip()


if __name__ == "__main__":
    ingestar_jsonl()
