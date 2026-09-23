"""Handler perintah /cid untuk Aktivasi Confirmation ID (CID) & OCR via Gemini."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import httpx
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

logger = logging.getLogger(__name__)

# Conversation states
TOKEN, IID, CONFIRM_IID = range(3)


def format_iid(iid: str) -> str:
    """Format IID menjadi kelompok angka agar mudah dibaca."""
    clean = re.sub(r"\D+", "", iid)
    if len(clean) == 54:
        return " ".join([clean[i:i+6] for i in range(0, 54, 6)])
    elif len(clean) == 63:
        return " ".join([clean[i:i+9] for i in range(0, 63, 9)])
    else:
        return " ".join([clean[i:i+6] for i in range(0, len(clean), 6)])


def format_cid_blocks(cid: str) -> str:
    """Format 48 digit CID menjadi 8 blok A-H."""
    clean = re.sub(r"\D+", "", cid)
    if len(clean) != 48:
        return f"`{cid}`"

    labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
    chunks = [clean[i:i+6] for i in range(0, 48, 6)]
    lines = []
    for i in range(0, 8, 2):
        lines.append(f"• *{labels[i]}:* `{chunks[i]}`  |  *{labels[i+1]}:* `{chunks[i+1]}`")
    return "\n".join(lines)


def generate_offline_script(cid: str) -> str:
    """Buat script CMD aktivasi offline otomatis."""
    clean = re.sub(r"\D+", "", cid)
    lines = [
        r'for %a in (4,5,6) do (if exist "%ProgramFiles%\Microsoft Office\Office1%a\ospp.vbs" (cd /d "%ProgramFiles%\Microsoft Office\Office1%a")',
        r'if exist "%ProgramFiles(x86)%\Microsoft Office\Office1%a\ospp.vbs" (cd /d "%ProgramFiles(x86)%\Microsoft Office\Office1%a"))',
        'if not exist ospp.vbs for /f "delims=" %i in (\'dir /s /b "%ProgramFiles%\\Microsoft Office\\root\\Office*\\ospp.vbs" 2^>nul\') do cd /d "%~dp$i"',
        'if not exist ospp.vbs for /f "delims=" %i in (\'dir /s /b "%ProgramFiles(x86)%\\Microsoft Office\\root\\Office*\\ospp.vbs" 2^>nul\') do cd /d "%~dp$i"',
        f'set CID={clean}',
        r'cscript //nologo OSPP.VBS /actcid:%CID%',
        r'cscript.exe OSPP.vbs /act',
    ]
    return "\n".join(lines)


async def check_token_api(token: str) -> dict:
    """Validasi token ke API check_token.php."""
    url = f"{config.CID_BASE_URL}/check_token.php"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(url, params={"token": token.strip()})
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception as json_err:
                    logger.error("check_token_api JSON decode error: %s | text: %r", json_err, resp.text)
                    return {"success": False, "error": "Respon tidak valid dari server token."}
            return {"success": False, "error": f"Server status {resp.status_code}"}
    except Exception as exc:
        logger.error("Error check_token_api: %s", exc)
        return {"success": False, "error": "Gagal terhubung ke server verifikasi token."}


async def extract_iid_gemini(image_bytes: bytes, mime_type: str = "image/jpeg") -> str | None:
    """Ekstrak Installation ID dari gambar menggunakan Gemini API (gemini-3.5-flash-lite)."""
    model = config.GEMINI_OCR_MODEL or "gemini-3.5-flash-lite"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={config.GEMINI_API_KEY}"
    prompt = (
        "Gambar ini berisi layar aktivasi Microsoft Office. Ambil HANYA Installation ID yang tampil pada Step 2 aktivasi "
        "(deretan angka panjang, biasanya 9 kelompok masing-masing 6 digit yang dipisahkan spasi). "
        "Keluarkan hanya digit-digitnya saja, tanpa spasi, tanpa label, tanpa teks lain apa pun."
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode("utf-8")}}
            ]
        }],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024}
    }
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                clean = re.sub(r"\D+", "", text)
                if len(clean) >= 9:
                    logger.info("OCR sukses menggunakan model %s: %s (panjang: %d)", model, clean, len(clean))
                    return clean
                else:
                    logger.warning("OCR hasil kurang dari 9 digit (%d): %r", len(clean), text)
            else:
                logger.error("OCR API error %d: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.exception("Error extract_iid_gemini: %s", exc)
    return None


async def request_get_cid(iid: str, token: str) -> dict:
    """Kirim request ke get_cid.php."""
    url = f"{config.CID_BASE_URL}/get_cid.php"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    params = {"iid": iid, "token": token}
    try:
        async with httpx.AsyncClient(timeout=40.0, headers=headers) as client:
            resp = await client.get(url, params=params)
            try:
                return resp.json()
            except Exception as json_err:
                logger.error("request_get_cid JSON decode error: %s | text: %r", json_err, resp.text)
                return {"error": "Respon tidak valid dari server CID."}
    except Exception as exc:
        logger.error("Error request_get_cid: %s", exc)
        return {"error": "Gagal menghubungi server GET CID."}


async def poll_check_cid(iid: str, max_retries: int = 24, delay: int = 5) -> str | None:
    """Polling ke check_cid.php sampai CID terbit atau timeout (default ~2 menit)."""
    url = f"{config.CID_BASE_URL}/check_cid.php"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    params = {"iid": iid}
    for _ in range(max_retries):
        await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success") and data.get("cid"):
                        return str(data["cid"]).strip()
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def cmd_cid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: pengguna mengetik /cid."""
    message = update.effective_message
    if message is None:
        return ConversationHandler.END

    context.user_data.pop("cid_token", None)
    context.user_data.pop("cid_iid", None)

    await message.reply_text(
        "🔑 *Aktivasi Confirmation ID (CID)*\n\n"
        "Silakan masukkan *Token CID* Anda (disertakan saat pembelian link/lisensi).\n\n"
        "Ketik /cancel untuk membatalkan.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return TOKEN


async def handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menerima dan memverifikasi token."""
    message = update.effective_message
    if message is None or not message.text:
        return TOKEN

    token = message.text.strip()
    if token.startswith("/"):
        return TOKEN

    status_msg = await message.reply_text("🔄 Memverifikasi token Anda...")

    res = await check_token_api(token)
    if not res.get("success"):
        err = res.get("error", "Token tidak valid.")
        await status_msg.edit_text(
            f"❌ *Verifikasi Gagal:*\n{err}\n\n"
            "Silakan ketik ulang token yang benar, atau ketik /cancel untuk membatalkan.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return TOKEN

    quota = res.get("value", 1)
    context.user_data["cid_token"] = token
    context.user_data["cid_quota"] = quota

    await status_msg.edit_text(
        f"✅ *Token Terverifikasi!* (Sisa kuota: *{quota}x*)\n\n"
        "Silakan kirimkan *Installation ID (IID)* Anda:\n"
        "• 📸 *Kirim Foto:* Screenshot layar Step 2 aktivasi Office/Windows\n"
        "• ✍️ *Atau Ketik:* Deretan angka IID langsung di chat ini\n\n"
        "Ketik /cancel untuk batal.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return IID


async def handle_iid_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menerima IID via input teks."""
    message = update.effective_message
    if message is None or not message.text:
        return IID

    raw_text = message.text.strip()
    if raw_text.startswith("/"):
        return IID

    clean_digits = re.sub(r"\D+", "", raw_text)
    if len(clean_digits) < 9:
        await message.reply_text(
            "⚠️ *Format IID Tidak Valid!*\n\n"
            "Installation ID harus berupa deretan angka panjang (biasanya 54 atau 63 digit).\n"
            "Silakan periksa kembali atau kirimkan foto layar Step 2 aktivasi.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return IID

    context.user_data["cid_iid"] = clean_digits
    formatted = format_iid(clean_digits)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Dapatkan CID", callback_data="cid_action:confirm")],
        [
            InlineKeyboardButton("✏️ Ketik Ulang", callback_data="cid_action:retry"),
            InlineKeyboardButton("❌ Batal", callback_data="cid_action:cancel"),
        ]
    ])

    await message.reply_text(
        "📋 *Konfirmasi Installation ID (IID):*\n\n"
        f"`{formatted}`\n\n"
        f"Total: *{len(clean_digits)} digit*\n\n"
        "Pastikan angka di atas sudah sesuai, lalu tekan tombol di bawah:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )
    return CONFIRM_IID


async def handle_iid_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Menerima foto dan melakukan OCR via Gemini API."""
    message = update.effective_message
    if message is None:
        return IID

    status_msg = await message.reply_text("🔍 *Membaca Installation ID dari foto via AI OCR...*\nMohon tunggu sebentar.", parse_mode=ParseMode.MARKDOWN)

    # Ambil file foto ukuran terbesar
    photo_obj = None
    mime_type = "image/jpeg"
    if message.photo:
        photo_obj = message.photo[-1]
        mime_type = "image/jpeg"
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        photo_obj = message.document
        mime_type = message.document.mime_type

    if not photo_obj:
        await status_msg.edit_text("⚠️ Format gambar tidak didukung. Silakan kirim file foto langsung.")
        return IID

    try:
        tg_file = await context.bot.get_file(photo_obj.file_id)
        image_bytes = await tg_file.download_as_bytearray()
        
        extracted_digits = await extract_iid_gemini(bytes(image_bytes), mime_type=mime_type)
        if not extracted_digits:
            await status_msg.edit_text(
                "⚠️ *Gagal Membaca Installation ID!*\n\n"
                "AI tidak dapat mendeteksi deretan angka IID pada foto. Pastikan:\n"
                "1. Foto terlihat terang & tidak buram.\n"
                "2. Bagian *Step 2* aktivasi (kelompok angka) terlihat jelas.\n\n"
                "Silakan kirim foto ulang atau ketik deretan angka IID secara manual.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return IID

        context.user_data["cid_iid"] = extracted_digits
        formatted = format_iid(extracted_digits)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Dapatkan CID", callback_data="cid_action:confirm")],
            [
                InlineKeyboardButton("✏️ Kirim / Ketik Ulang", callback_data="cid_action:retry"),
                InlineKeyboardButton("❌ Batal", callback_data="cid_action:cancel"),
            ]
        ])

        await status_msg.edit_text(
            "🔍 *Hasil AI OCR (Installation ID):*\n\n"
            f"`{formatted}`\n\n"
            f"Total: *{len(extracted_digits)} digit*\n\n"
            "Periksa kembali apakah angka di atas sudah sesuai:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        return CONFIRM_IID

    except Exception as exc:
        logger.exception("Error proses foto OCR: %s", exc)
        await status_msg.edit_text("⚠️ Terjadi kesalahan saat membaca gambar. Silakan coba lagi atau ketik IID manual.")
        return IID


async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler tombol konfirmasi Dapatkan CID / Retry / Cancel."""
    query = update.callback_query
    if query is None:
        return ConversationHandler.END

    await query.answer()
    action = query.data.split(":")[1] if ":" in query.data else ""

    if action == "cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ Proses aktivasi CID dibatalkan.")
        return ConversationHandler.END

    if action == "retry":
        context.user_data.pop("cid_iid", None)
        await query.edit_message_text(
            "Silakan kirimkan foto ulang atau ketik angka Installation ID (IID) Anda:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return IID

    # action == "confirm"
    token = context.user_data.get("cid_token")
    iid = context.user_data.get("cid_iid")

    if not token or not iid:
        context.user_data.clear()
        await query.edit_message_text("Sesi telah berakhir. Silakan ulangi dengan /cid.")
        return ConversationHandler.END

    await query.edit_message_text(
        "⏳ *Menghubungi Server Aktivasi...*\n"
        "Mohon tunggu sebentar selagi sistem memproses IID Anda.",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Panggil API get_cid.php
    api_res = await request_get_cid(iid, token)
    logger.info("get_cid response for %s: %s", iid, api_res)

    if api_res.get("error"):
        await query.edit_message_text(
            f"❌ *Gagal Mendapatkan CID:*\n{api_res.get('error')}",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data.clear()
        return ConversationHandler.END

    response_msg = api_res.get("response_message") or ""
    cid_direct = api_res.get("cid")

    # Kasus 1: Error dari Microsoft (Dead Key, Fake Key, Key Blocked, dll)
    dead_messages = ["Dead Key.", "Key Blocked.", "Fake Key.", "Need to call MS Support.", "Wrong IID."]
    if any(m.lower() in response_msg.lower() for m in dead_messages):
        await query.edit_message_text(
            f"❌ *Aktivasi Gagal dari Server Microsoft:*\n\n"
            f"Pesan: *{response_msg}*\n\n"
            "⚠️ Kunci lisensi Anda bermasalah / telah diblokir oleh Microsoft. Kuota token Anda tidak berkurang.",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data.clear()
        return ConversationHandler.END

    # Kasus 2: CID langsung keluar dari get_cid.php
    cid_result = None
    if cid_direct and len(re.sub(r"\D+", "", str(cid_direct))) == 48:
        cid_result = str(cid_direct).strip()
    elif len(re.sub(r"\D+", "", response_msg)) == 48:
        cid_result = re.sub(r"\D+", "", response_msg)

    # Kasus 3: Antrean telepon ("We are calling to get CID for you...") -> Polling check_cid.php
    if not cid_result:
        await query.edit_message_text(
            "📞 *Menghubungi Telepon Aktivasi Microsoft...*\n\n"
            "Permintaan sedang diproses dalam antrean server.\n"
            "⏳ *Estimasi waktu:* 30 - 90 detik. Mohon jangan tinggalkan chat.",
            parse_mode=ParseMode.MARKDOWN,
        )

        cid_polled = await poll_check_cid(iid, max_retries=24, delay=5)
        if cid_polled and len(re.sub(r"\D+", "", cid_polled)) == 48:
            cid_result = cid_polled

    # Tampilkan Hasil jika CID berhasil didapatkan
    if cid_result:
        clean_cid = re.sub(r"\D+", "", cid_result)
        cid_blocks = format_cid_blocks(clean_cid)
        cmd_script = generate_offline_script(clean_cid)

        res_text = (
            "🎉 *CONFIRMATION ID (CID) BERHASIL DIDAPATKAN!*\n\n"
            f"🔑 *Blok CID (Step 3 Aktivasi):*\n"
            f"{cid_blocks}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 *Deretan 48 Digit Penuh (Salin Cepat):*\n"
            f"`{clean_cid}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💻 *Script Aktivasi CMD Offline (Jalankan as Admin):*\n"
            f"```cmd\n{cmd_script}\n```\n\n"
            "Terima kasih telah menggunakan layanan aktivasi IDLisensi!"
        )
        await query.edit_message_text(res_text, parse_mode=ParseMode.MARKDOWN)
        context.user_data.clear()
        return ConversationHandler.END

    # Jika polling timeout
    await query.edit_message_text(
        "⏳ *Proses Masih Berlangsung:*\n\n"
        "Server Microsoft sedang dalam antrean padat. CID Anda sedang diproses di latar belakang.\n"
        f"Silakan periksa kembali beberapa saat lagi dengan mengetik /cid atau cek riwayat di https://cid.idlisensi.com/",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_cid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Membatalkan sesi /cid."""
    context.user_data.clear()
    message = update.effective_message
    if message:
        await message.reply_text("❌ Sesi /cid telah dibatalkan.")
    return ConversationHandler.END


def register(app: Application) -> None:
    """Daftarkan ConversationHandler untuk perintah /cid."""
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("cid", cmd_cid)],
        states={
            TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_token),
            ],
            IID: [
                MessageHandler(filters.PHOTO | (filters.Document.ALL & filters.Document.MimeType("image/*")), handle_iid_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_iid_text),
            ],
            CONFIRM_IID: [
                CallbackQueryHandler(handle_confirm_callback, pattern=r"^cid_action:(confirm|retry|cancel)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_cid)],
        allow_reentry=True,
    )
    app.add_handler(conv_handler)
