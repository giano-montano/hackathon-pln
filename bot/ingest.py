"""Construye el índice vectorial. Ejecutar: python -m bot.ingest"""

from bot.rag import ingestar_jsonl

if __name__ == "__main__":
    print("🔧 Ingestando fuentes en ChromaDB…")
    ingestar_jsonl()
