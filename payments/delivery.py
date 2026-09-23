"""Modul Pengiriman Otomatis Produk (Auto-Delivery).

Mengambil link/lisensi dari file stok secara atomik & aman dari race condition,
membuat file ORD-xxxx.txt di folder orders/ (termasuk generate token CID untuk Office),
lalu mengirimkan file dokumen tersebut langsung ke chat pembeli di Telegram
serta mengirimkan laporan otomatis ke Telegram ID Admin.
"""

from __future__ import annotations

import fcntl
import html
import logging
import os
import re
import httpx
from pathlib import Path
from telegram.constants import ParseMode

import config
import db

logger = logging.getLogger(__name__)

OFFICE_STOCK_FILE_PATH = "/home/servermax/bottele/office2021.txt"


def escape_markdown(text: str | None) -> str:
    """Escape Telegram Markdown legacy special characters (_ * ` [)."""
    if not text:
        return ""
    s = str(text)
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, f"\\{ch}")
    return s


async def safe_send_markdown(bot, chat_id: int | str, text: str) -> bool:
    """Kirim pesan Telegram dengan ParseMode.MARKDOWN, fallback ke teks polos jika ada karakter entity error."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        return True
    except Exception as exc:
        logger.warning("Gagal kirim pesan dengan Markdown (%s), mencoba kirim plain text ke %s...", exc, chat_id)
        try:
            plain = re.sub(r"[*_`\\]", "", text)
            await bot.send_message(chat_id=chat_id, text=plain)
            return True
        except Exception as exc2:
            logger.error("Gagal kirim pesan plain text ke %s: %s", chat_id, exc2)
            return False


def get_product_stock_path(product: dict | None, product_name: str = "") -> str:
    """Tentukan path file stok berdasarkan produk."""
    if product and product.get("stock_file"):
        return product["stock_file"]
    name = (product.get("name") if product else product_name) or ""
    if "office" in name.lower():
        return OFFICE_STOCK_FILE_PATH
    if product and product.get("id") == 1:
        return config.STOCK_FILE_PATH
    if product and product.get("id"):
        return f"{config.STOCKS_DIR}/stock_{product['id']}.txt"
    return config.STOCK_FILE_PATH


def is_office_product(product: dict | None, product_name: str = "") -> bool:
    """Cek apakah produk merupakan produk Office / Phone Key yang memerlukan token CID."""
    if product and product.get("product_type") == "office_cid":
        return True
    name = (product.get("name") if product else product_name) or ""
    return "office" in name.lower()


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
    """Tambahkan baris stok baru ke file stok secara atomik dengan file locking."""
    clean_lines = [line.strip() for line in new_lines if line.strip()]
    if not clean_lines:
        return get_stock_count(stock_path)

    p = Path(stock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "r+" if p.is_file() else "w+"

    with open(p, mode, encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            existing = f.read()
            lines = [l.strip() for l in existing.splitlines() if l.strip()]
            lines.extend(clean_lines)

            f.seek(0)
            f.truncate()
            f.write("\n".join(lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
            return len(lines)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def save_stock_lines(stock_path: str, lines: list[str]) -> int:
    """Simpan ulang seluruh baris stok secara atomik."""
    clean_lines = [line.strip() for line in lines if line.strip()]
    p = Path(stock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if clean_lines:
                f.write("\n".join(clean_lines) + "\n")
            f.flush()
            os.fsync(f.fileno())
            return len(clean_lines)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def take_stock_links(stock_path: str, count: int = 1) -> tuple[list[str], int]:
    """Ambil sejumlah `count` baris dari file stok dan hapus dari file secara atomik."""
    stock_file = Path(stock_path)
    if not stock_file.is_file():
        logger.error("File stok tidak ditemukan di: %s", stock_path)
        return [], 0

    with open(stock_file, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            content = f.read()
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            
            if not lines:
                return [], 0
            
            taken = lines[:count]
            remaining = lines[count:]
            
            # Tulis ulang file stok dengan sisa link
            f.seek(0)
            f.truncate()
            if remaining:
                f.write("\n".join(remaining) + "\n")
            f.flush()
            os.fsync(f.fileno())
            return taken, len(remaining)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


async def generate_cid_token(value: int = 1) -> str | None:
    """Generate CID token resmi via API cid.idlisensi.com."""
    url = f"{config.CID_BASE_URL}/api_generate_token.php"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    data = {"secret": "Paloco$6_cid_token_secret", "value": value}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.post(url, data=data)
            if resp.status_code == 200:
                res = resp.json()
                if res.get("success"):
                    token = res.get("token")
                    logger.info("Berhasil generate token CID: %s (value: %d)", token, value)
                    return token
                else:
                    logger.error("Gagal generate token CID: %s", res)
            else:
                logger.error("Status error generate token CID: %s", resp.status_code)
    except Exception as exc:
        logger.error("Exception generate_cid_token: %s", exc)
    return None


def save_order_content(orders_dir: str, order_id: str, content: str) -> Path:
    """Simpan isi pesanan ke file orders/{order_id}.txt."""
    out_dir = Path(orders_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{order_id}.txt"
    out_path.write_text(content, encoding="utf-8")
    return out_path


async def deliver_order_products(bot, order: dict, product_name: str) -> bool:
    """Kirim produk (link atau Lisensi + Token CID) ke pembeli secara otomatis setelah lunas."""
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

    # Idempotensi: Cek jika file order sudah pernah dibuat (mencegah kirim dobel)
    if order_file_path.is_file():
        logger.warning("Order %s sudah pernah dikirimkan sebelumnya.", order_id)
        return True

    # Tentukan path file stok & tipe produk
    stock_path = get_product_stock_path(product, product_name)
    is_office = is_office_product(product, product_name)

    logger.info("Memproses delivery order %s: product='%s', stock_path='%s', is_office=%s, qty=%d",
                order_id, product_name, stock_path, is_office, quantity)

    # Ambil baris lisensi/link dari stok
    taken_items, remaining_count = take_stock_links(stock_path, count=quantity)

    # Kasus: Stok Habis
    if not taken_items:
        logger.error("STOK HABIS untuk order %s di file %s!", order_id, stock_path)
        if bot:
            try:
                # Hapus pesan QRIS tagihan sebelumnya jika ada
                msg_id = order.get("message_id")
                if msg_id:
                    try:
                        await bot.delete_message(chat_id=user_id, message_id=msg_id)
                    except Exception:
                        pass

                # Notif ke user
                out_of_stock_buyer = (
                    f"✅ *Pembayaran Berhasil Diterima!*\n\n"
                    f"Pesanan *#{order_id}* ({escape_markdown(product_name)}) telah lunas.\n\n"
                    "⚠️ *Catatan:* Stok lisensi/link instan sedang di-restock. "
                    "Admin akan segera mengirimkan produk Anda secara manual melalui chat ini.\n"
                    "Mohon tunggu sebentar ya!"
                )
                await safe_send_markdown(bot, user_id, out_of_stock_buyer)

                # Notif ke admin
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

    # Buat isi file order
    if is_office:
        # Format Lisensi & Token CID untuk Office beserta cara aktivasi
        blocks = []
        for lic in taken_items:
            tok = await generate_cid_token(value=1)
            tok_str = tok if tok else "Gagal generate token, hubungi admin"
            block = (
                f"Lisensi: {lic}\n"
                f"Token: {tok_str}\n"
                "Cara aktivivasi:\n"
                "1. Input lisensi ke app office Windows\n"
                "2. Ke menu file > account > activate product\n"
                "3. Pilih Activate by phone, foto\n"
                "4. Kembali ke bot tele dan ketik /cid dan upload foto IID tadi dan klik dapatkan CID\n"
                "5. Ikuti petunjuk selanjutnya dari bot."
            )
            blocks.append(block)
        file_text = "\n\n".join(blocks) + "\n"
        hint_cid = (
            "\n\n💡 *Cara Aktivasi Office:*\n"
            "1. Input lisensi ke aplikasi Office Windows\n"
            "2. Menu *File* > *Account* > *Activate Product*\n"
            "3. Pilih *Activate by phone*, lalu foto layar Step 2\n"
            "4. Ketik /cid di bot ini, masukkan Token, upload foto IID & klik *Dapatkan CID*\n"
            "5. Ikuti petunjuk selanjutnya dari bot."
        )
    else:
        # Format baris link biasa
        file_text = "\n".join(taken_items) + "\n"
        hint_cid = ""

    # Simpan file ke folder orders/
    save_order_content(config.ORDERS_DIR, order_id, file_text)
    logger.info("File order %s berhasil disimpan. Sisa stok di %s: %d", order_file_path, stock_path, remaining_count)

    # Hapus pesan QRIS tagihan sebelumnya agar chat rapi & digantikan chat lunas
    msg_id = order.get("message_id")
    if msg_id and bot:
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
            logger.info("Pesan QRIS lama (message_id %s) untuk order %s berhasil dihapus.", msg_id, order_id)
        except Exception as del_err:
            logger.warning("Gagal menghapus pesan QRIS lama order %s: %s", order_id, del_err)

    # Kirim ke pembeli
    if bot:
        # 1. Kirim pesan teks ke pembeli
        try:
            buyer_text = (
                f"🎉 *PEMBAYARAN LUNAS & PRODUK SIAP!*\n\n"
                f"Order ID: *#{order_id}*\n"
                f"Produk: *{escape_markdown(product_name)}* (x{quantity})\n\n"
                f"📄 Rincian produk Anda telah disimpan dalam file *{order_id}.txt* di bawah.\n"
                f"Silakan unduh dan buka file terlampir.{hint_cid}\n\n"
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
                logger.info("Notifikasi produk terkirim otomatis berhasil dikirim ke admin %s", config.ADMIN_USER_ID)
            except Exception as e_adm:
                logger.error("Gagal kirim notif admin di delivery.py: %s", e_adm)

        return True

    return True
