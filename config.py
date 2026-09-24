"""Konfigurasi bot order telegram dan QRIS Webhook."""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")


def _get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_int_env(key: str, default: int = 0) -> int:
    val = os.getenv(key, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _resolve_path(env_key: str, default_subpath: str) -> str:
    raw = _get_env(env_key, default_subpath)
    p = Path(raw)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


def _clean_path(path_val: str, default: str = "") -> str:
    val = (path_val or default).strip()
    if not val.startswith("/"):
        val = "/" + val
    return val.rstrip("/") if val != "/" else "/"


BOT_TOKEN: str = _get_env("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID: int = _get_int_env("ADMIN_USER_ID", 0)
ADMIN_CONTACT: str = _get_env("ADMIN_CONTACT", "").strip()

SHOP_NAME: str = _get_env("SHOP_NAME", "Toko DANA Bot")
DB_PATH: str = _resolve_path("DB_PATH", "data/bot.db")

# QRIS Dinamis Otomatis (GoPay Merchant, DANA Bisnis, Bank, dsb)
QRIS_AUTO_CHECK: bool = _get_env("QRIS_AUTO_CHECK", _get_env("DANA_AUTO_CHECK", "true")).lower() in ("true", "1", "yes")
DANA_AUTO_CHECK: bool = QRIS_AUTO_CHECK  # Alias backward compatibility

QRIS_BASE_PAYLOAD: str = _get_env(
    "QRIS_BASE_PAYLOAD",
    "00020101021126610014COM.GO-JEK.WWW01189360091436257905280210G6257905280303UMI51440014ID.CO.QRIS.WWW0215ID10266017203150303UMI5204899953033605802ID5909IDLisensi6014KONAWE SELATAN61059387062070703A0163043042"
)
MERCHANT_NAME: str = _get_env("MERCHANT_NAME", "IDLisensi")

# Fallback info pembayaran manual jika diperlukan
PAYMENT_BANK: str = _get_env("PAYMENT_BANK", "QRIS (Semua E-Wallet & Bank)")
PAYMENT_NUMBER: str = _get_env("PAYMENT_NUMBER", "QRIS Dinamis")
PAYMENT_NAME: str = _get_env("PAYMENT_NAME", MERCHANT_NAME)

# Webhook & Tunnel
WEBHOOK_HOST: str = _get_env("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT: int = _get_int_env("WEBHOOK_PORT", 8085)
WEBHOOK_SECRET: str = _get_env("WEBHOOK_SECRET", "bottele_dana_secret_2026")
WEBHOOK_PATH: str = _clean_path(_get_env("WEBHOOK_PATH", _get_env("DANA_WEBHOOK_PATH", "/webhook/dana")), "/webhook/dana")
DANA_WEBHOOK_PATH: str = WEBHOOK_PATH  # Alias backward compatibility
PUBLIC_URL: str = _get_env("PUBLIC_URL", "https://bottele.bijiflix2.dpdns.org")
USE_CLOUDFLARE_TUNNEL: bool = _get_env("USE_CLOUDFLARE_TUNNEL", "false").lower() in ("true", "1", "yes")
ORDER_EXPIRE_MINUTES: int = _get_int_env("ORDER_EXPIRE_MINUTES", 30)


def get_public_url() -> str:
    """Mengembalikan URL publik aktif. Mendukung Cloudflare Quick Tunnel (.current_tunnel_url) atau fallback ke PUBLIC_URL."""
    tunnel_file = BASE_DIR / ".current_tunnel_url"
    if tunnel_file.is_file():
        try:
            url = tunnel_file.read_text(encoding="utf-8").strip()
            if url.startswith("http"):
                return url
        except Exception:
            pass
    return PUBLIC_URL


# Auto-Delivery Stock Config
STOCK_FILE_PATH: str = _resolve_path("STOCK_FILE_PATH", "data/stocks/stock_default.txt")
ORDERS_DIR: str = _resolve_path("ORDERS_DIR", "orders")
STOCKS_DIR: str = _resolve_path("STOCKS_DIR", "data/stocks")


# CID & OCR Config
CID_BASE_URL: str = _get_env("CID_BASE_URL", "https://cid.idlisensi.com")
GEMINI_API_KEY: str = _get_env("GEMINI_API_KEY", "")
GEMINI_OCR_MODEL: str = _get_env("GEMINI_OCR_MODEL", "gemini-3.5-flash-lite")


# Admin Web Panel Config
ADMIN_WEB_PASSWORD: str = _get_env("ADMIN_WEB_PASSWORD", "paloco46")
ADMIN_SESSION_SECRET: str = _get_env("ADMIN_SESSION_SECRET", "idlisensi_secret_panel_key_2026")
