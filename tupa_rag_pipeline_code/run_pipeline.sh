#!/usr/bin/env bash
set -euo pipefail

python tupa_pipeline.py \
  --tupa-doc "${1:-tupa_consolidado.doc}" \
  --index-xls "${2:-relacionTupa-2018.xls}" \
  --output-dir "${3:-output_tupa}" \
  --target-tokens 450 \
  --max-tokens 650 \
  --debug-rows
