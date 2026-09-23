"""Entry point Simpel Order Bot dengan DANA Notification Webhook."""

import asyncio
import logging
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder

import config
import db
from handlers import start, product, myorders, admin, cid
from jobs import poller
from payments import klikqris
from payments.dana_webhook import create_webhook_app

logger = logging.getLogger(__name__)

_webhook_runner: web.AppRunner | None = None


async def on_startup(application):
    """Callback saat bot mulai berjalan."""
    global _webhook_runner
    if config.DANA_AUTO_CHECK:
        logger.info("Menjalankan DANA Webhook Server di %s:%d...", config.WEBHOOK_HOST, config.WEBHOOK_PORT)
        webapp = create_webhook_app(application.bot)
        _webhook_runner = web.AppRunner(webapp)
        await _webhook_runner.setup()
        site = web.TCPSite(_webhook_runner, config.WEBHOOK_HOST, config.WEBHOOK_PORT)
        await site.start()
        logger.info("DANA Webhook Server ONLINE di port %d (Publik: %s)",
                    config.WEBHOOK_PORT, config.get_public_url())


async def on_shutdown(application):
    """Callback saat bot berhenti."""
    global _webhook_runner
    if _webhook_runner is not None:
        logger.info("Menghentikan DANA Webhook Server...")
        await _webhook_runner.cleanup()
        logger.info("DANA Webhook Server telah berhenti.")


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
            "Bot Telegram tidak dapat terhubung, silakan isi token di /home/servermax/bottele/.env"
        )
        # Jalankan standalone webhook server jika bot token belum diset
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        webapp = create_webhook_app(None)
        logger.info("Menjalankan Webhook Server Standalone di port %d...", config.WEBHOOK_PORT)
        web.run_app(webapp, host=config.WEBHOOK_HOST, port=config.WEBHOOK_PORT)
        return

    # KlikQRIS (opsional)
    if config.KLIKQRIS_ACTIVE:
        klikqris.init(
            api_key=config.KLIKQRIS_API_KEY,
            merchant_id=config.KLIKQRIS_MERCHANT_ID,
            mode=config.KLIKQRIS_MODE,
        )
        logger.info("KlikQRIS aktif (mode %s)", config.KLIKQRIS_MODE)
    else:
        logger.info("KlikQRIS non-aktif. Menggunakan DANA Notification Auto-check.")

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
    cid.register(app)

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

        # 2) KlikQRIS poller jika aktif
        if config.KLIKQRIS_ACTIVE:
            app.job_queue.run_repeating(
                poller.check_payments,
                interval=poller.POLL_INTERVAL,
                first=poller.POLL_INTERVAL,
                name="klikqris_poller",
            )
            logger.info("Job poller KlikQRIS aktif")

    logger.info("Bot Telegram mulai polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()


