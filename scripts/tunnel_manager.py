#!/usr/bin/env python3
"""
Cloudflare Quick Tunnel Manager (try.cloudflare.com)
Otomatis menjalankan tunnel, mengekstrak URL publik ephemeral,
mengupdate .env / .current_tunnel_url, dan mengirim notifikasi link ke Telegram Admin.
"""

import os
import re
import sys
import time
import signal
import logging
import subprocess
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None

# Konfigurasi logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] [TunnelManager] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("TunnelManager")

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
TUNNEL_URL_FILE = BASE_DIR / ".current_tunnel_url"

# Regex untuk mencocokkan URL Cloudflare Quick Tunnel
CF_URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def load_env_vars() -> dict:
    """Membaca konfigurasi dari file .env secara mandiri tanpa dependensi."""
    env_vars = {}
    if ENV_FILE.is_file():
        try:
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip("'\"")
        except Exception as exc:
            logger.warning("Gagal membaca .env: %s", exc)
    return env_vars


def update_env_public_url(new_url: str):
    """Memperbarui variabel PUBLIC_URL pada file .env secara aman."""
    if not ENV_FILE.is_file():
        return
    try:
        content = ENV_FILE.read_text(encoding="utf-8")
        if re.search(r"^PUBLIC_URL=.*$", content, flags=re.MULTILINE):
            updated = re.sub(
                r"^PUBLIC_URL=.*$",
                f"PUBLIC_URL={new_url}",
                content,
                flags=re.MULTILINE,
            )
        else:
            updated = content + f"\nPUBLIC_URL={new_url}\n"
        ENV_FILE.write_text(updated, encoding="utf-8")
        logger.info("PUBLIC_URL di .env diperbarui ke: %s", new_url)
    except Exception as exc:
        logger.error("Gagal memperbarui PUBLIC_URL di .env: %s", exc)


def send_telegram_notification(bot_token: str, admin_id: str, tunnel_url: str, port: int):
    """Mengirim pesan notifikasi link live tunnel ke admin via Telegram Bot API."""
    if not bot_token or not admin_id:
        logger.info("Bot token atau Admin ID tidak diset di .env, lewati notifikasi Telegram.")
        return

    admin_link = f"{tunnel_url}/admin"
    webhook_link = f"{tunnel_url}/webhook/dana"

    msg = (
        "🌐 *Cloudflare Quick Tunnel Online!*\n\n"
        f"🔗 *Public URL:* `{tunnel_url}`\n"
        f"⚙️ *Admin Web Panel:* `{admin_link}`\n"
        f"🔔 *Webhook DANA (MacroDroid):*\n`{webhook_link}`\n\n"
        f"💡 _Terhubung ke server lokal port {port}_"
    )

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": admin_id,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        if httpx:
            resp = httpx.post(api_url, json=payload, timeout=10.0)
            if resp.status_code == 200:
                logger.info("Notifikasi link tunnel berhasil dikirim ke Admin ID %s", admin_id)
            else:
                logger.warning("Gagal mengirim pesan Telegram: HTTP %s - %s", resp.status_code, resp.text)
        else:
            # Fallback pakai urllib jika httpx belum terpasang
            import urllib.request
            import json
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    logger.info("Notifikasi link tunnel terkirim via urllib ke Admin ID %s", admin_id)
    except Exception as exc:
        logger.error("Error saat mengirim notifikasi Telegram: %s", exc)


def find_cloudflared_binary() -> Optional[str]:
    """Mencari lokasi binary cloudflared."""
    # 1. Cek di PATH
    path_bin = shutil_which("cloudflared")
    if path_bin:
        return path_bin

    # 2. Cek path standar Linux / lokal
    candidates = [
        "/usr/local/bin/cloudflared",
        "/usr/bin/cloudflared",
        str(BASE_DIR / "cloudflared"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def shutil_which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd)


def main():
    logger.info("Memulai Cloudflare Tunnel Manager...")
    env_vars = load_env_vars()

    port = int(env_vars.get("WEBHOOK_PORT", 8085))
    bot_token = env_vars.get("TELEGRAM_BOT_TOKEN", "")
    admin_id = env_vars.get("ADMIN_USER_ID", "")

    cf_bin = find_cloudflared_binary()
    if not cf_bin:
        logger.critical(
            "Binary 'cloudflared' tidak ditemukan! "
            "Silakan pasang cloudflared terlebih dahulu (atau jalankan install.sh)."
        )
        sys.exit(1)

    logger.info("Menggunakan binary cloudflared: %s", cf_bin)
    logger.info("Target reverse-proxy: http://127.0.0.1:%d", port)

    cmd = [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{port}"]

    current_process = None

    def handle_exit(signum, frame):
        logger.info("Menerima sinyal keluar (%s), mematikan tunnel...", signum)
        if current_process:
            current_process.terminate()
            try:
                current_process.wait(timeout=5)
            except Exception:
                current_process.kill()
        if TUNNEL_URL_FILE.exists():
            try:
                TUNNEL_URL_FILE.unlink()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    last_url = ""

    while True:
        try:
            logger.info("Menjalankan: %s", " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            current_process = proc

            for line in iter(proc.stdout.readline, ""):
                line_str = line.strip()
                if not line_str:
                    continue

                # Cek apakah baris mengandung URL trycloudflare.com
                match = CF_URL_REGEX.search(line_str)
                if match:
                    found_url = match.group(0)
                    if found_url != last_url:
                        last_url = found_url
                        logger.info("==================================================")
                        logger.info("🚀 CLOUDFLARE QUICK TUNNEL LIVE: %s", found_url)
                        logger.info("⚙️  Admin Panel: %s/admin", found_url)
                        logger.info("🔔 Webhook URL: %s/webhook/dana", found_url)
                        logger.info("==================================================")

                        # 1. Simpan ke .current_tunnel_url
                        try:
                            TUNNEL_URL_FILE.write_text(found_url, encoding="utf-8")
                        except Exception as exc:
                            logger.error("Gagal menulis file tunnel URL: %s", exc)

                        # 2. Update .env
                        update_env_public_url(found_url)

                        # 3. Notifikasi Telegram Admin
                        # Muat ulang env_vars jika admin baru mengisi data
                        fresh_env = load_env_vars()
                        t_token = fresh_env.get("TELEGRAM_BOT_TOKEN", bot_token)
                        t_admin = fresh_env.get("ADMIN_USER_ID", admin_id)
                        send_telegram_notification(t_token, t_admin, found_url, port)

            proc.wait()
            ret_code = proc.returncode
            logger.warning("Proses cloudflared terhenti (return code %d). Mencoba restart dalam 5 detik...", ret_code)
            time.sleep(5)

        except Exception as exc:
            logger.error("Terjadi kesalahan pada loop tunnel: %s. Restart dalam 5 detik...", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
