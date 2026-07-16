# sunat-scraper

Scraper de contenido oficial **HTML** de SUNAT para construir un corpus simple de
un chatbot RAG tributario peruano.

```text
Fuentes configuradas -> Descubrimiento de URLs -> Filtrado -> Descarga de HTML
  -> Extraccion del contenido principal -> Limpieza -> Fragmentacion -> JSONL
```

El scraper acepta **multiples fuentes** y no esta acoplado a SUNAT: para agregar
otro sitio basta con anadir un bloque a `config/sources.yaml`.

## Principios

- **Fidelidad**: el texto guardado es una representacion fiel de lo visible en la
  pagina. No se inventa, no se completa, no se resume, no se reformula y no
  interviene ningun LLM.
- **Rechazar antes que aproximar**: si el contenido no se puede extraer con
  claridad, la pagina se rechaza con un motivo trazable.
- **Solo HTML**: no se descargan ni procesan PDFs, Word, Excel, imagenes, videos
  ni documentos escaneados. Sin OCR, sin Selenium, sin Playwright.
- **Trazabilidad**: cada documento, fragmento y FAQ conserva su URL de origen.

## Instalacion

Requiere Python 3.11 o superior.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -e ".[dev]"
```

## Configuracion

Toda la configuracion vive en [`config/sources.yaml`](config/sources.yaml).

```bash
cp .env.example .env    # opcional: User-Agent, timeout y velocidad
```

El `.env` **nunca** debe contener credenciales de SUNAT. El scraper solo lee
paginas publicas y no accede a zonas autenticadas.

### Descubrimiento de URLs: sitemap primero, enlaces como respaldo

La via principal es el **sitemap**: soporta `<urlset>`, `<sitemapindex>`, sitemaps
anidados y `.xml.gz`; si no da resultados se prueba `rss.xml`.

> **Hallazgo verificado el 2026-07-16.** Ni `orientacion.sunat.gob.pe` ni
> `emprender.sunat.gob.pe` publican sitemap: `/sitemap.xml` devuelve **404**, no hay
> linea `Sitemap:` en `robots.txt`, y el `rss.xml` de orientacion es un canal vacio.
> Son Drupal, pero sin el modulo XML Sitemap instalado.

Por eso existe un **recorrido de enlaces acotado** (`crawl_links: true`), que se
activa **solo** cuando no hay sitemap util. No tiene logica de ningun sitio
concreto: sigue enlaces y las decisiones las toman los mismos filtros del YAML.
En cuanto SUNAT publique un sitemap, el codigo lo prefiere sin tocar nada.

| Clave | Que hace |
|---|---|
| `crawl_links` | Activa el recorrido de enlaces si no hay sitemap util. |
| `crawl_start_urls` | Puntos de partida (por defecto, la portada del dominio). |
| `crawl_max_pages` | Maximo de paginas a **descargar** durante el descubrimiento. |
| `crawl_max_depth` | Profundidad maxima de saltos desde el inicio. |

Dos detalles que importan:

- **Registrar no cuesta una descarga.** Para decidir si una URL entra basta la URL,
  asi que un enlace aceptado se registra en cuanto se ve. En la practica, 6
  descargas bastan para descubrir ~120 URLs aceptadas.
- **Las ramas excluidas no se recorren.** Lo que cae en `exclude_patterns` no se
  visita ni se expande: aduanas, noticias o videos se podan de raiz. Solo se visita
  lo aceptado y lo que cayo en el rechazo por defecto (las paginas indice).
- El HTML que se descarga al descubrir **queda en cache**, y `crawl --resume` lo
  reutiliza: ninguna pagina se pide dos veces.

### Bloque `defaults`

| Clave | Que hace |
|---|---|
| `user_agent` | User-Agent identificable enviado en cada peticion. **Pon un contacto real.** |
| `timeout` | Segundos por peticion. |
| `requests_per_second` | Maximo de solicitudes por segundo. |
| `max_retries` | Reintentos con backoff exponencial. |
| `respect_robots` | Respeta `robots.txt`. |
| `min_text_chars` | Menos caracteres que esto -> `insufficient_text`. |
| `max_link_ratio` | Proporcion maxima de texto dentro de enlaces. |
| `chunk_min_words` / `chunk_max_words` / `chunk_overlap_words` | Reglas de fragmentacion. |

### Como agregar otra fuente

Anade un bloque a `sources`. **No hay que tocar el codigo.**

```yaml
sources:
  - name: mi_fuente                     # prefijo de los ids generados
    domain: ejemplo.gob.pe              # las URLs de otro dominio se descartan
    sitemap: https://ejemplo.gob.pe/sitemap.xml   # opcional
    default_audience: todas             # personas | empresas | emprendedor | todas
    default_topic: otros

    include_prefixes:  [/guias]         # la ruta empieza con...
    include_urls:      [/guia-rapida]   # ruta exacta
    include_patterns:  [preguntas-frecuentes]     # subcadena en la URL
    exclude_patterns:  [aduana, /videos/]
```

Si la fuente **no tiene sitemap**, usa una lista manual de URLs:

```yaml
  - name: sunat_www
    domain: www.sunat.gob.pe
    manual_urls:
      - https://www.sunat.gob.pe/orientacion/una-pagina.html
```

### Reglas de inclusion y exclusion

Para cada URL se evalua, **en este orden**, y gana la primera que coincide:

| # | Regla | Resultado |
|---|---|---|
| 1 | Extension `.pdf` | rechazo (`pdf`) |
| 2 | Otra extension no HTML (`.docx`, `.xlsx`, `.jpg`, `.mp4`, `.zip`...) | rechazo (`non_html`) |
| 3 | Dominio distinto al de la fuente | rechazo (`excluded_by_rule`) |
| 4 | Ruta de zona autenticada (`/login`...) | rechazo (`login`) |
| 5 | `global_exclude_patterns` + `exclude_patterns` de la fuente | rechazo (`excluded_by_rule`) |
| 6 | `include_urls` (ruta exacta) | **aceptacion** |
| 7 | `include_prefixes` | **aceptacion** |
| 8 | `include_patterns` | **aceptacion** |
| 9 | Nada coincidio | rechazo (`default_reject`) |

Las **exclusiones siempre ganan** a las inclusiones: `/ruc-personas/aduanas` se
rechaza aunque `/ruc-personas` este incluido por prefijo.

`global_exclude_patterns` bloquea en todas las fuentes: login, SUNAT Operaciones
en Linea, buscadores, noticias, eventos, videos y **datos dinamicos** (consulta
de RUC, deudas, estado de tramites, cronogramas).

### Reglas de clasificacion

El bloque `classification` define, sin LLM, como se asigna la audiencia y el tema
a partir del dominio, la URL, el titulo y el breadcrumb.

- **Audiencia** (`personas`, `empresas`, `emprendedor`, `todas`): gana la primera
  regla que coincida; si ninguna lo hace, se usa `audience_by_domain` y, en
  ultimo caso, el `default_audience` de la fuente.
- **Tema**: gana la regla con mas puntaje. Cada senal suma **una vez**:
  URL = 3, titulo = 2, breadcrumb = 1. Ante empate gana la que aparece primero en
  el YAML, por eso las reglas mas especificas (`recibos_honorarios`) van **antes**
  que las genericas (`comprobantes`).

## Extraccion

`trafilatura` es el extractor principal: detecta el contenido y devuelve XML
estructurado, que aqui solo se renderiza a texto. `BeautifulSoup` es el respaldo
cuando trafilatura no devuelve suficiente, y **toma el relevo** en dos estructuras
que trafilatura estropea:

1. **Preguntas frecuentes.** Los acordeones rompen el par pregunta/respuesta.
2. **Listas con numeracion propia.** trafilatura descarta `<ol start>` y
   `<li value>`, y reinicia toda lista en 1. En una pagina real de SUNAT eso
   convertia "2. Presencialmente" en "1. Presencialmente": un numero inventado,
   justo en las paginas de pasos. BeautifulSoup lee la numeracion del DOM.

Antes de extraer se eliminan menus, cabecera, pie, breadcrumbs, banners, modales,
cookies, redes, formularios, scripts, estilos y bloques de "mas popular" y
"relacionados". La limpieza **nunca** elimina `<html>`, `<body>`, `<main>` ni
`<article>`: Drupal marca el `<body>` con clases de maquetacion como
`layout-one-sidebar`, y filtrar por el token `sidebar` borraria la pagina entera.

## Uso

```bash
python -m sunat_scraper discover --dry-run     # ver que se recolectaria, sin escribir nada
python -m sunat_scraper discover               # sitemaps -> data/raw/urls.jsonl
python -m sunat_scraper crawl                  # descarga  -> data/raw/html/
python -m sunat_scraper process                # extrae    -> data/processed/*.jsonl
python -m sunat_scraper run                    # los tres pasos seguidos
```

Ejemplo tipico:

```bash
python -m sunat_scraper run \
  --source orientacion \
  --max-pages 150 \
  --requests-per-second 1 \
  --resume
```

Opciones principales:

| Opcion | Que hace |
|---|---|
| `--source` | Procesar una sola fuente. |
| `--max-pages` | Maximo de paginas **por fuente** (no global: con varias fuentes, un limite global se gastaria entero en la primera). |
| `--max-discovery-pages` | Paginas a descargar durante el recorrido de enlaces. |
| `--requests-per-second` | Limite de velocidad. |
| `--resume` | Reutiliza el HTML ya descargado. |
| `--config`, `--data-dir`, `--verbose` | Rutas y detalle del log. |

`--resume` reutiliza el HTML ya guardado en `data/raw/html/` y no vuelve a pedirlo,
asi que una ejecucion interrumpida continua donde quedo.

El `discover --dry-run` muestra las URLs descubiertas, las aceptadas, las
rechazadas, **la regla aplicada** a cada una y el conteo por fuente, por audiencia
y por tema, sin escribir ningun archivo.

## Salidas

| Archivo | Contenido |
|---|---|
| `data/raw/html/` | HTML original (cache y reanudacion). |
| `data/processed/documents.jsonl` | Una pagina por linea. |
| `data/processed/chunks.jsonl` | Fragmentos para RAG. |
| `data/processed/faqs.jsonl` | Preguntas frecuentes reales de la pagina. |
| `data/processed/rejected.jsonl` | Paginas descartadas y su motivo. |
| `data/reports/summary.json` | Reporte final de la ejecucion. |

### `documents.jsonl`

```json
{
  "id": "orientacion-ruc-personas",
  "url": "https://orientacion.sunat.gob.pe/ruc-personas",
  "title": "Registro Único de Contribuyentes",
  "audience": "personas",
  "topic": "ruc",
  "text": "Texto limpio completo de la página...",
  "collected_at": "2026-07-16T10:00:00-05:00"
}
```

Campos obligatorios: `id`, `url`, `title`, `audience`, `topic`, `text`,
`collected_at`. Opcionales (se omiten si estan vacios): `updated_at`, `source`,
`subtopic`.

El campo `text` es texto plano con marcas minimas:

| Marca | Significado |
|---|---|
| `## Titulo` | Encabezado de seccion |
| `- item` | Elemento de lista |
| `1. paso` | Paso de una lista ordenada |
| `col a \| col b` | Fila de tabla simple |
| `Pregunta:` / `Respuesta:` | Par de pregunta frecuente |

### `chunks.jsonl`

```json
{
  "id": "orientacion-ruc-personas-001",
  "document_id": "orientacion-ruc-personas",
  "text": "Fragmento textual...",
  "audience": "personas",
  "topic": "ruc",
  "url": "https://orientacion.sunat.gob.pe/ruc-personas"
}
```

Reglas: entre 250 y 450 palabras; solape maximo de 40 palabras; nunca se separa
una pregunta de su respuesta; nunca se corta una lista de pasos ni una tabla; se
prefiere cortar en un encabezado. Como la fragmentacion es **por documento**,
un fragmento jamas mezcla paginas, audiencias ni temas.

Dos consecuencias de priorizar la fidelidad sobre el tamano:

- El solape ocurre siempre en un limite de bloque, nunca a mitad de frase: por eso
  puede ser de 0 palabras cuando el bloque anterior es largo o indivisible.
- Un fragmento **puede superar las 450 palabras** si contiene un bloque
  indivisible (una lista de 60 pasos, un par pregunta/respuesta largo). Cuando las
  dos reglas chocan, gana "no cortar". Los parrafos corrientes si se parten por
  frases.

### `faqs.jsonl`

```json
{
  "question": "¿Cómo actualizo mi domicilio fiscal?",
  "answer": "Para actualizar el domicilio fiscal...",
  "audience": "personas",
  "topic": "ruc",
  "url": "https://..."
}
```

Solo se exportan preguntas que **existen realmente** en la pagina. Se detectan
cuatro patrones de marcado: `<dl>/<dt>/<dd>`, `<details>/<summary>`, acordeones
(Bootstrap y similares) y encabezados con forma de pregunta.

## Como revisar las paginas rechazadas

```bash
# Conteo por motivo
python -c "import json,collections;print(collections.Counter(json.loads(l)['reason'] for l in open('data/processed/rejected.jsonl',encoding='utf-8')))"

# Ver las que fallaron por poco texto
findstr insufficient_text data\processed\rejected.jsonl     # Windows
grep insufficient_text data/processed/rejected.jsonl        # Linux / macOS
```

`data/reports/summary.json` trae el desglose en `rejected_by_reason`.

| Motivo | Significado |
|---|---|
| `excluded_by_rule` | No paso los filtros de URL (incluye el rechazo por defecto). |
| `pdf` | La URL apunta a un PDF: no se descarga. |
| `non_html` | Formato no HTML (Word, Excel, imagen, video, ZIP...). |
| `external_document_only` | La pagina solo enlaza a un documento externo, o su contenido esta incrustado en un visor/iframe, sin explicacion textual suficiente. |
| `insufficient_text` | Menos de `min_text_chars`, o es basicamente una lista de enlaces. |
| `complex_table` | El contenido principal es una tabla con celdas combinadas o dependiente del formato visual. |
| `captcha` | La respuesta es un captcha. No se evaden captchas. |
| `login` | La pagina exige autenticacion. |
| `duplicate` | URL canonica repetida, o texto identico a otro documento. |
| `download_error` | Fallo de red, timeout o estado HTTP de error. |
| `extraction_error` | La extraccion salio fragmentada, con menu mezclado, o el servidor devolvio un error. |

Si un motivo aparece demasiado, ajusta las reglas: `insufficient_text` masivo
suele indicar que falta afinar `min_text_chars` o los selectores de limpieza;
`excluded_by_rule` masivo es normal (los sitemaps traen mucho fuera de alcance).

## Deduplicacion

Dos capas, deliberadamente simples:

1. **URL canonica**: sin fragmento, sin parametros de tracking, host en
   minusculas y sin barra final.
2. **Hash exacto** (SHA-256) del texto limpio.

No hay MinHash, SimHash ni embeddings en esta version.

## Datos dinamicos y consulta de RUC

El corpus **no** debe usarse para responder sobre datos que cambian: estado de un
RUC, razon social, condicion de habido, deudas, estado de tramites, cronogramas,
fechas de vencimiento, UIT, tasas o intereses vigentes.

Esos datos se resuelven **en tiempo de consulta** con una API externa. El contrato
esta en [`src/sunat_scraper/ruc_provider.py`](src/sunat_scraper/ruc_provider.py):

```python
from typing import Protocol


class RucProvider(Protocol):
    def get(self, ruc: str) -> dict:
        ...
```

Para conectar la API real mas adelante, implementa ese protocolo y usalo desde el
chatbot; el scraper no necesita cambios:

```python
class ApiRucProvider:
    def __init__(self, base_url: str, token: str):
        self.base_url, self.token = base_url, token

    def get(self, ruc: str) -> dict:
        # httpx.get(f"{self.base_url}/{ruc}", headers={"Authorization": ...}).json()
        ...
```

Mientras tanto hay un `MockRucProvider` con datos fijos para pruebas. La
integracion real no forma parte de esta version.

## Pruebas

```bash
pytest
```

Las pruebas usan fixtures HTML y XML locales (`tests/fixtures/`) y **no dependen
de internet**.

## Etica y limites de uso

- Se respeta `robots.txt` (`respect_robots: true`).
- User-Agent identificable con contacto. **Cambialo por uno tuyo.**
- Limite de solicitudes por segundo y pausa entre peticiones.
- No se evaden captchas.
- No se accede a zonas autenticadas ni a SUNAT Operaciones en Linea.
- No se solicita ni se almacena la Clave SOL.

Solo se recolecta contenido publico y oficial. Verifica los terminos de uso del
portal antes de una ejecucion grande.

## Limitaciones conocidas

- **SUNAT no publica sitemap** (verificado el 2026-07-16). El descubrimiento
  depende del recorrido de enlaces, que solo encuentra lo que este enlazado desde
  la portada dentro de `crawl_max_depth` saltos. Una pagina huerfana no se
  descubre: agregala con `manual_urls`.
- **Las paginas indice se rechazan**: `/ruc-personas` o `/comprobantes-de-pago` son
  menus de enlaces sin prosa, y salen como `insufficient_text`. Es lo correcto (el
  contenido esta en las paginas hijas), pero infla el conteo de rechazos.
- **Sin JavaScript**: el contenido que se pinta en el navegador no se ve. Esas
  paginas se rechazan (`insufficient_text` / `external_document_only`) en vez de
  extraerse a medias.
- **Sin PDFs**: buena parte de la normativa de SUNAT vive en PDF y queda fuera del
  corpus por diseno. Una pagina que solo enlaza un PDF se rechaza como
  `external_document_only`.
- **Tablas complejas**: las de celdas combinadas se descartan. Si la tabla era el
  contenido principal, se pierde la pagina (`complex_table`). Es intencional:
  preferimos perderla antes que inventar su lectura.
- **Datos volatiles**: el texto que menciona UIT, tasas o plazos **se conserva tal
  cual** (la fidelidad manda), pero envejece. No construyas respuestas sobre esos
  numeros: usa la API. Las paginas dedicadas a datos dinamicos si se excluyen por
  URL.
- **Clasificacion por reglas**: rapida y auditable, pero se equivoca en paginas
  ambiguas. Una pagina tiene una sola audiencia y un solo tema; el contenido mixto
  queda forzado a una etiqueta. Ajusta `classification` en el YAML.
- **Deteccion de FAQs por marcado**: si una pagina escribe las preguntas sin
  estructura reconocible, quedan dentro de `text` pero no en `faqs.jsonl`.
- **`## `, `- ` y ` | `** son marcas anadidas al texto plano. Si tu pipeline de
  embeddings las estorba, quitalas al indexar.
- **Los sitemaps de SUNAT mandan**: si una pagina util no esta en el sitemap, no se
  descubre. Agregala con `manual_urls`.
