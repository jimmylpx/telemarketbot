"""Payment & QRIS Notification Listener & Webhook Server untuk Bot Telegram.

Menerima HTTP POST dari MacroDroid / Tasker / Forwarder Android ketika
aplikasi e-Wallet / Merchant (GoPay Merchant, DANA Bisnis, OVO, BCA Mobile, dsb)
menerima transfer / transaksi masuk. Memverifikasi nominal unik dan
mengubah status pesanan menjadi PAID secara otomatis.
"""

from __future__ import annotations

import html
import logging
import re
from aiohttp import web
from telegram.constants import ParseMode

import config
import db

logger = logging.getLogger(__name__)


def parse_mutation_amount(text: str) -> int | None:
    """Mengekstrak nominal angka dari teks notifikasi pembayaran / mutasi masuk.
    
    Mendukung format berbagai provider (GoPay Merchant, DANA, OVO, BCA, dsb):
      - 'Kamu menerima saldo Rp 15.124 dari...'
      - 'Pembayaran QRIS Rp 9.138 berhasil'
      - 'Kamu menerima pembayaran QRIS sebesar Rp 9.138'
      - 'Berhasil menerima transfer Rp 9.138'
      - 'GoPay: Pembayaran sebesar Rp 9.138'
      - 'DANA: Rp 9.138 masuk'
      - 'Rp 9.138' atau 'Rp9138'
    """
    if not text:
        return None

    # Pola 1: Mencari format Rp 15.124 atau Rp15.124 atau IDR 15.124
    match = re.search(r"(?:Rp|IDR)\.?\s*([0-9\.,]+)", text, re.IGNORECASE)
    if match:
        clean_num = match.group(1).replace(".", "").replace(",", "")
        try:
            val = int(clean_num)
            if val > 0:
                return val
        except ValueError:
            pass

    # Pola 2: Format kata kunci diikuti angka
    match = re.search(r"(?:sebesar|masuk|terima|berhasil|nominal|transfer|bayar)\s*(?:Rp|IDR)?\.?\s*([0-9\.,]+)", text, re.IGNORECASE)
    if match:
        clean_num = match.group(1).replace(".", "").replace(",", "")
        try:
            val = int(clean_num)
            if val > 0:
                return val
        except ValueError:
            pass

    # Pola 3: Format angka berdiri sendiri dengan 4 s.d 9 digit
    match = re.search(r" ([0-9]{4,9}) ", text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass

    return None

# Alias untuk backward compatibility
parse_dana_amount = parse_mutation_amount


async def handle_not_found(request: web.Request) -> web.Response:
    """Tolak akses GET via browser demi keamanan (404 Not Found)."""
    return web.Response(text="404 Not Found", status=404)


async def handle_health(request: web.Request) -> web.Response:
    """Minimal health check endpoint."""
    return web.json_response({"status": "ok"})


async def handle_mutation_webhook(request: web.Request) -> web.Response:
    """Handler penerima webhook notifikasi pembayaran dari HP / MacroDroid."""
    bot = request.app.get("bot")

    try:
        if request.content_type == "application/json":
            data = await request.json()
        else:
            post_data = await request.post()
            data = dict(post_data)
    except Exception as exc:
        logger.warning("Gagal membaca payload webhook: %s", exc)
        return web.json_response({"success": False, "error": "Invalid payload format"}, status=400)

    # Validasi secret token
    provided_secret = data.get("secret", "").strip()
    if config.WEBHOOK_SECRET and provided_secret != config.WEBHOOK_SECRET:
        logger.warning("Webhook ditolak: invalid secret '%s'", provided_secret)
        return web.json_response({"success": False, "error": "Forbidden: invalid secret"}, status=403)

    title = str(data.get("title", ""))
    raw_text = str(data.get("text") or data.get("body") or data.get("notification") or data.get("message") or "")
    
    # Gabungkan semua string agar jika nominal berada di judul/text/ticker tetap terdeteksi
    all_content_parts = [title, raw_text]
    for k, v in data.items():
        if k != "secret" and isinstance(v, str):
            all_content_parts.append(v)
    combined_content = " ".join(all_content_parts)

    amount = None
    if "amount" in data:
        try:
            amount = int(data["amount"])
        except ValueError:
            amount = None

    if amount is None:
        amount = parse_mutation_amount(combined_content)

    if amount is None or amount <= 0:
        logger.info("Abaikan notifikasi non-mutasi (title: '%s', text: '%s')", title, raw_text)
        db.record_mutation(title, raw_text, None, None)
        return web.json_response({
            "success": True,
            "status": "ignored",
            "message": "Tidak ada nominal mutasi masuk yang terdeteksi.",
            "received_title": title,
            "received_text": raw_text,
        })

    logger.info("Mutasi pembayaran terdeteksi: Rp %d dari '%s'", amount, combined_content)

    # Cari pending order yang cocok
    order = db.find_pending_order_by_amount(amount)

    if not order:
        logger.warning("Nominal Rp %d masuk tapi tidak ada order pending yang cocok!", amount)
        db.record_mutation(title, combined_content, amount, None)

        if bot and config.ADMIN_USER_ID:
            try:
                from payments.delivery import escape_markdown, safe_send_markdown
                admin_msg = (
                    "⚠️ *MUTASI PEMBAYARAN MASUK (BELUM COCOK)*\n\n"
                    f"Nominal: *Rp {amount:,}*\n"
                    f"Judul: *{escape_markdown(title)}*\n"
                    f"Teks: _{escape_markdown(raw_text)}_\n\n"
                    "Tidak ditemukan order dengan status pending yang bernilai persis sama."
                )
                await safe_send_markdown(bot, config.ADMIN_USER_ID, admin_msg)
            except Exception as e:
                logger.error("Gagal kirim notif admin: %s", e)

        return web.json_response({
            "success": True,
            "status": "unmatched",
            "amount": amount,
            "message": "Mutasi dicatat, tetapi tidak ada order pending dengan nominal ini.",
        })

    # Order cocok! Update jadi PAID
    order_id = order["id"]
    user_id = order["user_id"]
    username = order.get("username") or "Customer"
    first_name = order.get("first_name") or "Customer"
    product_id = order.get("product_id")

    product = db.get_product(product_id) if product_id else None
    product_name = product["name"] if product else "Produk"

    db.update_order_status(order_id, "paid")
    db.record_mutation(title, combined_content, amount, order_id)
    logger.info("Order %s BERHASIL LUNAS via QRIS!", order_id)

    # Pengiriman Produk Otomatis (Auto-Delivery)
    if bot:
        from payments.delivery import deliver_order_products, escape_markdown, safe_send_markdown
        await deliver_order_products(bot, order, product_name)

        # Notifikasi LAPORAN ORDER BERHASIL ke admin di Telegram
        if config.ADMIN_USER_ID:
            try:
                s_prod = escape_markdown(product_name)
                s_user = escape_markdown(username)
                s_first = escape_markdown(first_name)

                admin_msg = (
                    "💰 *PEMBAYARAN QRIS TERVERIFIKASI (AUTO)*\n\n"
                    f"Order ID: *#{order_id}*\n"
                    f"Produk: *{s_prod}* (x{order['quantity']})\n"
                    f"Nominal: *Rp {amount:,}*\n"
                    f"Customer: *{s_first}* (@{s_user}) [ID: `{user_id}`]\n"
                    f"Status: *PAID (LUNAS)*"
                )
                await safe_send_markdown(bot, config.ADMIN_USER_ID, admin_msg)
                logger.info("Notifikasi pembayaran QRIS berhasil dikirim ke admin %s", config.ADMIN_USER_ID)
            except Exception as e:
                logger.error("Gagal kirim notif admin: %s", e)

    return web.json_response({
        "success": True,
        "status": "paid",
        "order_id": order_id,
        "amount": amount,
        "message": f"Order #{order_id} berhasil diverifikasi dan ditandai PAID.",
    })

# Alias untuk backward compatibility
handle_dana_webhook = handle_mutation_webhook

from payments import admin_web


def create_webhook_app(bot) -> web.Application:
    """Buat instance web application aiohttp terpadu (Admin Panel + QRIS Webhook)."""
    app = web.Application()
    app["bot"] = bot

    # Root & unauthorized endpoints tetap 404 (tidak diarahkan ke login)
    app.router.add_get("/", admin_web.handle_not_found)
    app.router.add_get("/health", handle_health)

    # Webhook routes (Dukungan path dinamis & alias umum)
    target_wh = getattr(config, "WEBHOOK_PATH", "/webhook/dana")
    routes_to_add = {target_wh, "/webhook/dana", "/webhook"}
    for p in routes_to_add:
        app.router.add_get(p, admin_web.handle_not_found)
        app.router.add_post(p, handle_mutation_webhook)

    # Admin Web Panel Routes (harus manual akses /admin)
    app.router.add_get("/admin", admin_web.handle_admin_dashboard)
    app.router.add_get("/admin/login", admin_web.handle_login_page)
    app.router.add_post("/admin/login", admin_web.handle_login_submit)
    app.router.add_get("/admin/logout", admin_web.handle_logout)

    # Admin Product Actions
    app.router.add_post("/admin/products/add", admin_web.handle_product_add)
    app.router.add_post("/admin/products/restock", admin_web.handle_product_restock)
    app.router.add_get("/admin/products/stock", admin_web.handle_get_product_stock)
    app.router.add_post("/admin/products/stock/save", admin_web.handle_save_product_stock)
    app.router.add_post("/admin/products/edit", admin_web.handle_product_edit)
    app.router.add_post("/admin/products/delete", admin_web.handle_product_delete)

    return app
