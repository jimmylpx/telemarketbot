# 🤖 TeleMarketBot — Telegram Auto Order & DANA Bisnis Gateway

<p align="center">
  <img src="https://raw.githubusercontent.com/jimmylpx/telemarketbot/main/assets/logo.svg" alt="Logo" width="160">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-2ea44f?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/Tunnel-Cloudflare%20Quick%20Tunnel-F38020?style=flat-square&logo=cloudflare&logoColor=white" alt="Cloudflare">
  <img src="https://img.shields.io/badge/Payment-QRIS%20DANA%20Bisnis-118EEA?style=flat-square" alt="DANA">
  <img src="https://img.shields.io/badge/Products-Per%20Line%20Digital-success?style=flat-square" alt="Products">
  <img src="https://img.shields.io/badge/Status-Production%20Ready-success?style=flat-square" alt="Status">
</p>

---

Solusi self-hosted lengkap untuk bot Telegram jualan produk digital berbasis baris teks otomatis (seperti **link Jio**, akun streaming/login, token, voucher, cookies, lisensi). Terintegrasi langsung dengan **QRIS Dinamis DANA Bisnis**, **Web Admin Panel**, dan **Instant Auto-Delivery stok**.

Dapat diinstal di VPS Linux manapun secara instan menggunakan **One-Command Installer** dengan dukungan **Cloudflare Quick Tunnel (`try.cloudflare.com`)** gratis tanpa perlu membeli domain atau konfigurasi port-forwarding!

---

## 🌟 Fitur Unggulan

- ⚡ **One-Command Installation:** Cukup satu baris perintah untuk memasang seluruh dependensi, mengonfigurasi `.env`, dan mendaftarkan service background (`systemd`).
- 🌐 **Cloudflare Quick Tunnel (`try.cloudflare.com`):** Webhook dan Admin Panel otomatis online ke publik dengan HTTPS resmi secara gratis tanpa perlu menyewa IP publik atau membeli domain. Link live otomatis dikirimkan ke Telegram Admin setiap kali server startup.
- 💳 **QRIS Dinamis DANA Bisnis (Auto EMVCo):** Bot secara otomatis menyuntikkan nominal tagihan ke QRIS statis DANA Bisnis menggunakan kalkulasi checksum CRC-16 standar Bank Indonesia. Pembeli tinggal scan tanpa perlu ketik nominal manual!
- 🔔 **Auto-Check Pembayaran (MacroDroid Webhook):** Setiap ada uang masuk ke aplikasi DANA Bisnis di HP Anda, MacroDroid langsung meneruskan notifikasi ke Webhook server. Pesanan diverifikasi lunas dalam hitungan detik.
- 📦 **Instant Auto-Delivery (1 Baris = 1 Stok):** Setiap baris di file stok merepresentasikan 1 item produk (misal 1 link Jio atau 1 akun). Setelah pembayaran terverifikasi lunas, bot langsung memotong stok baris tersebut dan mengirimkannya ke chat pembeli secara realtime.
- 🛠️ **Web Admin Panel (`/admin`):** Dashboard manajemen responsif untuk melihat statistik penjualan, riwayat pesanan pelanggan, menambah/mengedit produk, dan mengedit file stok `.txt` langsung via textarea web.
- 🔒 **Aman & Terisolasi:** URL root (`/`) diproteksi dengan response `404 Not Found`. Akses admin hanya melalui path `/admin` dengan autentikasi session terenkripsi HMAC-SHA256. Database, lisensi, dan data order dilindungi dari git (`.gitignore`).

---

## 🚀 Instalasi Cepat (One-Command Install)

Jalankan perintah berikut di terminal Linux / VPS Anda (Debian, Ubuntu, CentOS, Rocky Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/jimmylpx/telemarketbot/main/install.sh | bash
```

*Atau jika ingin meng-clone repository terlebih dahulu:*

```bash
git clone https://github.com/jimmylpx/telemarketbot.git
cd telemarketbot
bash install.sh
```

Installer interaktif akan memandu Anda untuk mengisi:
1. `TELEGRAM_BOT_TOKEN` (dari [@BotFather](https://t.me/BotFather))
2. `ADMIN_USER_ID` (ID angka Telegram Anda dari [@userinfobot](https://t.me/userinfobot))
3. `Nama Toko` (contoh: IDLisensi)
4. `QRIS_BASE_PAYLOAD` (hasil scan string QRIS DANA Bisnis Anda)
5. `Nama Merchant DANA` (sesuai yang tertera di DANA)
6. `Password Web Admin` (untuk login ke Admin Panel)
7. `Path Web Admin Panel` (kustomisasi URL admin, default: `/admin`, contoh: `/kelola`, `/panel`)
8. `Path Webhook DANA` (kustomisasi URL webhook, default: `/webhook/dana`, contoh: `/api/dana`)
9. Pilihan Tunnel: **Cloudflare Quick Tunnel (`try.cloudflare.com`)** (Gratis, rekomendasi) atau Custom Domain.

Setelah wizard selesai, bot dan tunnel otomatis berjalan sebagai **systemd service** di background!

---

## 📱 Panduan Integrasi MacroDroid (DANA Bisnis Auto-Check)

Agar bot dapat memverifikasi pembayaran secara otomatis saat pembeli mentransfer dana via QRIS:

1. Pasang aplikasi **[MacroDroid](https://play.google.com/store/apps/details?id=com.arlosoft.macrodroid)** di smartphone Android tempat akun DANA Bisnis Anda login.
2. Buat Makro Baru:
   - **Trigger (Pemicu):**
     - Pilih `Device Events` -> `Notification` -> `Notification Received`
     - Pilih Aplikasi: **DANA**
     - Text Content: `Matches (Berisi)` -> isi kata kunci seperti `berhasil`, `menerima`, `Rp`, atau kosongkan agar menangkap semua notifikasi dari aplikasi DANA.
   - **Actions (Tindakan):**
     - Pilih `Web Interactions` -> `HTTP Request`
     - **Tab Settings:**
       - Method: **POST**
       - URL: Masukkan URL Webhook Anda, contoh:  
         `https://[subdomain].trycloudflare.com/webhook/dana`  
         *(Link ini dikirimkan ke Telegram Admin Anda saat tunnel aktif, atau cek via `telemarketbot url`)*
     - **Tab Content Body:**
       - Content type: `application/json`
       - Content Body: Pilih opsi radio button **Text**
       - Masukkan format JSON persis seperti berikut:
         ```json
         {
           "title": "{not_title}",
           "text": "{notification}",
           "secret": "bottele_dana_secret_2026"
         }
         ```
       - *Catatan:*
         - `{not_title}` dan `{notification}` adalah tag magic variable MacroDroid (dapat dipilih via menu `...` di pojok kanan textfield).
         - Nilai `"secret"` harus cocok dengan `WEBHOOK_SECRET` di `.env` (default: `bottele_dana_secret_2026`).
3. Simpan dan aktifkan Makro.
4. Lakukan uji coba transfer QRIS Rp 1.000. Bot akan otomatis mengenali nominal unik dan mengirimkan produk ke pembeli!

---

## 💻 Web Admin Panel (`/admin`)

Untuk mengelola toko melalui browser:
1. Buka URL: `https://domain-anda.trycloudflare.com/admin` (atau domain kustom Anda).
2. Masukkan password admin yang Anda tentukan saat instalasi.
3. Fitur di Web Admin:
   - **Statistik:** Total pesanan, pesanan sukses, omzet penjualan, dan produk terdaftar.
   - **Manajemen Produk:** Tambah produk baru, ubah harga, ganti deskripsi, atau hapus produk.
   - **Edit Stok Langsung (`.txt`):** Klik tombol **Edit Stok** pada kartu produk. Sebuah modal textarea akan muncul, memungkinkan Anda menambah baris serial key / link lisensi baru atau menghapus stok lama secara realtime.
   - **Riwayat Transaksi:** Memantau 40 transaksi terakhir beserta status bayar (`paid`, `pending`, `cancelled`).

---

## ⚙️ Pengelolaan Layanan (CLI Tool)

TeleMarketBot dilengkapi perintah CLI global `telemarketbot` yang dapat dipanggil dari folder mana saja tanpa perlu mengetik perintah `systemctl` manual:

| Perintah | Fungsi |
|---|---|
| `telemarketbot status` | Cek status bot, Cloudflare tunnel, dan URL live dalam 1 tampilan |
| `telemarketbot log` | Pantau log realtime transaksi (tekan `Ctrl+C` kapan saja untuk keluar, server tetap aman) |
| `telemarketbot restart` | Restart server bot & Cloudflare tunnel |
| `telemarketbot start` | Jalankan bot & tunnel (sekaligus mengaktifkan auto-start saat reboot) |
| `telemarketbot stop` | Hentikan sementara server bot & tunnel |
| `telemarketbot url` | Cetak link live publik & admin panel saat ini |

> **Auto-Start on Boot:** Layanan otomatis terdaftar di `systemd` dengan status `enabled` sehingga bot dan tunnel akan otomatis menyala kembali setiap kali server reboot / mati listrik.

---

## 📁 Struktur Proyek

```text
├── bot.py                     # Entry point bot Telegram & webhook runner
├── config.py                  # Pengelola konfigurasi & path environment (.env)
├── db.py                      # Abstraksi database SQLite (users, products, orders)
├── install.sh                 # One-command installer interaktif
├── requirements.txt           # Daftar dependensi Python
├── .env.example               # Template environment variables
├── .gitignore                 # Filter proteksi file sensitif & lisensi
│
├── handlers/                  # Telegram Bot Handlers
│   ├── admin.py               # Perintah Telegram khusus admin (/admin, /orders, dll.)
│   ├── myorders.py            # Riwayat pesanan pembeli (/myorders)
│   ├── product.py             # Alur pembelian produk (katalog, qty, payment)
│   └── start.py               # Menu utama (/start, /help)
│
├── payments/                  # Modul Pembayaran & Web
│   ├── admin_web.py           # Web Admin Panel HTML & Handler (/admin)
│   ├── dana_webhook.py        # Receiver webhook notifikasi DANA dari MacroDroid
│   ├── delivery.py            # Auto-delivery stok produk ke pembeli
│   ├── qris_generator.py      # Generator QRIS DANA Dinamis (EMVCo CRC-16)
│   └── klikqris.py            # Gateway alternatif KlikQRIS (opsional)
│
├── scripts/                   # Helper Scripts
│   └── tunnel_manager.py      # Cloudflare Quick Tunnel supervisor & Telegram notifier
│
└── data/                      # Direktori data (ter-ignore dari git)
    ├── bot.db                 # Database SQLite
    └── stocks/                # File-file stok lisensi produk (*.txt)
```

---

## 🛡️ Keamanan & Privasi

1. **File Lisensi & Stok Terproteksi:** Seluruh file `.txt` stok produk, database `bot.db`, folder `orders/`, dan `.env` telah didaftarkan pada `.gitignore` sehingga tidak akan pernah ter-commit ke Git publik.
2. **Autentikasi Cookie Aman:** Session login admin diproteksi dengan HMAC-SHA256 signature berbasis `ADMIN_SESSION_SECRET` dan cookie `HttpOnly`.
3. **Webhook Secret Token:** Endpoint `/webhook/dana` memvalidasi `secret` token pada setiap request masuk untuk mencegah manipulasi saldo atau konfirmasi order palsu.
4. **Proteksi Root Domain:** Akses ke root URL (`/`) selalu menghasilkan HTTP 404 Not Found untuk menyamarkan keberadaan panel admin.

---

## 📄 Lisensi

Didistribusikan di bawah lisensi **MIT**. Siapapun bebas menggunakan, memodifikasi, dan mendeploy bot ini untuk keperluan komersial maupun pribadi.
