"""Generator QRIS Dinamis dari QRIS Statis Merchant (EMVCo Standard).

Mengonversi payload QRIS Statis menjadi QRIS Dinamis dengan menyisipkan
tag 54 (Transaction Amount), mengubah tag 01 (Point of Initiation) menjadi 12,
dan menghitung ulang checksum CRC-16 (CCITT-FALSE).
"""

from __future__ import annotations

import io


def calculate_crc16(text: str) -> str:
    """Hitung checksum CRC-16 CCITT-FALSE (polinom 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for char in text:
        crc ^= ord(char) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def generate_dynamic_qris(base_qris: str, amount: int | float) -> str:
    """Ubah QRIS statis menjadi QRIS dinamis dengan nominal spesifik."""
    qris = base_qris.strip()
    if not qris:
        return ""

    # 1. Hapus CRC lama (Tag 63) di akhir string jika ada (selalu 8 karakter: 6304XXXX)
    if len(qris) >= 8 and qris[-8:-4] == "6304":
        qris = qris[:-8]

    # 2. Ubah Point of Initiation Method dari Statis (010211) ke Dinamis (010212)
    qris = qris.replace("010211", "010212", 1)

    # 3. Format nominal angka (Tag 54)
    if isinstance(amount, float) and not amount.is_integer():
        amount_str = f"{amount:.2f}".rstrip("0").rstrip(".")
    else:
        amount_str = str(int(amount))

    tag_54 = f"54{len(amount_str):02d}{amount_str}"

    # 4. Sisipkan atau ganti Tag 54 setelah Tag 53 (5303360 untuk mata uang IDR)
    pos = qris.find("5303360")
    if pos != -1:
        after_53 = qris[pos + 7:]
        if after_53.startswith("54"):
            old_len = int(after_53[2:4])
            after_54 = after_53[4 + old_len:]
            qris = qris[:pos + 7] + tag_54 + after_54
        else:
            qris = qris[:pos + 7] + tag_54 + after_53
    else:
        qris += tag_54

    # 5. Tambahkan header Tag 63 dan hitung CRC16 baru
    qris += "6304"
    crc = calculate_crc16(qris)
    return qris + crc


def generate_qris_image_bytes(qris_payload: str) -> io.BytesIO:
    """Hasilkan gambar QR Code dalam bentuk BytesIO PNG siap kirim ke Telegram."""
    import qrcode
    from PIL import Image

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(qris_payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def extract_merchant_name(payload: str) -> str:
    """Ekstrak nama merchant dari EMVCo Tag 59."""
    if not payload:
        return ""
    p = payload.strip()
    i = 0
    while i < len(p):
        tag = p[i:i+2]
        if len(tag) < 2:
            break
        try:
            length = int(p[i+2:i+4])
        except ValueError:
            break
        val = p[i+4:i+4+length]
        if tag == "59":
            return val.strip()
        i += 4 + length
    return ""


