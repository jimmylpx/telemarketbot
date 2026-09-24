"""Handler /subs — Pengaturan langganan notifikasi stok produk.

Fitur:
- /subs dan /subscribe
- Menampilkan daftar produk aktif dengan status langganan (Aktif / Langganan)
- Inline toggle button untuk tiap produk
- Tombol Aktifkan Semua / Matikan Semua
- Callback query handler untuk instant toggle tanpa spam pesan baru
"""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import db

logger = logging.getLogger(__name__)


def build_subs_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Bangun inline keyboard daftar produk dengan status langganan user."""
    products = db.list_products()
    subscribed_ids = set(db.get_user_stock_subscriptions(user_id))

    buttons = []
    for p in products:
        pid = p["id"]
        is_sub = pid in subscribed_ids
        icon = "🔔" if is_sub else "🔕"
        status_txt = "Aktif" if is_sub else "Langganan"
        btn_text = f"{icon} {p['name']} ({status_txt})"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"sub_toggle:{pid}")])

    # Tombol aksi cepat
    action_row = [
        InlineKeyboardButton("🔔 Aktifkan Semua", callback_data="sub_all:on"),
        InlineKeyboardButton("🔕 Matikan Semua", callback_data="sub_all:off"),
    ]
    buttons.append(action_row)
    buttons.append([InlineKeyboardButton("✖️ Tutup Menu", callback_data="subs:close")])

    return InlineKeyboardMarkup(buttons)


def get_subs_message_text(user_id: int) -> str:
    """Teks penjelasan menu /subs."""
    subscribed_ids = set(db.get_user_stock_subscriptions(user_id))
    products = db.list_products()
    total_prods = len(products)
    active_subs = len(subscribed_ids)

    return (
        "🔔 *PENGATURAN NOTIFIKASI STOK PRODUK*\n"
        "\n"
        "Pilih produk yang ingin Anda ikuti. Bot akan otomatis mengirimkan pesan chat kepada Anda begitu stok produk ditambahkan atau di-restock oleh Admin!\n"
        "\n"
        f"📊 *Status Anda:* Mengikuti *{active_subs}* dari *{total_prods}* produk.\n"
        "\n"
        "_Keterangan Tombol:_\n"
        "🔔 *(Aktif)* = Anda menerima notifikasi saat restock\n"
        "🔕 *(Langganan)* = Notifikasi mati (ketuk untuk mengaktifkan)\n"
        "\n"
        "_Ketuk tombol produk di bawah untuk mengubah status langganan:_"
    )


async def cmd_subs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler /subs dan /subscribe: tampilkan menu langganan notifikasi stok."""
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return

    products = db.list_products()
    if not products:
        await message.reply_text(
            "Belum ada produk yang terdaftar di katalog saat ini.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    text = get_subs_message_text(user.id)
    reply_markup = build_subs_keyboard(user.id)
    await message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


async def handle_subs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback query interaktif dari tombol /subs."""
    query = update.callback_query
    if query is None:
        return

    user = update.effective_user
    if user is None:
        await query.answer()
        return

    data = query.data or ""

    if data == "subs:close":
        await query.answer("Menu ditutup.")
        try:
            await query.message.delete()
        except Exception:
            await query.edit_message_text("✅ Pengaturan notifikasi disimpan. Ketik /subs kapan saja untuk mengubahnya.")
        return

    if data == "subs:open":
        await query.answer()
        text = get_subs_message_text(user.id)
        reply_markup = build_subs_keyboard(user.id)
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        return

    if data == "catalog:open":
        await query.answer()
        from handlers.product import cmd_katalog
        await cmd_katalog(update, context)
        return

    if data.startswith("sub_toggle:"):
        try:
            pid = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Produk tidak valid.", show_alert=True)
            return

        product = db.get_product(pid)
        if not product:
            await query.answer("Produk tidak ditemukan.", show_alert=True)
            return

        is_sub = db.is_subscribed_to_stock(user.id, pid)
        if is_sub:
            db.remove_stock_subscription(user.id, pid)
            await query.answer(f"🔕 Notifikasi untuk {product['name']} dimatikan.")
        else:
            db.add_stock_subscription(user.id, pid)
            await query.answer(f"🔔 Berhasil berlangganan notifikasi stok {product['name']}!")

        new_text = get_subs_message_text(user.id)
        new_markup = build_subs_keyboard(user.id)
        try:
            await query.edit_message_text(new_text, reply_markup=new_markup, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=new_markup)
            except Exception:
                pass
        return

    if data == "sub_all:on":
        db.subscribe_all_products(user.id)
        await query.answer("🔔 Notifikasi SEMUA produk berhasil diaktifkan!")
        new_text = get_subs_message_text(user.id)
        new_markup = build_subs_keyboard(user.id)
        try:
            await query.edit_message_text(new_text, reply_markup=new_markup, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        return

    if data == "sub_all:off":
        db.unsubscribe_all_products(user.id)
        await query.answer("🔕 Semua notifikasi produk telah dimatikan.")
        new_text = get_subs_message_text(user.id)
        new_markup = build_subs_keyboard(user.id)
        try:
            await query.edit_message_text(new_text, reply_markup=new_markup, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass
        return


def register(app: Application) -> None:
    """Register command /subs dan callback query handler."""
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("subscribe", cmd_subs))
    app.add_handler(
        CallbackQueryHandler(
            handle_subs_callback,
            pattern=r"^(sub_toggle:\d+|sub_all:(on|off)|subs:(close|open)|catalog:open)$",
        )
    )
