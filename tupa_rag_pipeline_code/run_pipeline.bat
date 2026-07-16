@echo off
set TUPA=%~1
set INDEX=%~2
set OUTPUT=%~3
if "%TUPA%"=="" set TUPA=tupa_consolidado.doc
if "%INDEX%"=="" set INDEX=relacionTupa-2018.xls
if "%OUTPUT%"=="" set OUTPUT=output_tupa

python tupa_pipeline.py ^
  --tupa-doc "%TUPA%" ^
  --index-xls "%INDEX%" ^
  --output-dir "%OUTPUT%" ^
  --target-tokens 450 ^
  --max-tokens 650 ^
  --debug-rows
