"""
Escudo de ciberseguridad y protección de datos personales.

Regla dura: si se detecta cualquier dato privado o credencial, el mensaje
NUNCA se envía al LLM. Se corta aquí y se devuelve un mensaje de
concientización basado en la Ley N.º 29733 (Ley de Protección de Datos
Personales, Perú).

Uso:
    veredicto = inspeccionar(texto)
    if veredicto.bloqueado:
        responder(veredicto.respuesta)   # el LLM ni se entera
    else:
        responder(rag(texto))
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

LEY_URL = "https://www.gob.pe/institucion/congreso-de-la-republica/normas-legales/243470-29733"


# --------------------------------------------------------------------------
# Normalización: derrota ofuscaciones tipo "c l a v e   s0l" o acentos raros
# --------------------------------------------------------------------------

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def _sin_tildes(texto: str) -> str:
    desc = unicodedata.normalize("NFD", texto)
    return "".join(c for c in desc if unicodedata.category(c) != "Mn")


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, sin leet y con espacios colapsados."""
    t = _sin_tildes(texto.lower()).translate(_LEET)
    return re.sub(r"\s+", " ", t)


def _luhn(numero: str) -> bool:
    digitos = [int(d) for d in numero][::-1]
    total = 0
    for i, d in enumerate(digitos):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# --------------------------------------------------------------------------
# Detectores
# --------------------------------------------------------------------------

# Palabras que indican que el usuario está por entregar (o ya entregó) un secreto.
_PALABRAS_CREDENCIAL = (
    r"clave\s*sol|clavesol|clave\s*de\s*acceso|mi\s*clave|contrasena|contrasenia|"
    r"password|passwd|pass|pin|codigo\s*de\s*verificacion|codigo\s*sms|otp|"
    r"token|usuario\s*y\s*clave|user\s*y\s*pass|credencial(?:es)?|"
    r"clave\s*(?:de\s*)?(?:mi\s*)?(?:cuenta|banco|tarjeta|celular)"
)

# "mi clave es X", "clave sol: X", "password = X" -> credencial revelada
_CREDENCIAL_CON_VALOR = re.compile(
    rf"\b(?:{_PALABRAS_CREDENCIAL})\b\s*(?:es|son|:|=|->|sera|seria)?\s*[\"'`]?([A-Za-z0-9@#$%^&*._\-]{{4,}})",
    re.IGNORECASE,
)

# Con \b: sin esto, "pin" matchearía dentro de "opina" y "pass" dentro de "repasar".
_CREDENCIAL_MENCION = re.compile(rf"\b(?:{_PALABRAS_CREDENCIAL})\b", re.IGNORECASE)

# Variante compacta: se evalúa sobre el texto sin NINGÚN espacio, para derrotar
# ofuscaciones tipo "c l a v e  s0l". Solo tokens de alta señal, porque al quitar
# los espacios se pierden los límites de palabra.
_CREDENCIAL_COMPACTA = re.compile(
    r"clavesol|contrasena|contrasenia|password|passwd|codigodeverificacion|claveseacceso",
    re.IGNORECASE,
)

# Usuario SOL: SUNAT usa RUC (11) + usuario alfanumérico. "mi usuario es ..."
_USUARIO_CON_VALOR = re.compile(
    r"(?:usuario|user|login|nick|cuenta)\s*(?:sol)?\s*(?:es|:|=)\s*[\"'`]?([A-Za-z0-9._\-]{3,})",
    re.IGNORECASE,
)

# DNI peruano: 8 dígitos exactos, aislados.
_DNI = re.compile(r"(?<!\d)\d{8}(?!\d)")

# RUC peruano: 11 dígitos empezando en 10/15/16/17/20.
_RUC = re.compile(r"(?<!\d)(?:10|15|16|17|20)\d{9}(?!\d)")

# Tarjeta: 13-19 dígitos con separadores opcionales.
_TARJETA = re.compile(r"(?<!\d)(?:\d[ \-]?){13,19}(?!\d)")

# CCI / cuenta bancaria: 20 dígitos (CCI) o cuenta de 13-14.
_CCI = re.compile(r"(?<!\d)\d{20}(?!\d)")

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Celular peruano: 9 dígitos empezando en 9 (con o sin +51).
_CELULAR = re.compile(r"(?<![\d+])(?:\+?51[\s\-]?)?9\d{2}[\s\-]?\d{3}[\s\-]?\d{3}(?!\d)")

# Datos sensibles del art. 2.5 de la Ley 29733.
_SENSIBLE = re.compile(
    r"\b(?:mi|mis)\s+(?:diagnostico|enfermedad|historia\s*clinica|vih|sida|"
    r"cancer|discapacidad|religion|orientacion\s*sexual|afiliacion\s*(?:politica|sindical)|"
    r"huella\s*(?:digital|dactilar)|datos\s*biometricos)\b",
    re.IGNORECASE,
)

_MOTIVOS = {
    "clave_sol": "tu Clave SOL o una contraseña",
    "credencial": "una credencial de acceso (clave, PIN, token o código de verificación)",
    "usuario": "un nombre de usuario de una cuenta",
    "dni": "un número de DNI",
    "ruc": "un número de RUC",
    "tarjeta": "un número de tarjeta",
    "cuenta_bancaria": "un número de cuenta bancaria o CCI",
    "email": "una dirección de correo electrónico personal",
    "celular": "un número de celular",
    "dato_sensible": "un dato personal sensible (salud, biometría, creencias o afiliación)",
}


@dataclass
class Veredicto:
    bloqueado: bool
    motivos: list[str] = field(default_factory=list)
    respuesta: str = ""

    @property
    def texto_seguro(self) -> str:
        """Texto apto para logs: jamás incluye el contenido original."""
        return f"BLOQUEADO[{','.join(self.motivos)}]" if self.bloqueado else "OK"


def _detectar(texto: str) -> list[str]:
    norm = _normalizar(texto)
    compacto = re.sub(r"[\s._\-]", "", norm)
    motivos: list[str] = []

    # 1) Credenciales. La sola mención de "clave sol" acompañada de un valor,
    #    o incluso la intención de compartirla, ya es motivo de bloqueo.
    if re.search(r"clave\s*sol|clavesol", norm) or "clavesol" in compacto:
        motivos.append("clave_sol")
    elif (_CREDENCIAL_CON_VALOR.search(norm)
          or _CREDENCIAL_MENCION.search(norm)
          or _CREDENCIAL_COMPACTA.search(compacto)):
        motivos.append("credencial")

    if _USUARIO_CON_VALOR.search(norm):
        motivos.append("usuario")

    # 2) Identificadores. Se evalúan sobre el texto original (los dígitos no
    #    cambian al normalizar, pero el leet sí ensuciaría los conteos).
    solo_digitos = re.sub(r"[ \-]", "", texto)

    if _RUC.search(solo_digitos):
        motivos.append("ruc")
    if _CCI.search(solo_digitos):
        motivos.append("cuenta_bancaria")

    for cand in _TARJETA.finditer(texto):
        limpio = re.sub(r"[ \-]", "", cand.group())
        if 13 <= len(limpio) <= 19 and _luhn(limpio):
            motivos.append("tarjeta")
            break

    if _CELULAR.search(texto):
        motivos.append("celular")

    # El DNI se revisa al final: 8 dígitos aislados que no sean parte de un
    # RUC/CCI ya detectado.
    if "ruc" not in motivos and "cuenta_bancaria" not in motivos and _DNI.search(solo_digitos):
        motivos.append("dni")

    if _EMAIL.search(texto):
        motivos.append("email")

    if _SENSIBLE.search(norm):
        motivos.append("dato_sensible")

    return list(dict.fromkeys(motivos))  # únicos, en orden


def _mensaje_concientizacion(motivos: list[str]) -> str:
    detalle = _MOTIVOS.get(motivos[0], "un dato personal") if motivos else "un dato personal"
    otros = [_MOTIVOS[m] for m in motivos[1:] if m in _MOTIVOS]
    extra = f" (y además {', '.join(otros)})" if otros else ""

    return (
        "🛡️ *Detuve tu mensaje para protegerte.*\n\n"
        f"Parece que ibas a compartir {detalle}{extra}. "
        "Por seguridad *no lo procesé ni lo envié a ningún sistema de inteligencia artificial*: "
        "tu mensaje se descartó aquí mismo.\n\n"
        "🔐 *Recuerda:*\n"
        "• La SUNAT *nunca* te va a pedir tu Clave SOL por chat, correo, llamada ni redes sociales.\n"
        "• Tu Clave SOL es personal e intransferible. Si crees que alguien la vio, "
        "cámbiala ahora mismo en SUNAT Virtual.\n"
        "• Nunca compartas DNI, RUC, tarjetas ni cuentas en canales que no sean oficiales.\n\n"
        "⚖️ *Tus datos están protegidos por ley.* La *Ley N.º 29733 – Ley de Protección de Datos "
        "Personales* reconoce que tus datos son tuyos: nadie puede tratarlos sin tu consentimiento "
        "libre, previo, expreso e informado, y tú puedes ejercer tus derechos ARCO "
        "(acceso, rectificación, cancelación y oposición).\n"
        f"📖 Conócela aquí: {LEY_URL}\n\n"
        "✅ *Puedes volver a preguntarme sin datos personales.* Por ejemplo:\n"
        "_«¿Qué requisitos necesito para inscribirme en el RUC?»_\n"
        "_«¿Cuánto demora la baja de inscripción en el RUC?»_"
    )


def inspeccionar(texto: str) -> Veredicto:
    """Punto de entrada único del escudo. Llamar ANTES de tocar el LLM."""
    if not texto or not texto.strip():
        return Veredicto(bloqueado=False)

    motivos = _detectar(texto)
    if not motivos:
        return Veredicto(bloqueado=False)

    return Veredicto(bloqueado=True, motivos=motivos, respuesta=_mensaje_concientizacion(motivos))


def es_seguro(texto: str) -> bool:
    """Atajo booleano."""
    return not inspeccionar(texto).bloqueado


if __name__ == "__main__":
    pruebas = [
        "¿Qué requisitos necesito para inscribirme en el RUC?",         # OK
        "Mi clave SOL es Peru2024$ ayúdame a entrar",                   # bloquea
        "mi c l a v e  s0l es abc123",                                  # bloquea (ofuscado)
        "Mi DNI es 45678912, ¿puedo tramitar?",                         # bloquea
        "Mi RUC 20512345678 tiene deuda",                               # bloquea
        "escríbeme a juan.perez@gmail.com",                             # bloquea
        "mi celular es 987654321",                                      # bloquea
        "¿Cuánto cuesta el procedimiento 1.2 del TUPA?",                # OK
        "Fue publicado el 18.09.2004 según el TUPA",                    # OK
        "¿Qué opina SUNAT sobre el pintado de comprobantes?",           # OK (no confundir 'pin')
        "Necesito repasar los requisitos del RUC",                      # OK (no confundir 'pass')
        "mi  c o n t r a s e ñ a  es hola123",                          # bloquea (ofuscado)
        "olvidé mi contraseña, ¿qué hago?",                             # bloquea (menciona credencial)
    ]
    for p in pruebas:
        v = inspeccionar(p)
        print(f"{'[BLOQUEA]' if v.bloqueado else '[  PASA ]'} {v.texto_seguro:<45} | {p}")
