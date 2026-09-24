"""Background jobs: auto-expire pending orders & watch stock file changes."""

from __future__ import annotations

import logging
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
import db

logger = logging.getLogger(__name__)

# Cache stock count per product: {product_id: last_known_stock_count}
_LAST_STOCK_CACHE: dict[int, int] = {}
_CACHE_INITIALIZED: bool = False


def update_cached_stock(product_id: int, stock_count: int) -> None:
    """Update cache stok lokal agar perubahan manual via panel admin tidak terduplikasi oleh watcher."""
    _LAST_STOCK_CACHE[product_id] = stock_count


async def watch_stock_changes(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periksa file .txt stok produk tiap 1 menit dan kirim notifikasi jika stok bertambah."""
    global _LAST_STOCK_CACHE, _CACHE_INITIALIZED

    try:
        from payments.delivery import get_product_stock_path, get_stock_count, notify_stock_subscribers
        products = db.list_products()

        # Inisialisasi awal saat bot baru start: catat stok terkini tanpa broadcast
        if not _CACHE_INITIALIZED:
            for p in products:
                s_path = get_product_stock_path(p)
                _LAST_STOCK_CACHE[p["id"]] = get_stock_count(s_path)
            _CACHE_INITIALIZED = True
            logger.info("Stock watcher aktif: mengawasi file stok untuk %d produk.", len(products))
            return

        for p in products:
            pid = p["id"]
            s_path = get_product_stock_path(p)
            current_stock = get_stock_count(s_path)
            prev_stock = _LAST_STOCK_CACHE.get(pid)

            # Jika stok bertambah dibanding pencatatan sebelumnya
            if prev_stock is not None and current_stock > prev_stock:
                added = current_stock - prev_stock
                logger.info(
                    "Watcher mendeteksi file stok produk #%d '%s' bertambah (+%d unit: %d -> %d). Memicu notifikasi subscriber...",
                    pid, p['name'], added, prev_stock, current_stock
                )
                try:
                    await notify_stock_subscribers(context.bot, pid, added, current_stock)
                except Exception as ex:
                    logger.error("Gagal broadcast notifikasi restock dari watcher produk #%d: %s", pid, ex)

            # Update cache dengan angka stok terbaru
            _LAST_STOCK_CACHE[pid] = current_stock

    except Exception as exc:
        logger.error("Error pada watch_stock_changes: %s", exc)


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
