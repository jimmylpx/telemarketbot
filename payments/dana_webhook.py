"""DANA Notification Listener & Webhook Server untuk Bot Telegram.

Menerima HTTP POST dari MacroDroid / Tasker / Forwarder Android ketika
aplikasi DANA menerima transfer masuk. Memverifikasi nominal unik dan
mengubah status pesanan menjadi PAID secara otomatis tanpa API pihak ketiga.
"""

from __future__ import annotations

import json
import logging
import re
from aiohttp import web

import config
import db

logger = logging.getLogger(__name__)


def parse_dana_amount(text: str) -> int | None:
    """Mengekstrak nominal rupiah dari teks notifikasi DANA.
    
    Mendukung format:
      - 'Pembayaran Masuk Rp998 diterima DANA Bisnis.' -> 998
      - 'Kamu menerima saldo Rp 15.124 dari...' -> 15124
      - 'Pembayaran QRIS Rp 9.138 berhasil' -> 9138
      - 'Kamu menerima pembayaran QRIS sebesar Rp 9.138' -> 9138
      - 'Berhasil menerima transfer Rp 9.138' -> 9138
      - 'DANA: Rp 9.138 masuk' -> 9138
      - 'Rp 9.138' atau 'Rp9138' -> 9138
    """
    if not text:
        return None

    # Pola 1: Mencari format Rp 15.124 atau Rp15.124 atau IDR 15.124
    match = re.search(r"(?:Rp|IDR)\.?\s*([0-9\.,]+)", text, re.IGNORECASE)
    if match:
        clean_num = match.group(1).rstrip(".,").replace(".", "").replace(",", "")
        try:
            val = int(clean_num)
            if val > 0:
                return val
        except ValueError:
            pass

    # Pola 2: Format angka berdiri sendiri dengan 3 s.d 9 digit
    match = re.search(r"\b([0-9]{3,9})\b", text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass

    return None


async def handle_not_found(request: web.Request) -> web.Response:
    """Tolak akses GET via browser demi keamanan (404 Not Found)."""
    return web.Response(text="404 Not Found", status=404)


async def handle_health(request: web.Request) -> web.Response:
    """Minimal health check endpoint."""
    return web.json_response({"status": "ok"})


async def handle_dana_webhook(request: web.Request) -> web.Response:
    """Handler penerima webhook notifikasi dari HP (MacroDroid / Tasker)."""
    bot = request.app.get("bot")

    data: dict = {}
    try:
        if request.can_read_body:
            content_type = request.content_type.lower()
            if "json" in content_type:
                data = await request.json()
            else:
                try:
                    data = await request.json()
                except Exception:
                    post_data = await request.post()
                    data = dict(post_data)
    except Exception:
        pass

    if not data:
        try:
            body_text = await request.text()
            if body_text.strip():
                data = json.loads(body_text)
        except Exception as exc:
            logger.warning("Gagal membaca payload webhook: %s", exc)
            return web.json_response({"success": False, "error": "Invalid payload format"}, status=400)

    # Validasi secret token: validasi ketat sesuai WEBHOOK_SECRET dari .env
    provided_secret = str(data.get("secret", "")).strip()
    if config.WEBHOOK_SECRET and provided_secret != config.WEBHOOK_SECRET:
        logger.warning("Webhook ditolak: invalid secret '%s'", provided_secret)
        return web.json_response({"success": False, "error": "Forbidden: invalid secret"}, status=403)

    title = str(data.get("title", ""))
    raw_text = str(
        data.get("text")
        or data.get("notification")
        or data.get("body")
        or data.get("message")
        or data.get("notif_body")
        or ""
    )
    
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
        amount = parse_dana_amount(combined_content)

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

    logger.info("Mutasi DANA terdeteksi: Rp %d dari '%s'", amount, combined_content)

    # Cari pending order yang cocok
    order = db.find_pending_order_by_amount(amount)

    if not order:
        logger.warning("Nominal Rp %d masuk tapi tidak ada order pending yang cocok!", amount)
        db.record_mutation(title, combined_content, amount, None)

        if bot and config.ADMIN_USER_ID:
            try:
                from payments.delivery import escape_markdown, safe_send_markdown
                admin_msg = (
                    "⚠️ *MUTASI DANA MASUK (BELUM COCOK)*\n\n"
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
    logger.info("Order %s BERHASIL LUNAS via DANA!", order_id)

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
                    "💰 *PEMBAYARAN DANA TERVERIFIKASI (AUTO)*\n\n"
                    f"Order ID: *#{order_id}*\n"
                    f"Produk: *{s_prod}* (x{order['quantity']})\n"
                    f"Nominal: *Rp {amount:,}*\n"
                    f"Customer: *{s_first}* (@{s_user}) [ID: `{user_id}`]\n"
                    f"Status: *PAID (LUNAS)*"
                )
                await safe_send_markdown(bot, config.ADMIN_USER_ID, admin_msg)
                logger.info("Notifikasi pembayaran DANA berhasil dikirim ke admin %s", config.ADMIN_USER_ID)
            except Exception as e:
                logger.error("Gagal kirim notif admin: %s", e)

    return web.json_response({
        "success": True,
        "status": "paid",
        "order_id": order_id,
        "amount": amount,
        "message": f"Order #{order_id} berhasil diverifikasi dan ditandai PAID.",
    })


from payments import admin_web


def create_webhook_app(bot) -> web.Application:
    """Buat instance web application aiohttp terpadu (Admin Panel + DANA Webhook)."""
    app = web.Application()
    app["bot"] = bot

    webhook_path = getattr(config, "DANA_WEBHOOK_PATH", "/webhook/dana")
    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    admin_path = getattr(config, "ADMIN_WEB_PATH", "/admin").rstrip("/")
    if not admin_path.startswith("/"):
        admin_path = "/" + admin_path
    if not admin_path:
        admin_path = "/admin"

    # Root & health endpoints
    app.router.add_get("/", admin_web.handle_not_found)
    app.router.add_get("/health", handle_health)

    # DANA Webhook POST & GET
    app.router.add_get(webhook_path, admin_web.handle_not_found)
    app.router.add_post(webhook_path, handle_dana_webhook)
    if webhook_path != "/webhook/dana":
        app.router.add_get("/webhook/dana", admin_web.handle_not_found)
        app.router.add_post("/webhook/dana", handle_dana_webhook)

    # Admin Web Panel Routes
    app.router.add_get(admin_path, admin_web.handle_admin_dashboard)
    app.router.add_get(f"{admin_path}/login", admin_web.handle_login_page)
    app.router.add_post(f"{admin_path}/login", admin_web.handle_login_submit)
    app.router.add_get(f"{admin_path}/logout", admin_web.handle_logout)

    # Admin Product Actions
    app.router.add_post(f"{admin_path}/products/add", admin_web.handle_product_add)
    app.router.add_post(f"{admin_path}/products/restock", admin_web.handle_product_restock)
    app.router.add_get(f"{admin_path}/products/stock", admin_web.handle_get_product_stock)
    app.router.add_post(f"{admin_path}/products/stock/save", admin_web.handle_save_product_stock)
    app.router.add_post(f"{admin_path}/products/edit", admin_web.handle_product_edit)
    app.router.add_post(f"{admin_path}/products/delete", admin_web.handle_product_delete)

    return app
