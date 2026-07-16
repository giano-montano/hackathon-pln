# Asistente RAG de orientación sobre trámites SUNAT

Prototipo académico de PLN que responde consultas sobre procedimientos administrativos de SUNAT mediante RAG y Telegram. Incluye un pipeline para procesar el TUPA 2018, un scraper de páginas HTML oficiales, ChromaDB, OpenAI y un escudo local que bloquea credenciales y datos personales antes del LLM.

> No es un canal oficial de SUNAT. El corpus TUPA corresponde a 2018 y debe verificarse frente a fuentes oficiales actuales.

## Arquitectura

```text
Usuario -> Telegram -> Escudo local -> Recuperación ChromaDB -> OpenAI -> Respuesta
                                  ^
                                  |
          TUPA -> preprocesamiento -> chunks_ready.jsonl -> ingesta
          Web SUNAT -> scraper HTML -> chunks complementarios (integración pendiente)
```

## Componentes

- `bot/`: bot, RAG, ingesta y escudo de privacidad.
- `tupa_rag_pipeline_code/`: conversión, extracción, chunking y validación del TUPA.
- `sunat-scraper/`: descubrimiento, filtrado, extracción y exportación de páginas HTML.
- `docs/`: referencias y documentación.

## Instalación rápida en Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r .\bot\requirements.txt
pip install -r .\tupa_rag_pipeline_code\requirements.txt
pip install -e ".\sunat-scraper[dev]"
```

Crea `.env` en la raíz:

```env
OPENAI_API_KEY=tu_api_key
TELEGRAM_BOT=tu_token_de_telegram
```

## Ejecutar

El corpus listo ya se encuentra en `tupa_rag_pipeline_code/output_tupa/rag_ready/chunks_ready.jsonl`.

```powershell
python -m bot.ingest
python -m bot.main
```

## Regenerar el TUPA

```powershell
cd .\tupa_rag_pipeline_code
python .\tupa_pipeline.py --tupa-doc ".\tupa_consolidado.doc" --index-xls ".\relacionTupa-2018.xls" --output-dir ".\output_tupa" --target-tokens 450 --max-tokens 650 --debug-rows
python .\postprocess_tupa_rag.py --input-dir ".\output_tupa"
cd ..
```

## Scraper

```powershell
cd .\sunat-scraper
python -m sunat_scraper discover --dry-run
python -m sunat_scraper run --source orientacion --max-pages 150 --requests-per-second 1 --resume
python -m pytest -q
cd ..
```

## Resultados del corpus incluido

- 199 documentos padre y 100 % de cobertura del índice.
- 185 procedimientos activos y 1400 chunks listos para RAG.
- 0 códigos desconocidos, 0 filas huérfanas y 0 referencias padre rotas.

## Comandos del bot

- `/start`
- `/ayuda`
- `/privacidad`
- `/reiniciar`

## Documentación completa

Consulta `docs/DOCUMENTACION_TECNICA.md`.

Repositorio: https://github.com/giano-montano/hackathon-pln