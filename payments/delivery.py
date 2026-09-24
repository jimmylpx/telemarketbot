"""
Modul Pengiriman Otomatis (Instant Auto-Delivery) untuk Produk Berbasis Baris Teks.
Mendukung penjualan link (seperti Jio farm), akun, voucher, token, cookie, serial key.
Setiap baris pada file stok (.txt) merepresentasikan 1 unit produk.
"""

import os
import re
import fcntl
import logging
from pathlib import Path
from telegram.constants import ParseMode

import config
import db

logger = logging.getLogger(__name__)


def escape_markdown(text: str) -> str:
    """Escape karakter Markdown legacy agar tidak error parse entity."""
    if not text:
        return ""
    return re.sub(r"([_*`\[\]])", r"\\\1", str(text))


async def safe_send_markdown(bot, chat_id: int | str, text: str):
    """Kirim pesan dengan parse_mode MARKDOWN, fallback ke plain text jika gagal."""
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("Gagal kirim pesan MARKDOWN ke %s: %s. Fallback ke plain text.", chat_id, exc)
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=plain,
                disable_web_page_preview=True,
            )
        except Exception as exc2:
            logger.error("Gagal kirim pesan plain text ke %s: %s", chat_id, exc2)
            return False


def get_product_stock_path(product: dict | None, product_name: str = "") -> str:
    """Tentukan path file stok (.txt) berdasarkan record produk."""
    if product and product.get("stock_file"):
        return product["stock_file"]
    if product and product.get("id"):
        return f"{config.STOCKS_DIR}/stock_{product['id']}.txt"
    return config.STOCK_FILE_PATH


def get_stock_count(stock_path: str | None = None) -> int:
    """Hitung sisa baris stok yang tersedia secara real-time."""
    p = Path(stock_path or config.STOCK_FILE_PATH)
    if not p.is_file():
        return 0
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
            return len(lines)
    except Exception as exc:
        logger.error("Gagal membaca jumlah stok dari %s: %s", p, exc)
        return 0


def append_stock_lines(stock_path: str, new_lines: list[str]) -> int:
    """Menambahkan baris-baris stok baru ke akhir file stok dengan atomic lock."""
    p = Path(stock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.touch()

    cleaned = [l.strip() for l in new_lines if l.strip()]
    if not cleaned:
        return get_stock_count(stock_path)

    with open(p, "r+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            current_content = f.read()
            f.seek(0, os.SEEK_END)
            if current_content and not current_content.endswith("\n"):
                f.write("\n")
            f.write("\n".join(cleaned) + "\n")
            f.flush()
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

    return get_stock_count(stock_path)


def save_stock_lines(stock_path: str, all_lines: list[str]) -> int:
    """Menimpa seluruh isi file stok (.txt) dengan list baris baru."""
    p = Path(stock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    cleaned = [l.strip() for l in all_lines if l.strip()]
    content = "\n".join(cleaned) + ("\n" if cleaned else "")

    with open(p, "w", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(content)
            f.flush()
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass

    return len(cleaned)


def take_stock_links(stock_path: str, count: int = 1) -> tuple[list[str], int]:
    """
    Mengambil 'count' baris produk dari file stok secara atomic.
    Baris yang diambil akan dihapus dari file stok.
    Mengembalikan (list_produk_diambil, sisa_stok).
    """
    p = Path(stock_path)
    if not p.is_file():
        logger.error("File stok tidak ditemukan: %s", stock_path)
        return [], 0

    with open(p, "r+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            lines = [line.strip() for line in f.readlines() if line.strip()]

            if not lines:
                return [], 0

            taken = lines[:count]
            remaining = lines[count:]

            f.seek(0)
            f.truncate()
            if remaining:
                f.write("\n".join(remaining) + "\n")
            f.flush()
            return taken, len(remaining)
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def save_order_content(orders_dir: str, order_id: str, content: str) -> Path:
    """Simpan isi pesanan ke file orders/{order_id}.txt."""
    out_dir = Path(orders_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{order_id}.txt"
    out_path.write_text(content, encoding="utf-8")
    return out_path


async def deliver_order_products(bot, order: dict, product_name: str) -> bool:
    """Kirim produk baris ke pembeli secara otomatis setelah lunas."""
    order_id = order["id"]
    user_id = order["user_id"]
    username = order.get("username") or "Customer"
    first_name = order.get("first_name") or "Customer"
    quantity = int(order.get("quantity") or 1)

    product_id = order.get("product_id")
    product = db.get_product(product_id) if product_id else None
    if product and product.get("name"):
        product_name = product["name"]

    orders_dir = Path(config.ORDERS_DIR)
    order_file_path = orders_dir / f"{order_id}.txt"

    # Idempotensi: Cek jika file order sudah pernah dibuat (mencegah kirim ganda)
    if order_file_path.is_file():
        logger.warning("Order %s sudah pernah dikirimkan sebelumnya.", order_id)
        return True

    # Tentukan path file stok produk
    stock_path = get_product_stock_path(product, product_name)

    logger.info("Memproses delivery order %s: product='%s', stock_path='%s', qty=%d",
                order_id, product_name, stock_path, quantity)

    # Ambil baris produk dari stok
    taken_items, remaining_count = take_stock_links(stock_path, count=quantity)

    # Kasus: Stok Habis
    if not taken_items:
        logger.error("STOK HABIS untuk order %s di file %s!", order_id, stock_path)
        if bot:
            try:
                # Hapus pesan invoice QRIS sebelumnya jika ada
                msg_id = order.get("message_id")
                if msg_id:
                    try:
                        await bot.delete_message(chat_id=user_id, message_id=msg_id)
                    except Exception:
                        pass

                # Notifikasi ke pembeli
                out_of_stock_buyer = (
                    f"✅ *Pembayaran Berhasil Diterima!*\n\n"
                    f"Pesanan *#{order_id}* ({escape_markdown(product_name)}) telah lunas.\n\n"
                    "⚠️ *Catatan:* Stok instan sedang dalam proses restock. "
                    "Admin akan segera mengirimkan produk Anda secara manual melalui chat ini.\n"
                    "Mohon tunggu sebentar ya!"
                )
                await safe_send_markdown(bot, user_id, out_of_stock_buyer)

                # Notifikasi ke admin
                if config.ADMIN_USER_ID:
                    out_of_stock_admin = (
                        f"🚨 *PERINGATAN: STOK HABIS!*\n\n"
                        f"Order *#{order_id}* dari @{escape_markdown(username)} (ID: `{user_id}`) telah lunas,\n"
                        f"namun file stok `{escape_markdown(stock_path)}` kosong!\n\n"
                        "Harap segera isi stok dan kirim manual ke pembeli."
                    )
                    await safe_send_markdown(bot, config.ADMIN_USER_ID, out_of_stock_admin)
            except Exception as e:
                logger.error("Error kirim pesan stok habis: %s", e)
        return False

    # Buat isi file order (1 baris per produk)
    file_text = "\n".join(taken_items) + "\n"

    # Simpan file ke folder orders/
    save_order_content(config.ORDERS_DIR, order_id, file_text)
    logger.info("File order %s berhasil disimpan. Sisa stok di %s: %d", order_file_path, stock_path, remaining_count)

    # Hapus pesan QRIS tagihan sebelumnya agar chat rapi
    msg_id = order.get("message_id")
    if msg_id and bot:
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
            logger.info("Pesan QRIS lama (message_id %s) untuk order %s dihapus.", msg_id, order_id)
        except Exception as del_err:
            logger.warning("Gagal menghapus pesan QRIS lama order %s: %s", order_id, del_err)

    # Kirim ke pembeli
    if bot:
        # Tampilkan produk langsung di chat jika <= 5 item agar pembeli mudah copy
        items_preview = ""
        if len(taken_items) <= 5:
            preview_lines = [f"`{escape_markdown(it)}`" for it in taken_items]
            items_preview = "\n\n📦 *Detail Produk Anda:*\n" + "\n".join(preview_lines)

        # 1. Kirim pesan teks konfirmasi
        try:
            buyer_text = (
                f"🎉 *PEMBAYARAN LUNAS & PRODUK SIAP!*\n\n"
                f"Order ID: *#{order_id}*\n"
                f"Produk: *{escape_markdown(product_name)}* (x{quantity})\n"
                f"{items_preview}\n\n"
                f"📄 Salinan produk juga telah disimpan dalam file terlampir di bawah.\n"
                f"Terima kasih telah berbelanja di *{escape_markdown(config.SHOP_NAME)}*!"
            )
            await safe_send_markdown(bot, user_id, buyer_text)
        except Exception as exc:
            logger.warning("Gagal kirim pesan teks ke pembeli %s: %s", user_id, exc)

        # 2. Kirim file dokumen .txt ke pembeli
        try:
            with open(order_file_path, "rb") as doc_fp:
                await bot.send_document(
                    chat_id=user_id,
                    document=doc_fp,
                    filename=f"{order_id}.txt",
                    caption=f"📄 Salinan produk untuk order #{order_id}",
                )
        except Exception as exc:
            logger.error("Gagal kirim dokumen order ke pembeli %s: %s", user_id, exc)

        # 3. Notifikasi LAPORAN ORDER BERHASIL ke Admin di Telegram
        if config.ADMIN_USER_ID:
            try:
                s_prod = escape_markdown(product_name)
                s_user = escape_markdown(username)
                s_first = escape_markdown(first_name)
                s_stock_path = escape_markdown(stock_path)

                admin_text = (
                    f"🚀 *PRODUK TERKIRIM OTOMATIS!*\n\n"
                    f"Order ID: *#{order_id}*\n"
                    f"Produk: *{s_prod}* (x{quantity})\n"
                    f"Pembeli: *{s_first}* (@{s_user}) [ID: `{user_id}`]\n"
                    f"📦 File: `orders/{order_id}.txt`\n"
                    f"📉 *Sisa Stok:* `{remaining_count} unit` di `{s_stock_path}`"
                )
                await safe_send_markdown(bot, config.ADMIN_USER_ID, admin_text)
                logger.info("Notifikasi produk terkirim otomatis dikirim ke admin %s", config.ADMIN_USER_ID)
            except Exception as e_adm:
                logger.error("Gagal kirim notif admin di delivery.py: %s", e_adm)

        return True

    return True


async def notify_stock_subscribers(bot, product_id: int, added_count: int, total_stock: int) -> int:
    """Kirim notifikasi otomatis ke semua pelanggan yang subscribe produk ini saat stok ditambahkan."""
    if not bot or total_stock <= 0:
        return 0

    import db
    product = db.get_product(product_id)
    if not product:
        return 0

    subscribers = db.get_subscribers_for_product(product_id)
    if not subscribers:
        logger.info("Restock produk #%d '%s' (+%d unit), tidak ada subscriber.", product_id, product['name'], added_count)
        return 0

    logger.info("Restock produk #%d '%s' (+%d unit), mengirim notifikasi ke %d subscriber...", product_id, product['name'], added_count, len(subscribers))

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    import asyncio

    prod_name = product['name']
    price_val = product['price']
    desc = product.get('description', '')

    msg_text = (
        "🔔 *STOK PRODUK TERSEDIA KEMBALI!*\n"
        "\n"
        f"Kabar baik! Stok untuk produk *{escape_markdown(prod_name)}* baru saja ditambahkan oleh Admin.\n"
        "\n"
        f"📦 *Produk:* {escape_markdown(prod_name)}\n"
        f"💰 *Harga:* Rp {price_val:,}\n"
        f"📊 *Stok Tersedia:* *{total_stock} unit*\n"
    )
    if desc:
        msg_text += f"ℹ️ *Keterangan:* _{escape_markdown(desc)}_\n"

    msg_text += (
        "\n"
        "⚡ _Segera pesan sekarang sebelum kehabisan!_\n"
        "Ketuk perintah /katalog untuk langsung berbelanja."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Buka Katalog Produk", callback_data="catalog:open")],
        [InlineKeyboardButton("🔕 Atur Notifikasi (/subs)", callback_data="subs:open")]
    ])

    sent_count = 0
    for uid in subscribers:
        try:
            await bot.send_message(
                chat_id=uid,
                text=msg_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as exc:
            logger.warning("Gagal kirim notifikasi restock ke user %s: %s", uid, exc)

    logger.info("Selesai mengirim notifikasi restock #%d: berhasil %d dari %d subscriber.", product_id, sent_count, len(subscribers))
    return sent_count
