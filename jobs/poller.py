"""Background jobs: auto-expire pending orders."""

from __future__ import annotations

import logging
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import db

logger = logging.getLogger(__name__)


async def cleanup_expired_orders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: batalkan order DANA / manual yang melewati batas waktu."""
    try:
        expired = db.expire_pending_orders(minutes=config.ORDER_EXPIRE_MINUTES)
        for order in expired:
            order_id = order["id"]
            user_id = order["user_id"]
            msg_id = order.get("message_id")
            if msg_id:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except Exception:
                    pass
            logger.info("Order %s expired dan dibatalkan otomatis.", order_id)
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"⏰ *Waktu Pembayaran Habis*\n\n"
                        f"Pesanan *#{order_id}* telah dibatalkan otomatis karena melewati batas waktu pembayaran "
                        f"({config.ORDER_EXPIRE_MINUTES} menit).\n"
                        f"Silakan order ulang di /katalog jika masih berminat."
                    ),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
    except Exception as e:
        logger.error("Error menjalankan cleanup_expired_orders: %s", e)
