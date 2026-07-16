"""
Bot de Telegram (long polling).

Flujo:
  /start -> pide RUC -> Latinfo KYB -> resumen con Llama 3.1 8B (NVIDIA)
         -> a partir de ahí: mensaje -> ESCUDO -> RAG -> gpt-4o-mini -> respuesta

El escudo es la primera puerta de cada mensaje: si bloquea, el LLM nunca ve el texto.

Ejecutar:
    python -m bot.ingest      # una sola vez (construye el índice)
    python -m bot.main        # levanta el bot
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot import latinfo, rag, shield

load_dotenv(override=True)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

TOKEN = os.getenv("TELEGRAM_BOT")


@dataclass
class Sesion:
    esperando_ruc: bool = True
    ruc: str | None = None
    razon_social: str | None = None
    perfil_kyb: str | None = None          # resumen del 8B, inyectado en cada prompt
    historial: list[dict] = field(default_factory=list)


# En memoria: se pierde al reiniciar. Suficiente para la demo y mejor para privacidad.
_sesiones: dict[int, Sesion] = {}


def _sesion(chat_id: int) -> Sesion:
    return _sesiones.setdefault(chat_id, Sesion())


BIENVENIDA = (
    "👋 ¡Hola! Soy tu asistente virtual del *TUPA de la SUNAT*.\n\n"
    "Te respondo en segundos sobre trámites, comprobantes de pago, RUC, declaraciones e "
    "impuestos, sin que tengas que leer normativa extensa.\n\n"
    "🏢 *Para empezar, envíame tu RUC* (11 dígitos) y así personalizo mis respuestas con la "
    "información pública de tu empresa.\n\n"
    "🛡️ *Nunca* me envíes tu Clave SOL, contraseña, DNI ni tarjetas: si detecto ese tipo de "
    "datos, detengo el mensaje y no lo proceso. Estás protegido por la *Ley N.º 29733*."
)

AYUDA = (
    "*Comandos*\n"
    "/start – empezar de nuevo\n"
    "/ayuda – este mensaje\n"
    "/privacidad – cómo protejo tus datos\n"
    "/ruc – cambiar el RUC de la sesión\n"
    "/reiniciar – borrar historial y datos de esta sesión\n\n"
    "Luego solo escríbeme tu consulta en lenguaje natural. Por ejemplo:\n"
    "• ¿Qué requisitos necesito para inscribirme en el RUC?\n"
    "• ¿Cómo doy de baja mi RUC?\n"
    "• ¿Cuánto demora la autorización de impresión de comprobantes?"
)

PRIVACIDAD = (
    "🛡️ *Cómo protejo tus datos*\n\n"
    "• Cada mensaje pasa por un filtro local que detecta Clave SOL, contraseñas, PIN, DNI, "
    "tarjetas, cuentas, correos, celulares y datos sensibles.\n"
    "• Si detecto alguno, *el mensaje se descarta y jamás llega al modelo de IA*.\n"
    "• Del RUC solo uso información *pública* (SUNAT, OSCE, OEFA, SEACE) para orientarte mejor.\n"
    "• No guardo nada en disco: tu sesión vive en memoria y se borra con /reiniciar.\n\n"
    "⚖️ La *Ley N.º 29733 – Ley de Protección de Datos Personales* garantiza que tus datos son "
    "tuyos y que puedes ejercer tus derechos ARCO (acceso, rectificación, cancelación y oposición).\n"
    f"📖 {shield.LEY_URL}\n\n"
    "🔐 Recuerda: *SUNAT nunca te pedirá tu Clave SOL* por chat, correo o teléfono."
)


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _sesiones[update.effective_chat.id] = Sesion()
    await update.message.reply_text(BIENVENIDA, parse_mode=ParseMode.MARKDOWN)


async def cmd_ayuda(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(AYUDA, parse_mode=ParseMode.MARKDOWN)


async def cmd_privacidad(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(PRIVACIDAD, parse_mode=ParseMode.MARKDOWN,
                                    disable_web_page_preview=True)


async def cmd_ruc(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    s = _sesion(update.effective_chat.id)
    s.esperando_ruc = True
    await update.message.reply_text("🏢 Envíame el RUC (11 dígitos) que quieres consultar.")


async def cmd_reiniciar(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _sesiones.pop(update.effective_chat.id, None)
    await update.message.reply_text(
        "🧹 Listo, borré tu historial y los datos de la sesión. Usa /start para empezar de nuevo."
    )


async def on_mensaje(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    texto = update.message.text or ""
    s = _sesion(chat_id)

    # ---------- PUERTA 1: ESCUDO. Nada pasa de aquí sin aprobación. ----------
    veredicto = shield.inspeccionar(texto)
    if veredicto.bloqueado:
        log.warning("chat=%s %s", chat_id, veredicto.texto_seguro)  # se loguea el motivo, NUNCA el texto
        await update.message.reply_text(veredicto.respuesta, parse_mode=ParseMode.MARKDOWN,
                                        disable_web_page_preview=True)
        return

    # ---------- PASO 0: onboarding por RUC ----------
    if s.esperando_ruc:
        candidato = re.sub(r"\D", "", texto)
        if not latinfo.ruc_valido(candidato):
            await update.message.reply_text(
                "🤔 Ese no parece un RUC válido. Debe tener *11 dígitos* y empezar en 10, 15, 16, 17 o 20.\n"
                "Ejemplo: `20100362598`\n\nEnvíamelo de nuevo, por favor.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.chat.send_action(ChatAction.TYPING)
        aviso = await update.message.reply_text("🔎 Consultando información pública del RUC…")

        razon, resumen = await asyncio.to_thread(latinfo.perfil_de_ruc, candidato)

        if not razon:
            await aviso.edit_text(
                "⚠️ No pude consultar ese RUC ahora mismo. Igual puedo ayudarte:\n"
                "hazme tu consulta sobre trámites de SUNAT y te respondo con el TUPA."
            )
            s.esperando_ruc = False   # no bloqueamos al usuario por una API caída
            return

        s.ruc, s.razon_social, s.perfil_kyb, s.esperando_ruc = candidato, razon, resumen, False
        log.info("chat=%s RUC registrado: %s", chat_id, razon)

        # Sin parse_mode: el resumen lo genera un LLM y sus asteriscos sueltos
        # rompen el parser de Telegram (BadRequest: can't parse entities).
        await aviso.edit_text(
            f"✅ {razon}\n\n{resumen}\n\n"
            "Ya puedes preguntarme lo que necesites sobre tus trámites en SUNAT. 👇"
        )
        return

    # ---------- PUERTA 2: RAG ----------
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        respuesta = await asyncio.to_thread(rag.responder, texto, s.historial, s.perfil_kyb)
    except Exception:
        log.exception("Fallo al responder chat=%s", chat_id)
        await update.message.reply_text(
            "😕 Tuve un problema procesando tu consulta. Inténtalo de nuevo en unos segundos."
        )
        return

    s.historial.extend([
        {"role": "user", "content": texto},
        {"role": "assistant", "content": respuesta},
    ])
    s.historial = s.historial[-8:]

    await update.message.reply_text(respuesta)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sin esto, cualquier excepción deja al usuario esperando para siempre."""
    log.exception("Excepción no manejada", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "😕 Algo falló de mi lado. Vuelve a intentarlo, por favor."
            )
        except Exception:
            pass


def main() -> None:
    if not TOKEN:
        raise SystemExit("Falta TELEGRAM_BOT en el .env")

    app = Application.builder().token(TOKEN).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("help", cmd_ayuda))
    app.add_handler(CommandHandler("privacidad", cmd_privacidad))
    app.add_handler(CommandHandler("ruc", cmd_ruc))
    app.add_handler(CommandHandler("reiniciar", cmd_reiniciar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_mensaje))

    log.info("🤖 Bot arriba (long polling). Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
