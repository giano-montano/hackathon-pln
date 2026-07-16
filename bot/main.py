"""
Bot de Telegram (long polling).

Flujo de cada mensaje:
    texto -> ESCUDO -> (si pasa) RAG -> OpenAI -> respuesta
El escudo es la primera y única puerta: si bloquea, el LLM nunca ve el texto.

Ejecutar:
    python -m bot.ingest      # una sola vez (construye el índice)
    python -m bot.main        # levanta el bot
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot import rag, shield

load_dotenv()

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

TOKEN = os.getenv("TELEGRAM_BOT")

# Historial corto por chat. En memoria: se pierde al reiniciar (suficiente para la demo).
_historial: dict[int, list[dict]] = defaultdict(list)

BIENVENIDA = (
    "👋 ¡Hola! Soy tu asistente del *TUPA de la SUNAT*.\n\n"
    "Puedo orientarte sobre requisitos, plazos, costos y formularios de los trámites "
    "de SUNAT (RUC, comprobantes de pago, fraccionamiento, devoluciones y más).\n\n"
    "*Pregúntame por ejemplo:*\n"
    "• ¿Qué requisitos necesito para inscribirme en el RUC?\n"
    "• ¿Cómo doy de baja mi RUC?\n"
    "• ¿Cuánto demora la autorización de impresión de comprobantes?\n\n"
    "🛡️ *Tu privacidad primero:* nunca me envíes tu Clave SOL, DNI, RUC, tarjetas ni "
    "contraseñas. Si detecto datos personales, detengo el mensaje automáticamente y no lo "
    "proceso. Tus datos están protegidos por la *Ley N.º 29733*.\n\n"
    "Escribe /ayuda para ver los comandos."
)

AYUDA = (
    "*Comandos*\n"
    "/start – presentación\n"
    "/ayuda – este mensaje\n"
    "/privacidad – cómo protejo tus datos\n"
    "/reiniciar – borra el historial de esta conversación\n\n"
    "Solo escríbeme tu consulta sobre trámites de SUNAT en lenguaje natural."
)

PRIVACIDAD = (
    "🛡️ *Cómo protejo tus datos*\n\n"
    "• Cada mensaje pasa primero por un filtro local que detecta Clave SOL, contraseñas, "
    "DNI, RUC, tarjetas, cuentas, correos, celulares y datos sensibles.\n"
    "• Si detecto alguno, *el mensaje se descarta y jamás se envía al modelo de IA*.\n"
    "• No guardo tus mensajes en disco. El historial vive solo en memoria y se borra "
    "con /reiniciar o al reiniciar el bot.\n"
    "• Solo respondo con información pública del TUPA SUNAT 2018.\n\n"
    "⚖️ La *Ley N.º 29733 – Ley de Protección de Datos Personales* garantiza que tus datos "
    "son tuyos y que puedes ejercer tus derechos ARCO.\n"
    f"📖 {shield.LEY_URL}\n\n"
    "🔐 Recuerda: *SUNAT nunca te pedirá tu Clave SOL* por chat, correo o teléfono."
)


async def cmd_start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(BIENVENIDA, parse_mode=ParseMode.MARKDOWN)


async def cmd_ayuda(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(AYUDA, parse_mode=ParseMode.MARKDOWN)


async def cmd_privacidad(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(PRIVACIDAD, parse_mode=ParseMode.MARKDOWN,
                                    disable_web_page_preview=True)


async def cmd_reiniciar(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    _historial.pop(update.effective_chat.id, None)
    await update.message.reply_text("🧹 Listo, borré el historial de esta conversación.")


async def on_mensaje(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    texto = update.message.text or ""

    # ---------- PUERTA 1: ESCUDO. Nada pasa de aquí sin aprobación. ----------
    veredicto = shield.inspeccionar(texto)
    if veredicto.bloqueado:
        # El log registra el motivo, NUNCA el contenido.
        log.warning("chat=%s %s", chat_id, veredicto.texto_seguro)
        await update.message.reply_text(veredicto.respuesta, parse_mode=ParseMode.MARKDOWN,
                                        disable_web_page_preview=True)
        return

    # ---------- PUERTA 2: RAG ----------
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        respuesta = rag.responder(texto, _historial[chat_id])
    except Exception:
        log.exception("Fallo al responder chat=%s", chat_id)
        await update.message.reply_text(
            "😕 Tuve un problema procesando tu consulta. Inténtalo de nuevo en unos segundos."
        )
        return

    _historial[chat_id].extend([
        {"role": "user", "content": texto},
        {"role": "assistant", "content": respuesta},
    ])
    _historial[chat_id] = _historial[chat_id][-8:]

    await update.message.reply_text(respuesta)


def main() -> None:
    if not TOKEN:
        raise SystemExit("Falta TELEGRAM_BOT en el .env")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("help", cmd_ayuda))
    app.add_handler(CommandHandler("privacidad", cmd_privacidad))
    app.add_handler(CommandHandler("reiniciar", cmd_reiniciar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_mensaje))

    log.info("🤖 Bot arriba (long polling). Ctrl+C para detener.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
