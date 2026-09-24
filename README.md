# TeleMarketBot

Bot Telegram otomatis untuk penjualan produk digital per baris (link, akun, voucher, token, lisensi) yang terintegrasi langsung dengan QRIS Dinamis dan web admin panel.

Mendukung instalasi satu perintah untuk VPS Linux (Debian, Ubuntu, CentOS) dengan opsi Cloudflare Quick Tunnel (`try.cloudflare.com`) gratis tanpa memerlukan IP publik statis atau konfigurasi domain.

---

## Fitur

- **Instalasi Satu Perintah:** Setup otomatis seluruh dependensi Python, konfigurasi environment, dan service systemd.
- **QRIS Dinamis Otomatis:** Mengonversi payload statis merchant menjadi QRIS dinamis dengan nominal tagihan unik secara otomatis (standar EMVCo CRC-16).
- **Verifikasi Pembayaran Otomatis:** Menerima notifikasi mutasi masuk dari berbagai e-wallet atau m-banking (GoPay Merchant, DANA Bisnis, BCA, dsb) melalui webhook HTTP (dihubungkan via MacroDroid).
- **Pengiriman Stok Instan:** Format stok berbasis baris (1 baris = 1 stok). Stok otomatis dipotong dan dikirimkan ke pembeli setelah pembayaran terverifikasi.
- **Web Admin Panel:** Dashboard manajemen berbasis browser untuk melihat ringkasan pesanan, menambah atau mengedit produk, serta mengedit file stok secara langsung.
- **Cloudflare Quick Tunnel:** Opsi tunnel HTTPS publik gratis dari Cloudflare tanpa perlu membuka port router atau membeli domain.
- **Keamanan:** Path admin dan webhook dapat dikustomisasi, autentikasi session admin berbasis HMAC-SHA256, dan proteksi error 404 pada root URL.

---

## Instalasi

Jalankan perintah berikut pada terminal VPS atau server Linux Anda:

```bash
curl -fsSL https://raw.githubusercontent.com/jimmylpx/telemarketbot/main/install.sh | bash
```

Atau clone repository secara manual:

```bash
git clone https://github.com/jimmylpx/telemarketbot.git
cd telemarketbot
bash install.sh
```

Pada wizard instalasi, Anda akan diminta mengisi:
1. `TELEGRAM_BOT_TOKEN` (dari @BotFather)
2. `ADMIN_USER_ID` (ID Telegram admin dari @userinfobot)
3. `Nama Toko` (nama toko yang tampil di bot dan chat pelanggan)
4. `QRIS_BASE_PAYLOAD` (string EMVCo dari scan QRIS merchant Anda, atau upload gambar QRIS Anda ke https://qris-dana-converter.vercel.app/ untuk mengekstrak string payload-nya)
5. `Nama Merchant` (sesuai nama toko pada QRIS)
6. `Password Web Admin` (untuk login ke dashboard admin)
7. `Path Web Admin Panel` (default: `/admin`, dapat diganti misalnya `/kelola` atau `/panel`)
8. `Path Webhook QRIS` (default: `/webhook/qris`)
9. Metode Akses: Cloudflare Quick Tunnel (gratis, tanpa domain) atau Cloudflare Named Tunnel dengan Domain Sendiri

Setelah instalasi selesai, layanan bot dan tunnel akan berjalan otomatis di background sebagai service systemd.

---

## Integrasi Notifikasi (MacroDroid)

Agar bot dapat memverifikasi pembayaran secara otomatis saat pembeli mentransfer dana via QRIS:

1. Pasang aplikasi **MacroDroid** pada smartphone Android yang menerima notifikasi pembayaran/mutasi (GoPay Merchant, DANA Bisnis, BCA Mobile, dsb).
2. Buat makro baru:
   - **Trigger:**
     - Pilih `Device Events` -> `Notification` -> `Notification Received`
     - Pilih Aplikasi: **Aplikasi E-Wallet / Bank Anda**
   - **Action:**
     - Pilih `Web Interactions` -> `HTTP Request`
     - **Tab Settings:**
       - Method: **POST**
       - URL: Masukkan URL webhook bot Anda (contoh: `https://[subdomain].trycloudflare.com/webhook/qris`)
     - **Tab Content Body:**
       - Content type: `application/json`
       - Content Body: Pilih **Text**
       - Isi body request:
         ```json
         {
           "title": "{not_title}",
           "text": "{notification}",
           "secret": "TOKEN_WEBHOOK_RAHASIA_ANDA"
         }
         ```
       - *Catatan:* Ganti `TOKEN_WEBHOOK_RAHASIA_ANDA` dengan nilai `WEBHOOK_SECRET` Anda di file `.env` (default: `bottele_dana_secret_2026`).
3. Simpan dan aktifkan makro.

---

## Web Admin Panel

Dashboard admin dapat diakses melalui browser:
1. Buka URL: `https://[domain-anda]/admin` (atau sesuai path admin yang Anda tentukan).
2. Masukkan password admin yang Anda buat saat instalasi.
3. Fitur panel:
   - Ringkasan statistik transaksi dan omzet.
   - Manajemen katalog produk (tambah, edit harga, deskripsi, hapus).
   - Pengelolaan stok: modal edit langsung file `.txt` untuk menambah baris lisensi atau memantau sisa stok.
   - Riwayat transaksi pesanan pelanggan.

---

## Pengelolaan Layanan (CLI)

Gunakan perintah `telemarketbot` di terminal untuk mengelola server:

| Perintah | Deskripsi |
|---|---|
| `telemarketbot status` | Melihat status service bot, tunnel, dan link live aktif |
| `telemarketbot log` | Menampilkan log transaksi secara realtime (`Ctrl+C` untuk keluar tanpa mematikan bot) |
| `telemarketbot restart` | Me-restart service bot dan tunnel |
| `telemarketbot start` | Menjalankan bot dan tunnel |
| `telemarketbot stop` | Menghentikan bot dan tunnel |
| `telemarketbot url` | Menampilkan URL publik dan admin panel saat ini |
| `telemarketbot domain` | Mengatur dan beralih mode tunnel (TryCloudflare <-> Domain Sendiri via dash.cloudflare.com) |

Layanan telah dikonfigurasi dengan auto-start saat boot (`systemd enabled`).

---

## Struktur Direktori

```text
├── bot.py                     # Entry point bot Telegram & webhook runner
├── config.py                  # Pengelola konfigurasi environment (.env)
├── db.py                      # Database SQLite (users, products, orders)
├── install.sh                 # Script instalasi interaktif
├── requirements.txt           # Dependensi Python
├── .env.example               # Template variabel environment
├── .gitignore                 # Filter proteksi file sensitif & lisensi
│
├── handlers/                  # Telegram Bot Handlers
│   ├── admin.py               # Perintah khusus admin (/admin, /orders, dll.)
│   ├── myorders.py            # Riwayat pesanan pembeli (/myorders)
│   ├── product.py             # Alur pembelian produk (katalog, order, payment)
│   └── start.py               # Menu utama (/start, /help)
│
├── payments/                  # Modul Pembayaran & Web
│   ├── admin_web.py           # Web Admin Panel HTML & handler
│   ├── webhook.py             # Receiver webhook notifikasi pembayaran & mutasi QRIS
│   ├── dana_webhook.py        # Backward compatibility shim (/webhook/dana)
│   ├── delivery.py            # Pengiriman stok otomatis ke pembeli
│   └── qris_generator.py      # Generator QRIS Dinamis (CRC-16)
│
├── scripts/                   # Utility Scripts
│   ├── cloudflare_setup.py    # Setup Cloudflare Named Tunnel dengan Domain Sendiri
│   └── tunnel_manager.py      # Supervisor Cloudflare Quick Tunnel
│
└── data/                      # Direktori data (ter-ignore dari Git)
    ├── bot.db                 # Database SQLite
    └── stocks/                # File stok produk (*.txt)
```

---

## Keamanan

1. File stok (`data/stocks/`), database (`data/bot.db`), data pesanan (`orders/`), dan file `.env` diabaikan oleh Git via `.gitignore`.
2. Sesi login admin menggunakan cookie HttpOnly yang ditandatangani HMAC-SHA256.
3. Webhook memverifikasi secret token pada setiap payload masuk.
4. Akses ke root URL (`/`) menghasilkan respon HTTP 404.

---

## Lisensi

Proyek ini menggunakan lisensi MIT.
