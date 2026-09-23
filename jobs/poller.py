"""Background jobs: auto-expire pending orders & KlikQRIS poller."""

from __future__ import annotations

import logging
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import db
from payments import klikqris

logger = logging.getLogger(__name__)

POLL_INTERVAL = 10  # detik


async def check_payments(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: cek semua order QRIS KlikQRIS yang masih pending."""
    if not klikqris.is_active():
        return

    try:
        orders = db.get_pending_qris_orders()
    except Exception as e:
        logger.exception("Gagal ambil pending orders: %s", e)
        return

    if not orders:
        return

    klik = klikqris.get()
    bot = context.bot

    for order in orders:
        order_id = order["id"]
        try:
            res = await klik.check_status(order_id)
            payment_status = (res.get("data") or {}).get("payment_status", "").lower()
            if payment_status == "paid":
                db.update_order_status(order_id, "paid")
                logger.info("Order %s lunas via KlikQRIS!", order_id)
                await bot.send_message(
                    chat_id=order["user_id"],
                    text=f"✅ Pembayaran untuk order *#{order_id}* berhasil diterima! Pesanan Anda segera diproses.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            elif payment_status in ("expired", "failed"):
                db.update_order_status(order_id, "cancelled")
                logger.info("Order %s %s via KlikQRIS", order_id, payment_status)
                await bot.send_message(
                    chat_id=order["user_id"],
                    text=f"❌ Order *#{order_id}* dibatalkan karena waktu pembayaran telah habis.",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception as exc:
            logger.warning("Error check_status order %s: %s", order_id, exc)


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


