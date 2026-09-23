#!/usr/bin/env bash
# ==============================================================================
# Installer Otomatis: Telegram Auto Order Bot & DANA Bisnis Gateway
# Mendukung Debian, Ubuntu, CentOS, Rocky Linux, AlmaLinux
# Termasuk Cloudflare Quick Tunnel (try.cloudflare.com)
# ==============================================================================

set -e

# Warna Terminal
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

REPO_URL="https://github.com/jimmylpx/telemarketbot.git"
INSTALL_DIR="telemarketbot"

echo -e "${CYAN}${BOLD}"
echo "=================================================================="
echo "    🚀 INSTALLER TELEGRAM AUTO ORDER & DANA BISNIS GATEWAY"
echo "           Dengan Cloudflare Quick Tunnel (try.cloudflare.com)"
echo "=================================================================="
echo -e "${NC}"

# 1. Cek Sudo / Root
if [ "$EUID" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
        echo -e "${RED}[ERROR] Script membutuhkan akses sudo atau root.${NC}"
        exit 1
    fi
    SUDO="sudo"
else
    SUDO=""
fi

# 2. Deteksi apakah dijalankan via pipe atau clone langsung
if [ ! -f "bot.py" ] || [ ! -f "requirements.txt" ]; then
    echo -e "${BLUE}[*] Mengunduh repository dari GitHub...${NC}"
    if ! command -v git >/dev/null 2>&1; then
        echo -e "${YELLOW}[*] Memasang git...${NC}"
        if command -v apt-get >/dev/null 2>&1; then
            $SUDO apt-get update -y && $SUDO apt-get install -y git
        elif command -v yum >/dev/null 2>&1; then
            $SUDO yum install -y git
        fi
    fi

    if [ ! -d "$INSTALL_DIR" ]; then
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi
    cd "$INSTALL_DIR"
fi

PROJECT_DIR=$(pwd)
CURRENT_USER=$(logname 2>/dev/null || echo "$SUDO_USER" || whoami)
if [ "$CURRENT_USER" = "root" ] && [ -n "$SUDO_USER" ]; then
    CURRENT_USER="$SUDO_USER"
fi
CURRENT_GROUP=$(id -gn "$CURRENT_USER" 2>/dev/null || echo "$CURRENT_USER")

echo -e "${GREEN}[+] Direktori Instalasi: ${PROJECT_DIR}${NC}"
echo -e "${GREEN}[+] Menjalankan sebagai user: ${CURRENT_USER}${NC}"

# 3. Update Package Manager & Pasang Dependensi Sistem
echo -e "\n${BLUE}[*] Memeriksa paket dependensi sistem (Python 3, venv, pip, curl)...${NC}"
if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y
    $SUDO apt-get install -y python3 python3-venv python3-pip curl wget openssl
elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y python3 python3-pip curl wget openssl
elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-pip curl wget openssl
fi

# 4. Pasang Cloudflared Binary
echo -e "\n${BLUE}[*] Memeriksa binary cloudflared...${NC}"
if ! command -v cloudflared >/dev/null 2>&1; then
    ARCH=$(uname -m)
    CF_BIN_URL=""
    if [ "$ARCH" = "x86_64" ]; then
        CF_BIN_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
        CF_BIN_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
    elif [ "$ARCH" = "armv7l" ]; then
        CF_BIN_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm"
    else
        echo -e "${YELLOW}[!] Arsitektur $ARCH tidak dikenali untuk auto-download cloudflared.${NC}"
    fi

    if [ -n "$CF_BIN_URL" ]; then
        echo -e "${YELLOW}[*] Mengunduh cloudflared ($ARCH)...${NC}"
        $SUDO curl -fsSL -o /usr/local/bin/cloudflared "$CF_BIN_URL"
        $SUDO chmod +x /usr/local/bin/cloudflared
        echo -e "${GREEN}[+] Cloudflared berhasil dipasang di /usr/local/bin/cloudflared${NC}"
    fi
else
    echo -e "${GREEN}[+] Cloudflared sudah terpasang: $(cloudflared --version)${NC}"
fi

# 5. Panduan Input Interaktif
echo -e "\n${CYAN}${BOLD}------------------------------------------------------------------"
echo "            KONFIGURASI PENGATURAN BOT & SERVER"
echo "------------------------------------------------------------------${NC}"

# Token Bot
read -rp "$(echo -e "${BOLD}1. Masukkan TELEGRAM_BOT_TOKEN dari @BotFather: ${NC}")" INP_BOT_TOKEN
while [ -z "$INP_BOT_TOKEN" ]; do
    echo -e "${RED}Token bot tidak boleh kosong!${NC}"
    read -rp "Masukkan TELEGRAM_BOT_TOKEN: " INP_BOT_TOKEN
done

# Admin ID
read -rp "$(echo -e "${BOLD}2. Masukkan ID Telegram Admin (dapatkan via @userinfobot): ${NC}")" INP_ADMIN_ID
while [ -z "$INP_ADMIN_ID" ]; do
    echo -e "${RED}Admin ID tidak boleh kosong!${NC}"
    read -rp "Masukkan ID Telegram Admin: " INP_ADMIN_ID
done

# Nama Toko
read -rp "$(echo -e "${BOLD}3. Nama Toko (Default: IDLisensi): ${NC}")" INP_SHOP_NAME
INP_SHOP_NAME=${INP_SHOP_NAME:-IDLisensi}

# Payload QRIS DANA Bisnis
echo -e "\n${YELLOW}Petunjuk QRIS DANA Bisnis:${NC}"
echo "Scan QRIS statis DANA Bisnis Anda menggunakan aplikasi scanner barcode,"
echo "lalu copy teks hasilnya (format EMVCo, diawali 000201010211...)"
read -rp "$(echo -e "${BOLD}4. Masukkan QRIS_BASE_PAYLOAD DANA Bisnis: ${NC}")" INP_QRIS_PAYLOAD
while [ -z "$INP_QRIS_PAYLOAD" ]; do
    echo -e "${RED}QRIS_BASE_PAYLOAD tidak boleh kosong!${NC}"
    read -rp "Masukkan QRIS_BASE_PAYLOAD: " INP_QRIS_PAYLOAD
done

# Nama Merchant
read -rp "$(echo -e "${BOLD}5. Masukkan Nama Merchant QRIS DANA (Sesuai aplikasi DANA): ${NC}")" INP_MERCHANT_NAME
while [ -z "$INP_MERCHANT_NAME" ]; do
    echo -e "${RED}Nama Merchant tidak boleh kosong!${NC}"
    read -rp "Masukkan Nama Merchant: " INP_MERCHANT_NAME
done

# Password Admin Web Panel
RANDOM_PASS=$(openssl rand -base64 6 | tr -dc 'a-zA-Z0-9')
read -rp "$(echo -e "${BOLD}6. Password Admin Web Panel /admin (Default: $RANDOM_PASS): ${NC}")" INP_ADMIN_PASSWORD
INP_ADMIN_PASSWORD=${INP_ADMIN_PASSWORD:-$RANDOM_PASS}

# Port Webhook
read -rp "$(echo -e "${BOLD}7. Port Webhook Lokal (Default: 8085): ${NC}")" INP_WEBHOOK_PORT
INP_WEBHOOK_PORT=${INP_WEBHOOK_PORT:-8085}

# Webhook Secret Token
RANDOM_SECRET=$(openssl rand -hex 12)
read -rp "$(echo -e "${BOLD}8. Secret Token Webhook (Default: $RANDOM_SECRET): ${NC}")" INP_WEBHOOK_SECRET
INP_WEBHOOK_SECRET=${INP_WEBHOOK_SECRET:-$RANDOM_SECRET}

# Pilihan Tunnel
echo -e "\n${BOLD}9. Pilih Metode Akses Publik / Online Webhook:${NC}"
echo "  1) Cloudflare Quick Tunnel (try.cloudflare.com) [Rekomendasi - Gratis, Tanpa Domain]"
echo "  2) Custom Domain / Reverse Proxy Sendiri (Pangolin, Nginx, dll.)"
read -rp "Pilihan Anda (1/2, default: 1): " INP_TUNNEL_CHOICE
INP_TUNNEL_CHOICE=${INP_TUNNEL_CHOICE:-1}

USE_CF="true"
INP_PUBLIC_URL="https://try.cloudflare.com"

if [ "$INP_TUNNEL_CHOICE" = "2" ]; then
    USE_CF="false"
    read -rp "Masukkan URL Publik Anda (contoh: https://toko.domainanda.com): " INP_PUBLIC_URL
fi

# 6. Tulis file .env
echo -e "\n${BLUE}[*] Menyimpan konfigurasi ke .env...${NC}"
cat <<EOF > "$PROJECT_DIR/.env"
# Konfigurasi Bot Telegram & DANA Bisnis Gateway
TELEGRAM_BOT_TOKEN=${INP_BOT_TOKEN}
ADMIN_USER_ID=${INP_ADMIN_ID}
SHOP_NAME=${INP_SHOP_NAME}

# QRIS DANA Bisnis
DANA_AUTO_CHECK=true
QRIS_BASE_PAYLOAD=${INP_QRIS_PAYLOAD}
MERCHANT_NAME=${INP_MERCHANT_NAME}
PAYMENT_BANK=QRIS DANA Bisnis
PAYMENT_NUMBER=QRIS Dinamis
PAYMENT_NAME=${INP_MERCHANT_NAME}

# Webhook & Tunnel
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=${INP_WEBHOOK_PORT}
WEBHOOK_SECRET=${INP_WEBHOOK_SECRET}
PUBLIC_URL=${INP_PUBLIC_URL}
USE_CLOUDFLARE_TUNNEL=${USE_CF}
ORDER_EXPIRE_MINUTES=30

# Web Admin Panel (/admin)
ADMIN_WEB_PASSWORD=${INP_ADMIN_PASSWORD}
ADMIN_SESSION_SECRET=$(openssl rand -hex 16)

# Database & Stocks
DB_PATH=data/bot.db
ORDERS_DIR=orders
STOCKS_DIR=data/stocks
EOF

chmod 600 "$PROJECT_DIR/.env"
echo -e "${GREEN}[+] File .env berhasil dibuat dengan izin terbatas (600).${NC}"

# 7. Siapkan Folder Data
echo -e "\n${BLUE}[*] Menyiapkan direktori penyimpanan data...${NC}"
mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/data/stocks" "$PROJECT_DIR/orders" "$PROJECT_DIR/scripts" "$PROJECT_DIR/logs"
touch "$PROJECT_DIR/data/stocks/.gitkeep" "$PROJECT_DIR/orders/.gitkeep"

# 8. Setup Python Virtual Environment & Install Dependencies
echo -e "\n${BLUE}[*] Menyiapkan Python Virtual Environment (venv)...${NC}"
if [ ! -d "$PROJECT_DIR/venv" ]; then
    python3 -m venv "$PROJECT_DIR/venv"
fi

"$PROJECT_DIR/venv/bin/pip" install --upgrade pip
echo -e "${BLUE}[*] Memasang dependensi dari requirements.txt...${NC}"
"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# 9. Inisialisasi Database SQLite
echo -e "\n${BLUE}[*] Menginisialisasi Database SQLite...${NC}"
"$PROJECT_DIR/venv/bin/python" -c "import db; db.init_db('data/bot.db')"
echo -e "${GREEN}[+] Database SQLite siap: data/bot.db${NC}"

# 10. Konfigurasi Systemd Service
echo -e "\n${BLUE}[*] Mengonfigurasi systemd service...${NC}"

# A. telemarketbot.service
$SUDO bash -c "cat <<EOF > /etc/systemd/system/telemarketbot.service
[Unit]
Description=TeleMarketBot — Telegram Auto Order Bot & DANA Webhook Server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_GROUP}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/venv/bin/python bot.py
Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=telemarketbot

[Install]
WantedBy=multi-user.target
EOF"

# B. telemarketbot-tunnel.service (jika memilih Cloudflare Tunnel)
if [ "$USE_CF" = "true" ]; then
    $SUDO bash -c "cat <<EOF > /etc/systemd/system/telemarketbot-tunnel.service
[Unit]
Description=Cloudflare Quick Tunnel for TeleMarketBot
After=network.target network-online.target telemarketbot.service
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_GROUP}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/venv/bin/python scripts/tunnel_manager.py
Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=telemarketbot-tunnel

[Install]
WantedBy=multi-user.target
EOF"
fi

# Reload and Enable Services
$SUDO systemctl daemon-reload
$SUDO systemctl enable telemarketbot.service
$SUDO systemctl restart telemarketbot.service

if [ "$USE_CF" = "true" ]; then
    $SUDO systemctl enable telemarketbot-tunnel.service
    $SUDO systemctl restart telemarketbot-tunnel.service
fi

echo -e "\n${GREEN}${BOLD}=================================================================="
echo "                   INSTALASI SELESAI & BERHASIL! 🚀"
echo "==================================================================${NC}"
echo -e "Bot Telegram: ${GREEN}AKTIF${NC} (Layanan: telemarketbot.service)"
if [ "$USE_CF" = "true" ]; then
    echo -e "Cloudflare Tunnel: ${GREEN}AKTIF${NC} (Layanan: telemarketbot-tunnel.service)"
    echo -e "${YELLOW}URL Quick Tunnel sedang dibuat dan akan otomatis dikirimkan ke Telegram Admin Anda!${NC}"
fi
echo -e "\n${BOLD}Informasi Akses & Kredensial:${NC}"
echo -e "• Web Admin Panel   : ${CYAN}/admin${NC}"
echo -e "• Password Web Admin: ${YELLOW}${INP_ADMIN_PASSWORD}${NC}"
echo -e "• Webhook DANA Path : ${CYAN}/webhook/dana${NC}"
echo -e "• Webhook Secret    : ${YELLOW}${INP_WEBHOOK_SECRET}${NC}"

echo -e "\n${BOLD}Perintah Pengelolaan:${NC}"
echo -e "• Cek status bot    : ${CYAN}sudo systemctl status telemarketbot${NC}"
if [ "$USE_CF" = "true" ]; then
    echo -e "• Cek status tunnel : ${CYAN}sudo systemctl status telemarketbot-tunnel${NC}"
    echo -e "• Cek URL live      : ${CYAN}cat .current_tunnel_url${NC}"
fi
echo -e "• Pantau log bot    : ${CYAN}sudo journalctl -u telemarketbot -f${NC}"
echo -e "• Restart bot       : ${CYAN}sudo systemctl restart telemarketbot${NC}"
echo -e "==================================================================\n"
