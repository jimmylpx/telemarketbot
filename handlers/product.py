"""Catalog & order conversation handlers for Simpel Order Bot.

Registered:
- /katalog — list products as inline-keyboard buttons.
- ConversationHandler that walks the user through:
    1. QTY      — ask for and validate the desired quantity.
    2. CONFIRM  — show payment info + Confirm/Cancel buttons.
  Entry point: any `order:<id>` callback from the catalog.
  Fallback:    /cancel at any time.

All replies use Markdown (legacy) parse mode so *bold*, _italic_, and
`code` work without escaping punctuation.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db
from payments.delivery import get_stock_count, get_product_stock_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

QTY = 0
CONFIRM = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_rupiah(n: int) -> str:
    """Format an integer as Indonesian Rupiah with dot separators.

    Example: 50000 -> "50.000".
    """
    return f"{n:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: Application) -> None:
    """Register the /katalog command and the order ConversationHandler."""
    app.add_handler(CommandHandler("katalog", cmd_katalog))

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_quantity, pattern=r"^order:\d+$"),
        ],
        states={
            QTY: [
                CallbackQueryHandler(ask_quantity, pattern=r"^order:\d+$"),
                CallbackQueryHandler(receive_quantity_callback, pattern=r"^qty:\d+$"),
                CallbackQueryHandler(handle_confirm, pattern=r"^confirm:no$"),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_quantity,
                ),
            ],
            CONFIRM: [
                CallbackQueryHandler(ask_quantity, pattern=r"^order:\d+$"),
                CallbackQueryHandler(
                    handle_confirm,
                    pattern=r"^confirm:(yes|no)$",
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CommandHandler("start", cancel_conversation),
            CommandHandler("katalog", cancel_conversation),
        ],
        allow_reentry=True,
        per_message=False,
        per_chat=True,
        conversation_timeout=600,
    )
    app.add_handler(conv_handler)


# ---------------------------------------------------------------------------
# /katalog
# ---------------------------------------------------------------------------


async def cmd_katalog(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """List products with one inline-keyboard button per product."""
    message = update.message
    if message is None:
        return

    try:
        products = db.list_products()
    except Exception as exc:
        logger.exception("Gagal mengambil daftar produk: %s", exc)
        await message.reply_text(
            "Maaf, ada masalah saat memuat katalog. Coba lagi sebentar.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not products:
        await message.reply_text(
            "Belum ada produk. Tanyakan admin untuk menambahkan.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    buttons: list[list[InlineKeyboardButton]] = []
    text_lines = [
        f"🛍️ *Katalog {config.SHOP_NAME}*\n",
        "Silakan pilih produk yang ingin Anda beli:\n",
    ]

    for product in products:
        p_stock_path = get_product_stock_path(product)
        stock = get_stock_count(p_stock_path)
        stock_text = f"{stock} unit" if stock > 0 else "Habis"
        text_lines.append(
            f"📦 *{product['name']}*\n"
            f"• 💵 *Harga:* Rp {format_rupiah(product['price'])}\n"
            f"• 📊 *Stok:* `{stock_text}`\n"
        )
        if stock > 0:
            btn_label = f"🛒 {product['name']} (Stok: {stock})"
        else:
            btn_label = f"❌ {product['name']} (Stok Habis)"
        buttons.append(
            [InlineKeyboardButton(btn_label, callback_data=f"order:{product['id']}")]
        )

    text_lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    keyboard = InlineKeyboardMarkup(buttons)
    text = "\n".join(text_lines)
    await message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Conversation: entry point -> ask for quantity
# ---------------------------------------------------------------------------


async def ask_quantity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Entry point: user clicked an `order:<id>` catalog button."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    await query.answer()

    try:
        pid = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("Produk tidak ditemukan.")
        return ConversationHandler.END

    try:
        product = db.get_product(pid)
    except Exception as exc:
        logger.exception("Gagal mengambil produk %s: %s", pid, exc)
        await query.edit_message_text("Maaf, ada masalah. Coba lagi.")
        return ConversationHandler.END

    if product is None:
        await query.edit_message_text("Produk tidak ditemukan.")
        return ConversationHandler.END

    stock_path = get_product_stock_path(product)
    stock = get_stock_count(stock_path)
    if stock <= 0:
        await query.answer("Maaf, stok produk ini sedang habis!", show_alert=True)
        return ConversationHandler.END

    context.user_data["pending"] = {"product": product}

    qty_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1x", callback_data="qty:1"),
                InlineKeyboardButton("2x", callback_data="qty:2"),
                InlineKeyboardButton("3x", callback_data="qty:3"),
                InlineKeyboardButton("5x", callback_data="qty:5"),
            ],
            [
                InlineKeyboardButton("❌ Batal", callback_data="confirm:no"),
            ]
        ]
    )

    await query.edit_message_text(
        f"🛍️ *{product['name']}*\n"
        f"💵 *Harga:* Rp {format_rupiah(product['price'])}\n"
        f"📊 *Stok Tersedia:* `{stock} unit`\n\n"
        "Pilih jumlah pembelian dengan tombol di bawah atau ketik angka langsung:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=qty_keyboard,
    )
    return QTY


# ---------------------------------------------------------------------------
# Conversation: QTY state -> validate quantity, show payment info
# ---------------------------------------------------------------------------


async def receive_quantity_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Handler saat user menekan tombol pilihan jumlah (qty:1, qty:2, dst)."""
    query = update.callback_query
    if query is None:
        return QTY

    await query.answer()
    data = query.data or ""
    try:
        qty = int(data.split(":")[1])
    except (IndexError, ValueError):
        qty = 1

    pending = context.user_data.get("pending") or {}
    product = pending.get("product")
    if product is None:
        context.user_data.clear()
        await query.edit_message_text(
            "Sesi order sudah berakhir. Silakan mulai lagi dari /katalog.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    stock_path = get_product_stock_path(product)
    stock = get_stock_count(stock_path)
    if stock > 0 and qty > stock:
        await query.answer(f"Stok tidak mencukupi! Hanya tersedia {stock} unit.", show_alert=True)
        return QTY

    base_total = product["price"] * qty
    if config.DANA_AUTO_CHECK:
        final_total, discount = db.get_discounted_unique_total(base_total)
    else:
        final_total = base_total
        discount = 0

    pending["quantity"] = qty
    pending["base_total"] = base_total
    pending["discount"] = discount
    pending["total"] = final_total
    context.user_data["pending"] = pending

    confirm_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Konfirmasi Order", callback_data="confirm:yes"),
                InlineKeyboardButton("❌ Batal", callback_data="confirm:no"),
            ]
        ]
    )

    if discount > 0:
        unik_info = f"Potongan Kode Unik: *-Rp {discount}* (verifikasi otomatis)\n"
    else:
        unik_info = ""

    text = (
        "📦 *Rincian Pesanan*\n"
        f"Produk: *{product['name']}*\n"
        f"Jumlah: *{qty}*\n"
        f"Subtotal: Rp {format_rupiah(base_total)}\n"
        f"{unik_info}"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*Total Tagihan: Rp {format_rupiah(final_total)}*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Klik *Konfirmasi Order* untuk menampilkan QRIS pembayaran."
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard,
    )
    return CONFIRM


async def receive_quantity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """QTY state: parse the typed quantity and ask for confirmation."""
    message = update.message
    if message is None or message.text is None:
        return QTY

    text = message.text.strip()
    try:
        qty = int(text)
    except ValueError:
        await message.reply_text(
            "Jumlah tidak valid. Masukkan angka >= 1. /cancel untuk batal.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return QTY

    if qty < 1:
        await message.reply_text(
            "Jumlah tidak valid. Masukkan angka >= 1. /cancel untuk batal.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return QTY

    pending = context.user_data.get("pending") or {}
    product = pending.get("product")
    if product is None:
        context.user_data.clear()
        await message.reply_text(
            "Sesi order sudah berakhir. Silakan mulai lagi dari /katalog.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    stock_path = get_product_stock_path(product)
    stock = get_stock_count(stock_path)
    if stock > 0 and qty > stock:
        await message.reply_text(
            f"⚠️ *Jumlah Melebihi Stok!*\n\n"
            f"Stok yang tersedia saat ini hanya *{stock} unit*.\n"
            f"Silakan ketik angka antara 1 hingga {stock}:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return QTY

    base_total = product["price"] * qty
    if config.DANA_AUTO_CHECK:
        final_total, discount = db.get_discounted_unique_total(base_total)
    else:
        final_total = base_total
        discount = 0

    pending["quantity"] = qty
    pending["base_total"] = base_total
    pending["discount"] = discount
    pending["total"] = final_total
    context.user_data["pending"] = pending

    confirm_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Konfirmasi Order", callback_data="confirm:yes"),
                InlineKeyboardButton("❌ Batal", callback_data="confirm:no"),
            ]
        ]
    )

    if discount > 0:
        unik_info = f"Potongan Kode Unik: *-Rp {discount}* (verifikasi otomatis)\n"
    else:
        unik_info = ""

    text = (
        "📦 *Rincian Pesanan*\n"
        f"Produk: *{product['name']}*\n"
        f"Jumlah: *{qty}*\n"
        f"Subtotal: Rp {format_rupiah(base_total)}\n"
        f"{unik_info}"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*Total Tagihan: Rp {format_rupiah(final_total)}*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Pembayaran via *{config.PAYMENT_BANK}*\n"
        "Klik *Konfirmasi Order* untuk mendapatkan instruksi transfer."
    )

    await message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=confirm_keyboard,
    )
    return CONFIRM


# ---------------------------------------------------------------------------
# Conversation: CONFIRM state -> create or cancel the order
# ---------------------------------------------------------------------------


async def handle_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """CONFIRM state: persist the order on `confirm:yes`, abort on `confirm:no`."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    await query.answer()

    choice = query.data.split(":")[1] if query.data else ""

    if choice == "no":
        context.user_data.clear()
        await query.edit_message_text("❌ Dibatalkan.")
        return ConversationHandler.END

    # choice == "yes"
    pending = context.user_data.get("pending") or {}
    product = pending.get("product")
    if product is None or "quantity" not in pending or "total" not in pending:
        context.user_data.clear()
        await query.edit_message_text(
            "Sesi order sudah berakhir. Silakan mulai lagi dari /katalog.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    user = update.effective_user
    if user is None:
        context.user_data.clear()
        await query.edit_message_text("Gagal membuat order: user tidak dikenal.")
        return ConversationHandler.END

    order_id = (
        f"ORD-{datetime.now().strftime('%Y%m%d')}-"
        f"{secrets.token_hex(2).upper()}"
    )

    # 1) Simpan order (qris_ref NULL, akan di-set jika QRIS berhasil)
    try:
        db.create_order(
            order_id,
            user.id,
            user.username,
            user.first_name,
            product["id"],
            pending["quantity"],
            pending["total"],
        )
    except Exception as exc:
        logger.exception("Gagal membuat order: %s", exc)
        context.user_data.clear()
        await query.edit_message_text(
            "Maaf, gagal membuat order. Coba lagi dari /katalog.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    context.user_data.clear()

    # 2) Siapkan info transfer & QRIS Dinamis DANA Bisnis
    kode_unik = pending.get("kode_unik", 0)
    base_total = pending.get("base_total", pending["total"])
    final_total = pending["total"]

    if kode_unik > 0:
        unik_line = f"• Kode Verifikasi: *Rp {kode_unik}*\n"
        auto_note = (
            "⚡ *Verifikasi Otomatis:*\n"
            "Sistem akan membaca mutasi masuk secara otomatis begitu transfer diterima.\n"
        )
    else:
        unik_line = ""
        auto_note = "Admin akan verifikasi setelah transfer masuk.\n"

    text = (
        f"✅ *Order #{order_id} Berhasil Dibuat!*\n"
        "\n"
        "📦 *Detail Pesanan:*\n"
        f"• Produk: *{product['name']}* (x{pending['quantity']})\n"
        f"• Subtotal: Rp {format_rupiah(base_total)}\n"
        f"{unik_line}"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *TOTAL TRANSFER: Rp {format_rupiah(final_total)}*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"📱 *Tujuan Pembayaran ({config.PAYMENT_BANK}):*\n"
        f"Nomor: `{config.PAYMENT_NUMBER}`\n"
        f"A/N: *{config.PAYMENT_NAME}*\n"
        "\n"
        "⚠️ *PENTING:*\n"
        f"Harap transfer *TEPAT Rp {format_rupiah(final_total)}* (jangan dibulatkan agar terbaca otomatis).\n"
        "\n"
        f"{auto_note}"
        f"⏰ Batas waktu pembayaran: *{config.ORDER_EXPIRE_MINUTES} menit*.\n"
        "Cek status pesanan Anda di /myorders."
    )

    # Generate QRIS Dinamis dari QRIS Statis DANA Bisnis
    if config.QRIS_BASE_PAYLOAD:
        try:
            from payments.qris_generator import generate_dynamic_qris, generate_qris_image_bytes
            dynamic_payload = generate_dynamic_qris(config.QRIS_BASE_PAYLOAD, final_total)
            qr_bytes = generate_qris_image_bytes(dynamic_payload)

            caption = (
                f"✅ *Order #{order_id} Siap Dibayar!*\n"
                "\n"
                "📦 *Detail Pesanan:*\n"
                f"• Produk: *{product['name']}* (x{pending['quantity']})\n"
                f"• Subtotal: Rp {format_rupiah(base_total)}\n"
                f"{unik_line}"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *TOTAL BAYAR: Rp {format_rupiah(final_total)}*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏪 Merchant: *{config.MERCHANT_NAME}*\n"
                "\n"
                "📲 *Cara Pembayaran:*\n"
                "1. Scan kode QRIS di atas dengan aplikasi *DANA*, GoPay, OVO, ShopeePay, BCA, atau m-Banking Anda.\n"
                f"2. Nominal *Rp {format_rupiah(final_total)}* akan *otomatis terisi* di aplikasi Anda (tidak perlu ketik manual).\n"
                "3. Selesaikan pembayaran.\n"
                "\n"
                f"{auto_note}"
                f"⏰ Batas waktu pembayaran: *{config.ORDER_EXPIRE_MINUTES} menit*.\n"
                "Cek status pesanan di /myorders."
            )

            try:
                await query.delete_message()
            except Exception:
                pass

            sent_msg = await context.bot.send_photo(
                chat_id=user.id,
                photo=qr_bytes,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
            if sent_msg:
                db.set_order_message_id(order_id, sent_msg.message_id)
        except Exception as e:
            logger.exception("Gagal generate QRIS dinamis: %s", e)
            sent_msg = await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
            if sent_msg:
                db.set_order_message_id(order_id, sent_msg.message_id)
    else:
        sent_msg = await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        if sent_msg:
            db.set_order_message_id(order_id, sent_msg.message_id)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Conversation: fallback -> /cancel
# ---------------------------------------------------------------------------


async def cancel_conversation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """Fallback: user typed /cancel — clear state and end the conversation."""
    context.user_data.clear()
    message = update.message
    if message is not None:
        await message.reply_text("Dibatalkan.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


