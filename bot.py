"""Entry point Simpel Order Bot dengan QRIS Mutation Webhook."""

import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder

import config
import db
from handlers import start, product, myorders, admin, subscribe
from jobs import poller
from payments.webhook import create_webhook_app

logger = logging.getLogger(__name__)

_webhook_runner: web.AppRunner | None = None


async def on_startup(application):
    """Callback saat bot mulai berjalan."""
    global _webhook_runner

    # Daftarkan daftar command menu bot Telegram
    try:
        from telegram import BotCommand
        commands = [
            BotCommand("katalog", "Katalog produk & belanja"),
            BotCommand("subs", "Notifikasi restock produk"),
            BotCommand("myorders", "Riwayat & status pesanan"),
            BotCommand("cs", "Hubungi Admin / CS (Bantuan Pembayaran)"),
            BotCommand("help", "Bantuan & panduan bot"),
            BotCommand("start", "Mulai bot / Menu utama"),
        ]
        await application.bot.set_my_commands(commands)
    except Exception as exc:
        logger.warning("Gagal mendaftarkan menu commands: %s", exc)

    if config.QRIS_AUTO_CHECK:
        logger.info("Menjalankan QRIS Webhook Server di %s:%d...", config.WEBHOOK_HOST, config.WEBHOOK_PORT)
        webapp = create_webhook_app(application.bot)
        _webhook_runner = web.AppRunner(webapp)
        await _webhook_runner.setup()
        site = web.TCPSite(_webhook_runner, config.WEBHOOK_HOST, config.WEBHOOK_PORT)
        await site.start()
        logger.info("QRIS Webhook Server ONLINE di port %d (Publik: %s)",
                    config.WEBHOOK_PORT, config.get_public_url())


async def on_shutdown(application):
    """Callback saat bot berhenti."""
    global _webhook_runner
    if _webhook_runner is not None:
        logger.info("Menghentikan QRIS Webhook Server...")
        await _webhook_runner.cleanup()
        logger.info("QRIS Webhook Server telah berhenti.")


def main():
    logging.basicConfig(
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        level=logging.INFO,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.INFO)

    db.init_db(config.DB_PATH)
    logger.info("Database SQLite siap: %s", config.DB_PATH)
    logger.info("Toko: %s | Admin ID: %s", config.SHOP_NAME, config.ADMIN_USER_ID)

    if not config.BOT_TOKEN:
        logger.warning(
            "⚠️ TELEGRAM_BOT_TOKEN belum diset di .env! "
            "Bot Telegram tidak dapat terhubung, silakan isi token di file .env"
        )
        # Jalankan standalone webhook server jika bot token belum diset
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        webapp = create_webhook_app(None)
        logger.info("Menjalankan Webhook Server Standalone di port %d...", config.WEBHOOK_PORT)
        web.run_app(webapp, host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)
        return

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    start.register(app)
    product.register(app)
    myorders.register(app)
    admin.register(app)
    subscribe.register(app)

    # Job queue
    if app.job_queue is not None:
        # 1) Auto-expire pending orders
        app.job_queue.run_repeating(
            poller.cleanup_expired_orders,
            interval=60,
            first=30,
            name="cleanup_expired_orders",
        )
        logger.info("Job auto-cancel order kadaluarsa aktif (interval 60 detik)")

        # 2) Watcher stok file .txt tiap produk (interval 60 detik)
        app.job_queue.run_repeating(
            poller.watch_stock_changes,
            interval=60,
            first=10,
            name="watch_stock_changes",
        )
        logger.info("Job stock watcher aktif (interval 60 detik)")

    logger.info("Bot Telegram mulai polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


