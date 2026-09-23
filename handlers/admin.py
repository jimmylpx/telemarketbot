"""Admin command handlers for Simpel Order Bot.

Registered commands:
- /addproduct  — ConversationHandler to add a new product
                  (NAME -> PRICE -> DESCRIPTION)
- /listproducts — list every product in the database
- /delproduct   — delete a product by id
- /orders       — list the most recent orders with inline status buttons
- /broadcast    — broadcast a message to every known user

Also registers a ``CallbackQueryHandler`` for the
``setstatus:<order_id>:<status>`` inline-button pattern emitted by ``/orders``
so the admin can mark orders as ``paid`` / ``cancelled`` / ``pending``.

All admin entry points first check
``update.effective_user.id == config.ADMIN_USER_ID`` and reply with
"⛔ Perintah ini khusus admin." otherwise.
"""

import logging
import re

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

logger = logging.getLogger(__name__)

# ConversationHandler states for /addproduct.
NAME, PRICE, DESCRIPTION = 0, 1, 2
EDIT_CHOICE, EDIT_INPUT = 10, 11

# Map order status -> emoji used in the /orders list.
_STATUS_EMOJI = {
    "pending": "⏳",
    "paid": "✅",
    "cancelled": "❌",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _is_admin(update: Update) -> bool:
    """Return True when the effective user matches the configured admin."""
    user = update.effective_user
    return user is not None and user.id == config.ADMIN_USER_ID


async def _deny_non_admin(update: Update) -> None:
    """Reply to the effective chat with the standard 'admin only' message."""
    message = update.effective_message
    if message is None:
        return
    await message.reply_text("⛔ Perintah ini khusus admin.")


def format_rupiah(n: int) -> str:
    """Format an integer IDR amount as Indonesian-style ``50.000``."""
    return f"{n:,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def register(app) -> None:
    """Register every admin handler on the supplied ``Application``."""

    conv_addproduct = ConversationHandler(
        entry_points=[CommandHandler("addproduct", addproduct_start)],
        states={
            NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, addproduct_name
                )
            ],
            PRICE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, addproduct_price
                )
            ],
            DESCRIPTION: [
                # /skip must reach the description handler as a text
                # message, so we list it explicitly. /cancel intentionally
                # does NOT match here and is handled by the fallback
                # CommandHandler below.
                MessageHandler(
                    filters.Regex(r"^/skip$"), addproduct_description
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, addproduct_description
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_addproduct)],
        per_message=False,
        per_chat=True,
        conversation_timeout=600,
    )

    conv_editproduct = ConversationHandler(
        entry_points=[
            CommandHandler("editproduct", cmd_editproduct),
            CallbackQueryHandler(handle_select_product, pattern=r"^editprod_sel:\d+$"),
        ],
        states={
            EDIT_CHOICE: [
                CallbackQueryHandler(handle_select_product, pattern=r"^editprod_sel:\d+$"),
                CallbackQueryHandler(handle_choose_field, pattern=r"^editprod_field:(name|price|desc|done|cancel)(:\d+)?$"),
            ],
            EDIT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input_value),
                MessageHandler(filters.Regex(r"^/skip$"), handle_input_value),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_editproduct)],
        per_message=False,
        per_chat=True,
        conversation_timeout=600,
        allow_reentry=True,
    )

    app.add_handler(conv_addproduct)
    app.add_handler(conv_editproduct)
    app.add_handler(CommandHandler("editproduct", cmd_editproduct))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("listproducts", cmd_listproducts))
    app.add_handler(CommandHandler("delproduct", cmd_delproduct))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(
        CallbackQueryHandler(
            handle_setstatus,
            pattern=r"^setstatus:[A-Z0-9-]+:(paid|cancelled|pending)$",
        )
    )
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))


# ---------------------------------------------------------------------------
# /addproduct ConversationHandler
# ---------------------------------------------------------------------------


async def addproduct_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Entry point of ``/addproduct`` — reset state and ask for the name."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return -1

    message = update.effective_message
    if message is None:
        return -1

    context.user_data["new_product"] = {}
    await message.reply_text(
        "📝 *Tambah Produk Baru*\nKirim nama produk:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return NAME


async def addproduct_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Store the product name and ask for the price."""
    message = update.effective_message
    if message is None or message.text is None:
        # Stay in the NAME state; the user can retype.
        return NAME

    context.user_data["new_product"]["name"] = message.text.strip()
    await message.reply_text(
        "Kirim harga (angka, contoh: `50000`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return PRICE


async def addproduct_price(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Validate the entered price and ask for the description."""
    message = update.effective_message
    if message is None or message.text is None:
        return PRICE

    raw = message.text.strip().replace(".", "").replace(",", "")
    try:
        price = int(raw)
    except ValueError:
        await message.reply_text(
            "Harga tidak valid. Kirim angka saja. /cancel untuk batal."
        )
        return PRICE

    if price <= 0:
        await message.reply_text(
            "Harga tidak valid. Kirim angka saja. /cancel untuk batal."
        )
        return PRICE

    context.user_data["new_product"]["price"] = price
    await message.reply_text(
        "Kirim deskripsi (atau /skip untuk kosong):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return DESCRIPTION


async def addproduct_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Persist the new product (or honour ``/skip``) and end the conversation."""
    message = update.effective_message
    if message is None or message.text is None:
        return DESCRIPTION

    text = message.text.strip()
    desc = "" if text == "/skip" else text

    pending = context.user_data.get("new_product", {})
    name = pending.get("name", "")
    price = pending.get("price", 0)

    try:
        pid = db.add_product(name, price, desc)
    except Exception as exc:
        logger.exception("Gagal add_product: %s", exc)
        await message.reply_text(
            "Maaf, ada masalah saat menyimpan produk. Coba lagi."
        )
        context.user_data.clear()
        return -1

    await message.reply_text(
        "✅ Produk ditambahkan!\n"
        f"ID: {pid}\n"
        f"Nama: {name}\n"
        f"Harga: Rp {format_rupiah(price)}\n"
        f"Deskripsi: {desc or '(kosong)'}",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data.clear()
    return -1


async def cancel_addproduct(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Fallback handler that aborts the ``/addproduct`` conversation."""
    message = update.effective_message
    if message is not None:
        await message.reply_text("Dibatalkan.")
    context.user_data.clear()
    return -1


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# /editproduct ConversationHandler
# ---------------------------------------------------------------------------


async def show_edit_menu(msg_or_query, context: ContextTypes.DEFAULT_TYPE, product: dict, is_callback: bool = False) -> int:
    """Tampilkan menu pilihan field yang ingin diedit."""
    pid = product["id"]
    context.user_data["editing_pid"] = pid
    desc = product.get("description") or "-"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Ubah Nama", callback_data=f"editprod_field:name:{pid}"),
            InlineKeyboardButton("💰 Ubah Harga", callback_data=f"editprod_field:price:{pid}"),
        ],
        [
            InlineKeyboardButton("📄 Ubah Deskripsi", callback_data=f"editprod_field:desc:{pid}"),
        ],
        [
            InlineKeyboardButton("✅ Selesai", callback_data="editprod_field:done"),
        ]
    ])

    text = (
        f"✏️ *Edit Produk #{pid}*\n\n"
        f"• *Nama:* {product['name']}\n"
        f"• *Harga:* Rp {format_rupiah(product['price'])}\n"
        f"• *Deskripsi:* {desc}\n\n"
        "Pilih bagian yang ingin diubah:"
    )

    if is_callback:
        await msg_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    else:
        await msg_or_query.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    return EDIT_CHOICE


async def cmd_editproduct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /editproduct [id]."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return ConversationHandler.END

    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    args = context.args or []
    if args:
        try:
            pid = int(args[0])
            product = db.get_product(pid)
            if not product:
                await message.reply_text(f"❌ Produk #{pid} tidak ditemukan.")
                return ConversationHandler.END
            return await show_edit_menu(message, context, product, is_callback=False)
        except ValueError:
            await message.reply_text("ID produk harus angka. Contoh: `/editproduct 1`", parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END

    products = db.list_products()
    if not products:
        await message.reply_text("Belum ada produk yang tersimpan.")
        return ConversationHandler.END

    buttons = []
    for p in products:
        buttons.append([
            InlineKeyboardButton(
                f"📦 #{p['id']} - {p['name']} (Rp {format_rupiah(p['price'])})",
                callback_data=f"editprod_sel:{p['id']}"
            )
        ])
    buttons.append([InlineKeyboardButton("❌ Batal", callback_data="editprod_field:cancel")])

    await message.reply_text(
        "✏️ *Pilih Produk yang Ingin Diedit:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return EDIT_CHOICE


async def handle_select_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler saat admin memilih produk dari tombol inline."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    data = query.data or ""
    pid_str = data.split(":")[1]
    try:
        pid = int(pid_str)
    except ValueError:
        return ConversationHandler.END

    product = db.get_product(pid)
    if not product:
        await query.edit_message_text("❌ Produk tidak ditemukan.")
        return ConversationHandler.END

    return await show_edit_menu(query, context, product, is_callback=True)


async def handle_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler saat admin memilih field: name, price, desc, atau done/cancel."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END
    await query.answer()

    parts = query.data.split(":")
    action = parts[1]

    if action in ("done", "cancel"):
        context.user_data.pop("editing_pid", None)
        context.user_data.pop("editing_field", None)
        await query.edit_message_text("✅ Selesai mengedit produk.")
        return ConversationHandler.END

    field = action
    pid = int(parts[2])
    product = db.get_product(pid)
    if not product:
        await query.edit_message_text("❌ Produk tidak ditemukan.")
        return ConversationHandler.END

    context.user_data["editing_pid"] = pid
    context.user_data["editing_field"] = field

    if field == "name":
        prompt = (
            f"📝 *Ubah Nama Produk #{pid}*\n\n"
            f"Nama saat ini: `{product['name']}`\n\n"
            "Silakan ketik nama baru untuk produk ini (atau ketik /cancel untuk batal):"
        )
    elif field == "price":
        prompt = (
            f"💰 *Ubah Harga Produk #{pid}*\n\n"
            f"Harga saat ini: `Rp {format_rupiah(product['price'])}`\n\n"
            "Silakan ketik nominal harga baru berupa angka (contoh: 25000) (atau ketik /cancel untuk batal):"
        )
    elif field == "desc":
        prompt = (
            f"📄 *Ubah Deskripsi Produk #{pid}*\n\n"
            f"Deskripsi saat ini: `{product.get('description') or '-'}`\n\n"
            "Silakan ketik deskripsi baru (ketik /skip untuk kosongkan, /cancel untuk batal):"
        )
    else:
        return EDIT_CHOICE

    await query.edit_message_text(prompt, parse_mode=ParseMode.MARKDOWN)
    return EDIT_INPUT


async def handle_input_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler saat admin mengetikkan nilai baru untuk field yang dipilih."""
    message = update.effective_message
    if message is None or not message.text:
        return EDIT_INPUT

    text = message.text.strip()
    pid = context.user_data.get("editing_pid")
    field = context.user_data.get("editing_field")

    if not pid or not field:
        context.user_data.clear()
        await message.reply_text("Sesi edit berakhir. Gunakan /editproduct untuk mulai lagi.")
        return ConversationHandler.END

    product = db.get_product(pid)
    if not product:
        context.user_data.clear()
        await message.reply_text("❌ Produk tidak ditemukan.")
        return ConversationHandler.END

    if field == "name":
        db.update_product(pid, name=text)
        await message.reply_text(f"✅ Nama produk #{pid} diperbarui menjadi: *{text}*", parse_mode=ParseMode.MARKDOWN)
    elif field == "price":
        clean_num = re.sub(r"\D+", "", text)
        try:
            new_price = int(clean_num)
            if new_price < 0:
                raise ValueError
        except ValueError:
            await message.reply_text("⚠️ Harga harus berupa angka positif. Silakan ketik angka lagi (atau /cancel):")
            return EDIT_INPUT
        db.update_product(pid, price=new_price)
        await message.reply_text(f"✅ Harga produk #{pid} diperbarui menjadi: *Rp {format_rupiah(new_price)}*", parse_mode=ParseMode.MARKDOWN)
    elif field == "desc":
        new_desc = "" if text == "/skip" else text
        db.update_product(pid, description=new_desc)
        await message.reply_text(f"✅ Deskripsi produk #{pid} berhasil diperbarui.", parse_mode=ParseMode.MARKDOWN)

    # Tampilkan kembali menu edit produk dengan data terbaru
    updated_p = db.get_product(pid)
    return await show_edit_menu(message, context, updated_p, is_callback=False)


async def cancel_editproduct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Membatalkan sesi /editproduct."""
    context.user_data.pop("editing_pid", None)
    context.user_data.pop("editing_field", None)
    message = update.effective_message
    if message:
        await message.reply_text("❌ Edit produk dibatalkan.")
    return ConversationHandler.END



# Simple admin commands
# ---------------------------------------------------------------------------


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tampilkan menu bantuan panel admin."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return

    message = update.effective_message
    if message is None:
        return

    text = (
        f"🛠️ *Panel Admin {config.SHOP_NAME}*\n\n"
        "Gunakan perintah di bawah ini:\n"
        "• /addproduct — Tambah produk baru\n"
        "• /listproducts — Lihat daftar semua produk\n"
        "• /editproduct `<id>` — Edit nama, harga, atau deskripsi produk\n"
        "• /delproduct `<id>` — Hapus produk\n"
        "• /orders — Lihat riwayat & status pesanan\n"
        "• /broadcast `<pesan>` — Broadcast pesan ke user\n\n"
        f"🌐 *Web Admin Panel:* `{config.get_public_url()}{config.ADMIN_WEB_PATH}`\n"
        f"🔔 *Webhook URL (MacroDroid):* `{config.get_public_url()}{config.DANA_WEBHOOK_PATH}`"
    )
    await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_listproducts(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ``/listproducts`` — print every product."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return

    message = update.effective_message
    if message is None:
        return

    try:
        products = db.list_products()
    except Exception as exc:
        logger.exception("Gagal list_products: %s", exc)
        await message.reply_text("Maaf, ada masalah. Coba lagi.")
        return

    if not products:
        await message.reply_text("Belum ada produk.")
        return

    lines = []
    for p in products:
        desc = p.get("description") or ""
        lines.append(
            f"📦 *#{p['id']} — {p['name']}*\n"
            f"💰 Harga: Rp {format_rupiah(p['price'])}\n"
            f"📄 Deskripsi: {desc}\n"
            f"👉 Ketik `/editproduct {p['id']}` untuk mengubah."
        )
    await message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_delproduct(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ``/delproduct <id>`` — remove a product by id."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return

    message = update.effective_message
    if message is None:
        return

    args = context.args or []
    if not args:
        await message.reply_text(
            "Gunakan: `/delproduct <id>`", parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        pid = int(args[0])
    except ValueError:
        await message.reply_text("ID harus angka.")
        return

    try:
        ok = db.delete_product(pid)
    except Exception as exc:
        logger.exception("Gagal delete_product(%s): %s", pid, exc)
        await message.reply_text("Maaf, ada masalah. Coba lagi.")
        return

    if ok:
        await message.reply_text(f"✅ Produk #{pid} dihapus.")
    else:
        await message.reply_text("Produk tidak ditemukan.")


async def cmd_orders(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ``/orders [status]`` — show the latest orders with buttons."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return

    message = update.effective_message
    if message is None:
        return

    # Optional status filter, e.g. ``/orders paid``.
    status_filter = context.args[0] if context.args else None

    try:
        orders = db.get_all_orders(limit=20, status=status_filter)
    except Exception as exc:
        logger.exception("Gagal get_all_orders: %s", exc)
        await message.reply_text("Maaf, ada masalah. Coba lagi.")
        return

    if not orders:
        await message.reply_text("Belum ada order.")
        return

    # Keep individual messages short: split into chunks of 10 orders.
    chunk_size = 10
    chunks = [
        orders[i : i + chunk_size] for i in range(0, len(orders), chunk_size)
    ]
    total = len(orders)

    for chunk in chunks:
        lines = [f"📦 *Order Terbaru* ({total})\n"]
        keyboard: list[list[InlineKeyboardButton]] = []

        for o in chunk:
            username = o.get("username") or "no_user"
            status = o.get("status", "pending")
            emoji = _STATUS_EMOJI.get(status, "⏳")
            order_id = o["id"]

            lines.append(
                f"#{order_id} | @{username}\n"
                f"Product #{o['product_id']} x{o['quantity']} = "
                f"Rp {format_rupiah(o['total'])}\n"
                f"Status: {emoji} {status}\n"
                f"{o.get('created_at', '')}\n"
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "✅ Paid",
                        callback_data=f"setstatus:{order_id}:paid",
                    ),
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=f"setstatus:{order_id}:cancelled",
                    ),
                ]
            )

        await message.reply_text(
            "".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ---------------------------------------------------------------------------
# Inline-button callback: setstatus
# ---------------------------------------------------------------------------


async def handle_setstatus(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle the ``setstatus:<order_id>:<status>`` callback from ``/orders``."""
    query = update.callback_query
    if query is None:
        return

    # Always acknowledge the callback so the loading indicator disappears,
    # even if we are about to bail out.
    await query.answer()

    if not _is_admin(update):
        return

    try:
        _, order_id, new_status = query.data.split(":")
    except ValueError:
        logger.warning("Data setstatus tidak valid: %r", query.data)
        return

    try:
        db.update_order_status(order_id, new_status)
        if new_status == "paid":
            order = db.get_order_by_id(order_id)
            if order:
                product = db.get_product(order["product_id"])
                p_name = product["name"] if product else "Produk"
                from payments.delivery import deliver_order_products
                await deliver_order_products(context.bot, order, p_name)
    except Exception as exc:
        logger.exception(
            "Gagal update_order_status(%s, %s): %s",
            order_id,
            new_status,
            exc,
        )
        try:
            await query.edit_message_text(
                "Maaf, ada masalah saat update status."
            )
        except Exception as inner_exc:  # pragma: no cover - defensive
            logger.warning("edit_message_text gagal: %s", inner_exc)
        return

    admin_id = update.effective_user.id if update.effective_user else "?"
    logger.info(
        "Order %s -> %s oleh admin %s", order_id, new_status, admin_id
    )

    try:
        await query.edit_message_text(
            f"✅ Status order #{order_id} diubah ke *{new_status}*.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("edit_message_text setstatus gagal: %s", exc)


# ---------------------------------------------------------------------------
# /broadcast
# ---------------------------------------------------------------------------


async def cmd_broadcast(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ``/broadcast <message>`` — send the text to every known user."""
    if not _is_admin(update):
        await _deny_non_admin(update)
        return

    message = update.effective_message
    if message is None:
        return

    text = " ".join(context.args or []).strip()
    if not text:
        await message.reply_text(
            "Gunakan: `/broadcast <pesan>`", parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        user_ids = db.get_all_user_ids()
    except Exception as exc:
        logger.exception("Gagal get_all_user_ids: %s", exc)
        await message.reply_text("Maaf, ada masalah. Coba lagi.")
        return

    success = 0
    failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
            success += 1
        except Exception as exc:
            logger.warning("Broadcast ke %s gagal: %s", uid, exc)
            failed += 1

    await message.reply_text(
        f"📣 Broadcast terkirim ke *{success}* user. Gagal: *{failed}*.",
        parse_mode=ParseMode.MARKDOWN,
    )


