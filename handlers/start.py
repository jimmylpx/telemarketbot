"""Start, help, and menu command handlers for Simpel Order Bot.

Registered commands:
- /start — upsert user and send welcome message (Indonesian).
- /menu  — alias of /start.
- /help  — send help text (Indonesian).
- /cs    — customer service & admin contact for payment assistance.

All replies use Markdown (legacy) parse mode so *bold*, _italic_,
and `code` work without escaping punctuation.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import config
import db

logger = logging.getLogger(__name__)


def register(app: Application) -> None:
    """Register the /start, /help, /menu, and /cs command handlers on the app."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    # /menu is an alias for /start.
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("cs", cmd_cs))
    app.add_handler(CommandHandler("support", cmd_cs))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start and /menu: upsert the user, then send the welcome message."""
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        # No associated user (e.g. channel post); nothing meaningful to do.
        return

    try:
        db.upsert_user(user.id, user.username, user.first_name)
    except Exception as exc:
        logger.exception("Gagal upsert user %s: %s", user.id, exc)
        # Don't abort the welcome; the user can still use the bot.

    first_name = user.first_name or "teman"

    text = (
        f"👋 Halo {first_name}!\n"
        "\n"
        f"Selamat datang di *{config.SHOP_NAME}*.\n"
        "Ketuk /katalog untuk order produk, /cid untuk aktivasi Confirmation ID, /myorders untuk orderan kamu, "
        "/cs jika butuh bantuan admin, atau /help untuk bantuan."
    )

    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help: send a short Indonesian help message."""
    message = update.message
    if message is None:
        return

    text = (
        "ℹ️ *Bantuan & Panduan Bot*\n"
        "\n"
        "/katalog — Lihat & order produk\n"
        "/cid — Aktivasi Confirmation ID (CID) & OCR\n"
        "/subs — Pengaturan notifikasi saat stok produk ditambah\n/myorders — Riwayat order & status pesanan\n"
        "/cs — Hubungi Customer Service / Admin jika bot tidak merespon pembayaran\n"
        "/start — Menu utama\n"
        "\n"
        "Pembayaran otomatis terverifikasi via QRIS (Semua E-Wallet & Mobile Banking). "
        "Jika ada kendala transfer, silakan gunakan perintah /cs."
    )

    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_cs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cs: Tampilkan kontak Telegram Admin jika bot tidak merespon pembayaran."""
    message = update.message
    if message is None:
        return

    admin_username = ""
    admin_name = "Admin / CS"
    admin_url = None

    # 1. Cek dari konfigurasi manual ADMIN_CONTACT jika disetel
    contact_cfg = getattr(config, "ADMIN_CONTACT", "").strip()
    if contact_cfg:
        admin_username = contact_cfg.lstrip("@").replace("https://t.me/", "")
        admin_url = f"https://t.me/{admin_username}"

    # 2. Cek database jika username belum didapat
    if not admin_username and config.ADMIN_USER_ID:
        try:
            admin_user = db.get_user(config.ADMIN_USER_ID)
            if admin_user:
                if admin_user.get("username"):
                    admin_username = admin_user["username"]
                    admin_url = f"https://t.me/{admin_username}"
                if admin_user.get("first_name"):
                    admin_name = admin_user["first_name"]
        except Exception as exc:
            logger.warning("Gagal query profil admin dari database: %s", exc)

    # 3. Fallback direct telegram link jika tidak ada username publik
    if not admin_url and config.ADMIN_USER_ID:
        admin_url = f"tg://user?id={config.ADMIN_USER_ID}"

    username_display = f"@{admin_username}" if admin_username else admin_name

    text = (
        "🆘 *BANTUAN & CUSTOMER SERVICE (CS)*\n"
        "\n"
        "Jika Anda telah melakukan pembayaran/transfer tetapi *bot belum merespon* atau pesanan belum ditandai *Lunas*, silakan hubungi Admin kami:\n"
        "\n"
        f"👤 *Kontak Admin:* {username_display}\n"
        "\n"
        "📋 *Format Pengaduan Pembayaran:*\n"
        "Agar kendala Anda dapat segera diproses, mohon kirimkan:\n"
        "1. *Order ID* (Cek melalui perintah /myorders)\n"
        "2. *Bukti Screenshot* struk transfer / pembayaran QRIS\n"
        "3. *Nominal persis* yang Anda bayarkan\n"
        "\n"
        "Silakan klik tombol di bawah untuk langsung membuka chat dengan Admin Telegram 👇"
    )

    keyboard = []
    if admin_url:
        keyboard.append([InlineKeyboardButton("💬 Hubungi Admin via Telegram", url=admin_url)])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
