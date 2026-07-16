# Pipeline de preprocesamiento del TUPA SUNAT para RAG

Este proyecto transforma los archivos antiguos de SUNAT:

- `tupa_consolidado.doc`: tabla consolidada con procedimientos, requisitos, costos, plazos, autoridades y recursos.
- `relacionTupa-2018.xls`: índice con la jerarquía oficial de secciones, categorías, procedimientos y subprocedimientos.

El resultado principal es un corpus JSONL estructurado y, opcionalmente, un conjunto de chunks semánticos listos para embeddings.

## Por qué se utiliza el archivo de índices

El índice es relevante porque permite:

- recuperar el nombre canónico de cada procedimiento;
- asignar su sección y categoría temática;
- identificar subprocedimientos oficiales como `1.1`, `1.2`, `1.3`, `178.1`, etc.;
- reconocer los cuatro servicios prestados en exclusividad, que en la tabla principal aparecen como `01`, `02`, `03` y `04` y podrían confundirse con los procedimientos tributarios 1–4;
- validar que ningún procedimiento se pierda durante la extracción.

El pipeline no considera cualquier numeral interno como subprocedimiento. Esto evita confundir fechas como `18.09.2004` o requisitos internos como `5.1` con jerarquía oficial del TUPA.

## Requisitos

- Python 3.10 o superior.
- LibreOffice instalado y con `soffice` disponible en el `PATH`.
- En Windows, el script también puede usar Microsoft Word/Excel como fallback cuando `pywin32` está instalado.

Instalación de dependencias Python:

```bash
python -m pip install -r requirements.txt
```

## Ejecución

### Windows

```bat
python tupa_pipeline.py ^
  --tupa-doc "tupa_consolidado.doc" ^
  --index-xls "relacionTupa-2018.xls" ^
  --output-dir "output_tupa" ^
  --target-tokens 450 ^
  --max-tokens 650 ^
  --debug-rows
```

También puede utilizarse:

```bat
run_pipeline.bat "tupa_consolidado.doc" "relacionTupa-2018.xls" "output_tupa"
```

### Linux o macOS

```bash
python tupa_pipeline.py \
  --tupa-doc tupa_consolidado.doc \
  --index-xls relacionTupa-2018.xls \
  --output-dir output_tupa \
  --target-tokens 450 \
  --max-tokens 650 \
  --debug-rows
```

O:

```bash
./run_pipeline.sh tupa_consolidado.doc relacionTupa-2018.xls output_tupa
```

La conversión del `.doc` consolidado puede tardar varios minutos. El límite predeterminado es de 1200 segundos. Puede aumentarse así:

```bash
python tupa_pipeline.py ... --conversion-timeout 1800
```

## Solo preprocesamiento estructural, sin chunking

```bash
python tupa_pipeline.py \
  --tupa-doc tupa_consolidado.doc \
  --index-xls relacionTupa-2018.xls \
  --output-dir output_tupa \
  --no-chunks
```

## Archivos generados

- `converted/tupa_consolidado.docx`: conversión del Word antiguo.
- `converted/relacionTupa-2018.xlsx`: conversión del Excel antiguo.
- `index_catalog.json`: índice jerárquico normalizado.
- `procedures.jsonl`: un documento padre por procedimiento o servicio.
- `chunks.jsonl`: chunks semánticos para el índice vectorial.
- `quality_report.json`: cobertura, campos vacíos, tamaños, duplicados y advertencias.
- `preview.md`: vista previa legible de los primeros procedimientos.
- `diagnostics_unknown_rows.csv`: filas con códigos no reconocidos.
- `diagnostics_orphan_rows.csv`: filas sin procedimiento padre.
- `debug_extracted_rows.jsonl`: filas originales por las 14 columnas, cuando se usa `--debug-rows`.

## Estructura de un documento padre

```json
{
  "id": "tupa_1",
  "code": "1",
  "title": "INSCRIPCIÓN EN EL REGISTRO ÚNICO DE CONTRIBUYENTES",
  "metadata": {
    "section": "SECCIÓN I - TRIBUTOS INTERNOS",
    "category": "REGISTRO ÚNICO DE CONTRIBUYENTES",
    "subprocedures": [
      {"code": "1.1", "title": "..."},
      {"code": "1.2", "title": "..."},
      {"code": "1.3", "title": "..."}
    ]
  },
  "fields": {
    "fundamento_legal": "...",
    "requisitos": "...",
    "formularios": "...",
    "costo": "GRATUITO",
    "calificacion": "Automático",
    "plazo": "...",
    "inicio_procedimiento": "...",
    "autoridad_competente": "...",
    "reconsideracion": "...",
    "reclamo": "...",
    "apelacion": "..."
  },
  "page_content": "..."
}
```

## Estrategia de chunking

El pipeline utiliza una estrategia jerárquica:

1. Conserva un documento padre por procedimiento.
2. Separa los campos principales: fundamento legal, requisitos, formularios, resumen administrativo, canal/autoridad y recursos.
3. Divide por subprocedimientos únicamente cuando aparecen en el índice oficial.
4. Reconoce encabezados como `REQUISITOS GENERALES`, `REQUISITOS ESPECÍFICOS`, `SUNAT VIRTUAL` y `NOTA`.
5. Empaca párrafos completos hasta el tamaño objetivo; no corta por una cantidad fija de caracteres.
6. Repite en cada chunk el código, nombre, sección, categoría y subprocedimiento aplicable.

El conteo de tokens es aproximado e independiente del modelo. Antes de producción puede reemplazarse por el tokenizer exacto del modelo de embeddings.

## Indexación RAG

Para cada línea de `chunks.jsonl`:

- vectorizar `page_content`;
- guardar `metadata` como filtros;
- conservar `parent_id` para recuperar el procedimiento padre cuando sea necesario;
- combinar búsqueda vectorial con búsqueda léxica/BM25 para códigos exactos, formularios, números de resolución y plazos.

## Validación realizada con los archivos adjuntos

La ejecución de prueba produjo:

- 199 entradas del índice: 193 procedimientos, 2 procedimientos con sufijo y 4 servicios;
- 28 subprocedimientos oficiales;
- 199 documentos padre recuperados;
- cobertura del 100 % respecto del índice;
- 0 códigos desconocidos;
- 0 filas huérfanas;
- 1419 chunks con la configuración 450/650;
- ningún chunk por encima de 650 tokens aproximados.

Los conteos pueden variar ligeramente si otra versión de LibreOffice cambia los saltos de párrafo, pero la cobertura de procedimientos debe mantenerse.

## Precaución sobre vigencia

El corpus corresponde al TUPA 2018. El sistema debe mostrar la versión y fecha de la fuente, y no presentar el contenido como normativa vigente sin contrastarlo con documentación actual de SUNAT.
