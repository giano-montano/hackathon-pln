"""
Perfil KYB del contribuyente: Latinfo -> resumen con Llama 3.1 8B (NVIDIA).

El endpoint /pe/kyb devuelve demasiado texto (SUNAT + OSCE sancionados +
multas + penalidades + OEFA + SEACE) para inyectarlo entero en cada prompt,
así que un modelo 8B barato lo comprime UNA sola vez por sesión y el resumen
se reutiliza en cada turno del chat.
"""

from __future__ import annotations

import json
import os

import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

LATINFO_URL = "https://api.latinfo.dev/pe/kyb/{ruc}"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
MODELO_8B = os.getenv("CHIQUI_NVIDIA_LLM_MODEL", "meta/llama-3.1-8b-instruct")

# Cloudflare responde 403 (error 1010) al User-Agent por defecto de las
# librerías HTTP; con un UA de navegador pasa sin problema.
_HEADERS_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"}

_nvidia = OpenAI(base_url=NVIDIA_BASE, api_key=os.getenv("NVIDIA_API_KEY"))

PROMPT_RESUMEN = """Eres un analista tributario. Resume el siguiente perfil KYB de una empresa peruana \
(datos públicos de SUNAT, OSCE, OEFA y SEACE) en MÁXIMO 12 líneas, en español, con viñetas.

Incluye solo lo que sirva para orientar trámites ante SUNAT:
- Razón social, RUC, estado y condición del contribuyente.
- Tipo de contribuyente, actividad económica principal, distrito, N.º de trabajadores.
- Sanciones, multas o penalidades vigentes (OSCE/OEFA), si las hay: indica cuántas y lo más reciente.
- Si participa en contrataciones del Estado (SEACE).

No inventes nada. Si un dato no está, omítelo. Sin preámbulos: empieza directo con las viñetas.

PERFIL KYB (JSON):
{datos}"""


def consultar_kyb(ruc: str, timeout: int = 45) -> dict | None:
    """Consulta el KYB completo. Devuelve None si falla."""
    token = os.getenv("LATINFO_API_KEY")
    if not token:
        return None
    try:
        r = requests.get(
            LATINFO_URL.format(ruc=ruc),
            headers={"Authorization": f"Bearer {token}", **_HEADERS_UA},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def resumir_kyb(datos: dict, max_chars: int = 12000) -> str:
    """Comprime el KYB con el 8B. Ante cualquier fallo, cae a un resumen mínimo."""
    crudo = json.dumps(datos, ensure_ascii=False)[:max_chars]
    try:
        r = _nvidia.chat.completions.create(
            model=MODELO_8B,
            messages=[{"role": "user", "content": PROMPT_RESUMEN.format(datos=crudo)}],
            temperature=0.2,
            max_tokens=500,
        )
        return r.choices[0].message.content.strip()
    except Exception:
        return _resumen_fallback(datos)


def _resumen_fallback(datos: dict) -> str:
    """Si el 8B no responde, arma un resumen determinista con los campos clave."""
    ident = datos.get("identity") or {}
    act = datos.get("activity") or {}
    partes = [
        f"- Razón social: {ident.get('razon_social', 'N/D')} (RUC {datos.get('ruc', 'N/D')})",
        f"- Estado: {ident.get('estado', 'N/D')} | Condición: {ident.get('condicion', 'N/D')}",
        f"- Tipo: {act.get('tipo_contribuyente', 'N/D')} | Actividad: {act.get('ciiu_principal', 'N/D')}",
        f"- Distrito: {act.get('distrito', 'N/D')} | Trabajadores: {act.get('nro_trabajadores', 'N/D')}",
    ]
    sanc = datos.get("sanctions") or {}
    if any(sanc.values()):
        partes.append("- Registra sanciones/multas en fuentes públicas (OSCE/OEFA).")
    return "\n".join(partes)


def perfil_de_ruc(ruc: str) -> tuple[str | None, str | None]:
    """RUC -> (razon_social, resumen). (None, None) si no se pudo obtener."""
    datos = consultar_kyb(ruc)
    if not datos:
        return None, None
    razon = ((datos.get("identity") or {}).get("razon_social")) or f"RUC {ruc}"
    return razon, resumir_kyb(datos)


def ruc_valido(ruc: str) -> bool:
    """Valida un RUC peruano con su dígito verificador (módulo 11)."""
    ruc = ruc.strip()
    if len(ruc) != 11 or not ruc.isdigit() or ruc[:2] not in {"10", "15", "16", "17", "20"}:
        return False
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(d) * p for d, p in zip(ruc[:10], pesos))
    resto = 11 - (suma % 11)
    return int(ruc[10]) == {10: 0, 11: 1}.get(resto, resto)


if __name__ == "__main__":
    print("ruc_valido(20100362598):", ruc_valido("20100362598"))
    print("ruc_valido(20100362590):", ruc_valido("20100362590"))
    razon, resumen = perfil_de_ruc("20100362598")
    print("\nRAZON:", razon)
    print("\nRESUMEN 8B:\n", resumen)
