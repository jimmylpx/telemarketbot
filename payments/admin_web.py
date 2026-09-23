"""Admin Web Panel untuk Bot Telegram IDLisensi.

Mengelola produk, penambahan stok via textarea (dipisahkan \\n),
edit langsung file stok .txt tiap produk, monitoring pesanan,
dan terintegrasi langsung dengan database SQLite serta bot Telegram.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
from pathlib import Path
from aiohttp import web

import config
import db
from payments.delivery import (
    get_stock_count,
    get_product_stock_path,
    append_stock_lines,
    save_stock_lines,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "idlisensi_admin_session"


def _sign_token(value: str) -> str:
    sig = hmac.new(
        config.ADMIN_SESSION_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{value}.{sig}"


def _verify_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    val, sig = token.rsplit(".", 1)
    expected = hmac.new(
        config.ADMIN_SESSION_SECRET.encode("utf-8"),
        val.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(sig, expected) and val == "admin_authenticated"


def is_authenticated(request: web.Request) -> bool:
    cookie = request.cookies.get(COOKIE_NAME)
    return bool(cookie and _verify_token(cookie))


# ---------------------------------------------------------------------------
# HTML Templates
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login Admin - IDLisensi Bot</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0b0f19;
            color: #f1f5f9;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-card {
            background: #151d30;
            border: 1px solid #22304d;
            border-radius: 16px;
            padding: 36px 30px;
            width: 100%;
            max-width: 400px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
            text-align: center;
        }
        .logo { font-size: 40px; margin-bottom: 12px; }
        h1 { font-size: 22px; font-weight: 700; color: #38bdf8; margin-bottom: 8px; }
        p { color: #94a3b8; font-size: 14px; margin-bottom: 24px; }
        .form-group { text-align: left; margin-bottom: 20px; }
        label { display: block; font-size: 13px; font-weight: 600; color: #cbd5e1; margin-bottom: 8px; }
        input[type="password"] {
            width: 100%;
            padding: 12px 14px;
            background: #0b0f19;
            border: 1px solid #334155;
            border-radius: 8px;
            color: #fff;
            font-size: 15px;
            outline: none;
            transition: border-color 0.2s;
        }
        input[type="password"]:focus { border-color: #38bdf8; }
        button {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #0284c7, #2563eb);
            color: #fff;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: opacity 0.2s;
        }
        button:hover { opacity: 0.9; }
        .alert {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid #ef4444;
            color: #fca5a5;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 18px;
            text-align: left;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">🔐</div>
        <h1>Panel Admin IDLisensi</h1>
        <p>Silakan masukkan password admin untuk mengakses pengelolaan bot.</p>
        __ALERT__
        <form method="POST" action="/admin/login">
            <div class="form-group">
                <label>Password Admin</label>
                <input type="password" name="password" placeholder="Masukkan password..." required autofocus />
            </div>
            <button type="submit">Masuk ke Dashboard</button>
        </form>
    </div>
</body>
</html>
"""


def render_dashboard(products: list[dict], stats: dict, orders: list[dict], alert_msg: str = "", alert_err: str = "") -> str:
    total_stock_count = 0
    prod_rows = []
    prod_options = []

    for p in products:
        s_path = get_product_stock_path(p)
        stk = get_stock_count(s_path)
        total_stock_count += stk
        p_type = p.get("product_type") or ("office_cid" if "office" in p["name"].lower() else "regular")
        type_badge = (
            '<span class="badge badge-office">Office (Auto CID)</span>'
            if p_type == "office_cid"
            else '<span class="badge badge-reg">Regular Link/Key</span>'
        )
        stk_badge = (
            f'<span class="badge badge-stock-ok">{stk} unit</span>'
            if stk > 0
            else '<span class="badge badge-stock-zero">Habis (0)</span>'
        )
        
        prod_rows.append(f"""
        <tr>
            <td><strong>#{p['id']}</strong></td>
            <td>
                <div class="p-name">{html.escape(p['name'])}</div>
                <small class="text-muted">{html.escape(p.get('description') or '-')}</small>
            </td>
            <td>{type_badge}</td>
            <td>Rp {p['price']:,}</td>
            <td>{stk_badge}</td>
            <td><code class="file-path">{html.escape(s_path)}</code></td>
            <td class="action-btns">
                <button class="btn btn-sm btn-info" onclick="openEditStock({p['id']}, '{html.escape(p['name'])}')">📝 Edit Stok TXT</button>
                <button class="btn btn-sm btn-success" onclick="openRestock({p['id']}, '{html.escape(p['name'])}', {stk})">➕ Tambah</button>
                <button class="btn btn-sm btn-secondary" onclick="openEdit({p['id']}, '{html.escape(p['name'])}', {p['price']}, '{html.escape(p.get('description') or '')}', '{p_type}')">✏️ Edit</button>
                <button class="btn btn-sm btn-danger" onclick="confirmDelete({p['id']}, '{html.escape(p['name'])}')">🗑️</button>
            </td>
        </tr>
        """)

        prod_options.append(f'<option value="{p["id"]}">#{p["id"]} - {html.escape(p["name"])} (Stok saat ini: {stk})</option>')

    prod_rows_html = "".join(prod_rows) if prod_rows else '<tr><td colspan="7" style="text-align:center; padding:30px;">Belum ada produk.</td></tr>'
    prod_options_html = "".join(prod_options)

    order_rows = []
    prod_map = {p['id']: p['name'] for p in products}
    for o in orders:
        st = o.get("status", "").lower()
        if st == "paid":
            s_badge = '<span class="badge badge-stock-ok">PAID (Lunas)</span>'
        elif st == "pending":
            s_badge = '<span class="badge badge-pending">PENDING</span>'
        else:
            s_badge = f'<span class="badge badge-cancelled">{html.escape(st.upper())}</span>'

        u_name = f"@{o['username']}" if o.get("username") else f"ID: {o['user_id']}"
        p_name = prod_map.get(o['product_id'], f"Produk #{o['product_id']}")
        order_rows.append(f"""
        <tr>
            <td><code>#{o['id']}</code></td>
            <td>{html.escape(str(o.get('created_at', '')))}</td>
            <td>{html.escape(u_name)}</td>
            <td><strong>{html.escape(p_name)}</strong> <small class="text-muted">(x{o['quantity']})</small></td>
            <td><strong>Rp {o['total']:,}</strong></td>
            <td>{s_badge}</td>
        </tr>
        """)
    order_rows_html = "".join(order_rows) if order_rows else '<tr><td colspan="6" style="text-align:center; padding:20px;">Belum ada riwayat order.</td></tr>'

    alert_banner = ""
    if alert_msg:
        alert_banner = f'<div class="alert alert-success">✅ {html.escape(alert_msg)}</div>'
    elif alert_err:
        alert_banner = f'<div class="alert alert-danger">⚠️ {html.escape(alert_err)}</div>'

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Panel Admin IDLisensi Bot</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0b0f19;
            color: #f1f5f9;
            padding: 24px;
        }}
        .container {{ max-width: 1250px; margin: 0 auto; }}
        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid #1e293b;
        }}
        .brand {{ display: flex; align-items: center; gap: 12px; }}
        .brand h1 {{ font-size: 22px; color: #38bdf8; }}
        .brand span {{ background: #0284c7; font-size: 11px; padding: 3px 8px; border-radius: 9999px; font-weight: 700; }}
        .logout-btn {{
            color: #94a3b8;
            text-decoration: none;
            font-size: 14px;
            padding: 6px 14px;
            border-radius: 6px;
            border: 1px solid #334155;
            transition: background 0.2s;
        }}
        .logout-btn:hover {{ background: #ef4444; color: #fff; border-color: #ef4444; }}
        
        .alert {{
            padding: 12px 18px;
            border-radius: 8px;
            font-size: 14px;
            margin-bottom: 20px;
        }}
        .alert-success {{ background: rgba(16, 185, 129, 0.15); border: 1px solid #10b981; color: #6ee7b7; }}
        .alert-danger {{ background: rgba(239, 68, 68, 0.15); border: 1px solid #ef4444; color: #fca5a5; }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .stat-card {{
            background: #151d30;
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 18px;
        }}
        .stat-title {{ font-size: 13px; color: #94a3b8; margin-bottom: 6px; }}
        .stat-val {{ font-size: 26px; font-weight: 700; color: #f8fafc; }}
        .stat-val.cyan {{ color: #38bdf8; }}
        .stat-val.green {{ color: #10b981; }}
        .stat-val.amber {{ color: #f59e0b; }}

        .tabs {{
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 1px solid #1e293b;
            padding-bottom: 10px;
            overflow-x: auto;
        }}
        .tab-btn {{
            background: #151d30;
            border: 1px solid #334155;
            color: #cbd5e1;
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            cursor: pointer;
            white-space: nowrap;
        }}
        .tab-btn.active {{
            background: #0284c7;
            border-color: #38bdf8;
            color: #fff;
        }}
        .tab-pane {{ display: none; }}
        .tab-pane.active {{ display: block; }}

        .card {{
            background: #151d30;
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
        }}
        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
        }}
        .card-title {{ font-size: 18px; font-weight: 700; color: #f8fafc; }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th, td {{
            padding: 12px 14px;
            text-align: left;
            border-bottom: 1px solid #1e293b;
        }}
        th {{ background: #0f172a; color: #94a3b8; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
        tr:hover {{ background: #1a233a; }}
        .p-name {{ font-weight: 600; color: #f8fafc; }}
        .text-muted {{ color: #94a3b8; font-size: 12px; }}
        .file-path {{ font-size: 11px; background: #0b0f19; padding: 2px 6px; border-radius: 4px; color: #93c5fd; }}

        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
        }}
        .badge-reg {{ background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid #0284c7; }}
        .badge-office {{ background: rgba(168, 85, 247, 0.2); color: #c084fc; border: 1px solid #9333ea; }}
        .badge-stock-ok {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #059669; }}
        .badge-stock-zero {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #dc2626; }}
        .badge-pending {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #d97706; }}
        .badge-cancelled {{ background: rgba(148, 163, 184, 0.2); color: #94a3b8; border: 1px solid #64748b; }}

        .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 14px; }}
        @media(max-width: 768px) {{
            .form-row {{ grid-template-columns: 1fr; }}
            table {{ display: block; overflow-x: auto; }}
        }}
        .form-group {{ margin-bottom: 16px; }}
        label {{ display: block; font-size: 13px; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; }}
        input, select, textarea {{
            width: 100%;
            padding: 10px 12px;
            background: #0b0f19;
            border: 1px solid #334155;
            border-radius: 8px;
            color: #fff;
            font-size: 14px;
            outline: none;
            font-family: inherit;
        }}
        input:focus, select:focus, textarea:focus {{ border-color: #38bdf8; }}
        textarea {{ font-family: monospace; font-size: 13px; line-height: 1.5; resize: vertical; }}
        .counter-badge {{
            display: inline-block;
            margin-top: 6px;
            font-size: 12px;
            color: #38bdf8;
            font-weight: 600;
        }}

        .btn {{
            padding: 10px 18px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 14px;
            border: none;
            cursor: pointer;
            transition: 0.2s;
        }}
        .btn-primary {{ background: linear-gradient(135deg, #0284c7, #2563eb); color: #fff; }}
        .btn-success {{ background: #059669; color: #fff; }}
        .btn-info {{ background: #0284c7; color: #fff; }}
        .btn-danger {{ background: #dc2626; color: #fff; }}
        .btn-secondary {{ background: #334155; color: #cbd5e1; }}
        .btn-sm {{ padding: 5px 10px; font-size: 12px; border-radius: 6px; }}
        .btn:hover {{ opacity: 0.9; }}
        .action-btns {{ display: flex; gap: 6px; flex-wrap: wrap; }}

        /* Modal */
        .modal {{
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0; top: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7);
            backdrop-filter: blur(4px);
            align-items: center;
            justify-content: center;
            padding: 16px;
        }}
        .modal.active {{ display: flex; }}
        .modal-content {{
            background: #151d30;
            border: 1px solid #22304d;
            border-radius: 14px;
            width: 100%;
            max-width: 580px;
            padding: 24px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }}
        .modal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }}
        .modal-title {{ font-size: 18px; color: #38bdf8; font-weight: 700; }}
        .close-btn {{ background: transparent; border: none; font-size: 20px; color: #94a3b8; cursor: pointer; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <h1>🤖 {config.SHOP_NAME}</h1>
                <span>ADMIN PANEL</span>
            </div>
            <a href="/admin/logout" class="logout-btn">Keluar (Logout)</a>
        </header>

        {alert_banner}

        <!-- Stats Overview -->
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-title">Total Produk Aktif</div>
                <div class="stat-val cyan">{len(products)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Total Stok Tersedia</div>
                <div class="stat-val green">{total_stock_count} unit</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Pesanan Lunas (Paid)</div>
                <div class="stat-val green">{stats.get('paid', 0)}</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">Pesanan Menunggu (Pending)</div>
                <div class="stat-val amber">{stats.get('pending', 0)}</div>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('products')">📦 Daftar Produk & Stok</button>
            <button class="tab-btn" onclick="switchTab('add-product')">➕ Tambah Produk Baru</button>
            <button class="tab-btn" onclick="switchTab('restock')">⚡ Tambah Stok (Restock)</button>
            <button class="tab-btn" onclick="switchTab('orders')">📋 Riwayat Pesanan</button>
        </div>

        <!-- Tab 1: Daftar Produk -->
        <div id="tab-products" class="tab-pane active">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">Daftar Produk di Bot Telegram</div>
                    <button class="btn btn-primary btn-sm" onclick="switchTab('add-product')">+ Tambah Produk Baru</button>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Nama Produk & Deskripsi</th>
                            <th>Tipe Produk</th>
                            <th>Harga</th>
                            <th>Stok</th>
                            <th>File Stok</th>
                            <th>Aksi</th>
                        </tr>
                    </thead>
                    <tbody>
                        {prod_rows_html}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Tab 2: Tambah Produk Baru -->
        <div id="tab-add-product" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">➕ Tambah Produk Baru ke Bot Telegram</div>
                </div>
                <form method="POST" action="/admin/products/add">
                    <div class="form-row">
                        <div class="form-group">
                            <label>Nama Produk</label>
                            <input type="text" name="name" placeholder="Contoh: Lisensi Windows 11 Pro Retail" required />
                        </div>
                        <div class="form-group">
                            <label>Harga (Rupiah)</label>
                            <input type="number" name="price" placeholder="Contoh: 25000" min="1000" required />
                        </div>
                    </div>

                    <div class="form-row">
                        <div class="form-group">
                            <label>Tipe Produk</label>
                            <select name="product_type">
                                <option value="regular">Biasa / Regular (Link Jio, Akun, Lisensi standar)</option>
                                <option value="office_cid">Office (Phone Key + Auto Token CID & Panduan Aktivasi)</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Deskripsi Singkat (Opsional)</label>
                            <input type="text" name="description" placeholder="Keterangan garansi, durasi, dll." />
                        </div>
                    </div>

                    <div class="form-group">
                        <label>Masukkan Stok Awal (1 baris per unit stok, dipisahkan Enter / \n):</label>
                        <textarea name="stock_lines" id="newProdStock" rows="6" placeholder="Paste daftar lisensi / link di sini...&#10;Contoh baris 1&#10;Contoh baris 2" oninput="updateCounter('newProdStock', 'newProdCounter')"></textarea>
                        <div id="newProdCounter" class="counter-badge">📊 Terdeteksi: 0 unit stok</div>
                    </div>

                    <button type="submit" class="btn btn-primary">Simpan Produk & Stok</button>
                </form>
            </div>
        </div>

        <!-- Tab 3: Tambah Stok (Restock) -->
        <div id="tab-restock" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">⚡ Tambah Stok (Restock) Produk</div>
                </div>
                <form method="POST" action="/admin/products/restock">
                    <div class="form-group">
                        <label>Pilih Produk yang Ingin Ditambah Stok:</label>
                        <select name="product_id" id="restockSelect" required>
                            {prod_options_html}
                        </select>
                    </div>

                    <div class="form-group">
                        <label>Paste Stok Tambahan (1 baris per unit stok, dipisahkan \n):</label>
                        <textarea name="stock_lines" id="restockText" rows="7" placeholder="Paste stok baru di sini (link/lisensi/akun)...&#10;Baris 1&#10;Baris 2&#10;Baris 3" required oninput="updateCounter('restockText', 'restockCounter')"></textarea>
                        <div id="restockCounter" class="counter-badge">📊 Terdeteksi: 0 unit stok akan ditambahkan</div>
                    </div>

                    <button type="submit" class="btn btn-success">➕ Tambahkan Stok Sekarang</button>
                </form>
            </div>
        </div>

        <!-- Tab 4: Riwayat Pesanan -->
        <div id="tab-orders" class="tab-pane">
            <div class="card">
                <div class="card-header">
                    <div class="card-title">📋 Riwayat Pesanan Terbaru</div>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Order ID</th>
                            <th>Tanggal & Waktu</th>
                            <th>Customer</th>
                            <th>Item Pesanan</th>
                            <th>Total Tagihan</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {order_rows_html}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Modal Edit Stok Langsung (.txt) -->
    <div id="stockModal" class="modal">
        <div class="modal-content" style="max-width: 650px;">
            <div class="modal-header">
                <div class="modal-title">📝 Edit File Stok Langsung (.txt)</div>
                <button class="close-btn" onclick="closeModal('stockModal')">&times;</button>
            </div>
            <p style="color:#94a3b8; font-size:13px; margin-bottom:12px;">
                Produk: <strong id="stockProdTitle" style="color:#38bdf8;"></strong><br/>
                File path: <code id="stockFilePath" class="file-path"></code>
            </p>
            <form method="POST" action="/admin/products/stock/save">
                <input type="hidden" name="product_id" id="stockPid" />
                <div class="form-group">
                    <label>Isi File Stok (1 baris per unit, dipisahkan Enter / \\n):</label>
                    <textarea name="stock_content" id="stockContentText" rows="12" placeholder="Memuat isi file stok..." oninput="updateCounter('stockContentText', 'stockLiveCounter')"></textarea>
                    <div id="stockLiveCounter" class="counter-badge">📊 Memuat data stok...</div>
                </div>
                <div style="display:flex; justify-content: flex-end; gap:10px; margin-top:16px;">
                    <button type="button" class="btn btn-secondary" onclick="closeModal('stockModal')">Batal</button>
                    <button type="submit" class="btn btn-success">💾 Simpan Perubahan File Stok</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Modal Edit Produk -->
    <div id="editModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <div class="modal-title">✏️ Edit Informasi Produk</div>
                <button class="close-btn" onclick="closeModal('editModal')">&times;</button>
            </div>
            <form method="POST" action="/admin/products/edit">
                <input type="hidden" name="product_id" id="editPid" />
                <div class="form-group">
                    <label>Nama Produk</label>
                    <input type="text" name="name" id="editName" required />
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Harga (Rp)</label>
                        <input type="number" name="price" id="editPrice" min="1000" required />
                    </div>
                    <div class="form-group">
                        <label>Tipe Produk</label>
                        <select name="product_type" id="editType">
                            <option value="regular">Biasa / Regular</option>
                            <option value="office_cid">Office (Auto CID)</option>
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label>Deskripsi</label>
                    <input type="text" name="description" id="editDesc" />
                </div>
                <div style="display:flex; justify-content: flex-end; gap:10px; margin-top:16px;">
                    <button type="button" class="btn btn-secondary" onclick="closeModal('editModal')">Batal</button>
                    <button type="submit" class="btn btn-primary">Simpan Perubahan</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Modal Konfirmasi Hapus -->
    <div id="deleteModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <div class="modal-title">🗑️ Hapus Produk</div>
                <button class="close-btn" onclick="closeModal('deleteModal')">&times;</button>
            </div>
            <p style="margin-bottom: 20px; color:#cbd5e1;">Apakah Anda yakin ingin menghapus produk <strong id="deleteProdName" style="color:#ef4444;"></strong> dari bot Telegram?</p>
            <form method="POST" action="/admin/products/delete">
                <input type="hidden" name="product_id" id="deletePid" />
                <div style="display:flex; justify-content: flex-end; gap:10px;">
                    <button type="button" class="btn btn-secondary" onclick="closeModal('deleteModal')">Batal</button>
                    <button type="submit" class="btn btn-danger">Ya, Hapus Produk</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        function switchTab(tabId) {{
            document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            const targetPane = document.getElementById('tab-' + tabId);
            if (targetPane) targetPane.classList.add('active');
            event.target.classList.add('active');
        }}

        function updateCounter(textareaId, counterId) {{
            const el = document.getElementById(textareaId);
            const cnt = document.getElementById(counterId);
            if (!el || !cnt) return;
            const lines = el.value.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
            cnt.textContent = "📊 Terdeteksi: " + lines.length + " unit stok";
        }}

        async function openEditStock(pid, name) {{
            document.getElementById('stockPid').value = pid;
            document.getElementById('stockProdTitle').textContent = name;
            document.getElementById('stockFilePath').textContent = 'Memuat...';
            document.getElementById('stockContentText').value = 'Mengambil data file stok dari server...';
            document.getElementById('stockLiveCounter').textContent = '⏳ Mengambil data...';
            document.getElementById('stockModal').classList.add('active');

            try {{
                const res = await fetch('/admin/products/stock?id=' + pid);
                const data = await res.json();
                if (data.success) {{
                    document.getElementById('stockFilePath').textContent = data.stock_path;
                    document.getElementById('stockContentText').value = data.content;
                    updateCounter('stockContentText', 'stockLiveCounter');
                }} else {{
                    alert('Gagal memuat stok: ' + (data.error || 'Terjadi kesalahan'));
                }}
            }} catch (err) {{
                alert('Gagal menghubungi server: ' + err);
            }}
        }}

        function openRestock(pid, name, curStock) {{
            switchTab('restock');
            document.querySelectorAll('.tab-btn').forEach(b => {{
                if (b.textContent.includes('Tambah Stok')) b.classList.add('active');
                else b.classList.remove('active');
            }});
            const sel = document.getElementById('restockSelect');
            if (sel) sel.value = pid;
            const txt = document.getElementById('restockText');
            if (txt) {{ txt.value = ''; txt.focus(); }}
            updateCounter('restockText', 'restockCounter');
        }}

        function openEdit(pid, name, price, desc, ptype) {{
            document.getElementById('editPid').value = pid;
            document.getElementById('editName').value = name;
            document.getElementById('editPrice').value = price;
            document.getElementById('editDesc').value = desc;
            document.getElementById('editType').value = ptype;
            document.getElementById('editModal').classList.add('active');
        }}

        function confirmDelete(pid, name) {{
            document.getElementById('deletePid').value = pid;
            document.getElementById('deleteProdName').textContent = name;
            document.getElementById('deleteModal').classList.add('active');
        }}

        function closeModal(id) {{
            document.getElementById(id).classList.remove('active');
        }}
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_not_found(request: web.Request) -> web.Response:
    """Tolak akses root / publik dengan status 404 Not Found (tidak diarahkan ke login)."""
    return web.Response(text="404 Not Found", status=404)


async def handle_login_page(request: web.Request) -> web.Response:
    if is_authenticated(request):
        raise web.HTTPFound("/admin")
    err = request.query.get("err", "")
    alert = f'<div class="alert">{html.escape(err)}</div>' if err else ""
    return web.Response(text=LOGIN_HTML.replace("__ALERT__", alert), content_type="text/html")


async def handle_login_submit(request: web.Request) -> web.Response:
    data = await request.post()
    pw = str(data.get("password", "")).strip()

    if pw == config.ADMIN_WEB_PASSWORD:
        resp = web.HTTPFound("/admin")
        token = _sign_token("admin_authenticated")
        resp.set_cookie(COOKIE_NAME, token, httponly=True, max_age=86400 * 30, path="/")
        return resp

    raise web.HTTPFound("/admin/login?err=Password+salah!+Periksa+kembali+password+admin+Anda.")


async def handle_logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/admin/login")
    resp.del_cookie(COOKIE_NAME, path="/")
    return resp


async def handle_admin_dashboard(request: web.Request) -> web.Response:
    if not is_authenticated(request):
        raise web.HTTPFound("/admin/login")

    products = db.list_products()
    stats = db.get_orders_stats()
    orders = db.get_all_orders(limit=40)
    alert_msg = request.query.get("msg", "")
    alert_err = request.query.get("err", "")

    html_page = render_dashboard(products, stats, orders, alert_msg=alert_msg, alert_err=alert_err)
    return web.Response(text=html_page, content_type="text/html")


async def handle_get_product_stock(request: web.Request) -> web.Response:
    """Mengambil isi file stok (.txt) untuk diedit langsung di modal web."""
    if not is_authenticated(request):
        return web.json_response({"success": False, "error": "Unauthorized"}, status=401)

    try:
        pid = int(request.query.get("id", "0"))
    except ValueError:
        return web.json_response({"success": False, "error": "ID tidak valid"}, status=400)

    product = db.get_product(pid)
    if not product:
        return web.json_response({"success": False, "error": "Produk tidak ditemukan"}, status=404)

    stock_path = get_product_stock_path(product)
    content = ""
    p = Path(stock_path)
    if p.is_file():
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Gagal membaca file stok %s: %s", stock_path, exc)
            content = ""

    return web.json_response({
        "success": True,
        "product_id": pid,
        "name": product["name"],
        "stock_path": stock_path,
        "content": content,
    })


async def handle_save_product_stock(request: web.Request) -> web.Response:
    """Menyimpan seluruh isi file stok (.txt) hasil edit langsung."""
    if not is_authenticated(request):
        raise web.HTTPFound("/admin/login")

    data = await request.post()
    try:
        pid = int(data.get("product_id", "0"))
    except ValueError:
        raise web.HTTPFound("/admin?err=ID+produk+tidak+valid!")

    product = db.get_product(pid)
    if not product:
        raise web.HTTPFound("/admin?err=Produk+tidak+ditemukan!")

    raw_content = str(data.get("stock_content", ""))
    lines = [l.strip() for l in raw_content.splitlines() if l.strip()]
    stock_path = get_product_stock_path(product)
    save_stock_lines(stock_path, lines)
    logger.info("Admin mengedit langsung file stok #%d '%s': total %d unit disimpan", pid, product['name'], len(lines))

    raise web.HTTPFound(f"/admin?msg=File+stok+untuk+{product['name']}+berhasil+disimpan!+(Total+stok:+{len(lines)}+unit)")


async def handle_product_add(request: web.Request) -> web.Response:
    if not is_authenticated(request):
        raise web.HTTPFound("/admin/login")

    data = await request.post()
    name = str(data.get("name", "")).strip()
    price_raw = str(data.get("price", "0")).strip()
    desc = str(data.get("description", "")).strip()
    p_type = str(data.get("product_type", "regular")).strip()
    stock_raw = str(data.get("stock_lines", "")).strip()

    if not name:
        raise web.HTTPFound("/admin?err=Nama+produk+tidak+boleh+kosong!")

    try:
        price = int(price_raw)
        if price <= 0:
            raise ValueError()
    except ValueError:
        raise web.HTTPFound("/admin?err=Harga+produk+harus+berupa+angka+valid!")

    # 1. Tambah record ke DB
    new_pid = db.add_product(name=name, price=price, description=desc, product_type=p_type)

    # 2. Tentukan file stok dedicated
    stocks_dir = Path(config.STOCKS_DIR)
    stocks_dir.mkdir(parents=True, exist_ok=True)
    stock_file_path = str(stocks_dir / f"stock_{new_pid}.txt")

    # 3. Parse dan tulis baris stok
    lines = [l.strip() for l in stock_raw.splitlines() if l.strip()]
    if lines:
        save_stock_lines(stock_file_path, lines)
    else:
        Path(stock_file_path).touch(exist_ok=True)

    # 4. Update path stock_file di database
    db.update_product(new_pid, stock_file=stock_file_path)
    logger.info("Admin menambahkan produk baru: #%d '%s' (Rp %d, %d unit stok)", new_pid, name, price, len(lines))

    raise web.HTTPFound(f"/admin?msg=Produk+%23{new_pid}+'{name}'+berhasil+dibuat+dengan+{len(lines)}+unit+stok!")


async def handle_product_restock(request: web.Request) -> web.Response:
    if not is_authenticated(request):
        raise web.HTTPFound("/admin/login")

    data = await request.post()
    try:
        pid = int(data.get("product_id", "0"))
    except ValueError:
        raise web.HTTPFound("/admin?err=ID+produk+tidak+valid!")

    product = db.get_product(pid)
    if not product:
        raise web.HTTPFound("/admin?err=Produk+tidak+ditemukan!")

    stock_raw = str(data.get("stock_lines", "")).strip()
    lines = [l.strip() for l in stock_raw.splitlines() if l.strip()]
    if not lines:
        raise web.HTTPFound("/admin?err=Tidak+ada+baris+stok+yang+dimasukkan!")

    stock_path = get_product_stock_path(product)
    total_now = append_stock_lines(stock_path, lines)
    logger.info("Admin restock produk #%d '%s': +%d unit (total sekarang: %d)", pid, product['name'], len(lines), total_now)

    raise web.HTTPFound(f"/admin?msg=Berhasil+menambahkan+{len(lines)}+unit+stok+untuk+{product['name']}!+(Total+stok:+{total_now}+unit)")


async def handle_product_edit(request: web.Request) -> web.Response:
    if not is_authenticated(request):
        raise web.HTTPFound("/admin/login")

    data = await request.post()
    try:
        pid = int(data.get("product_id", "0"))
        price = int(data.get("price", "0"))
    except ValueError:
        raise web.HTTPFound("/admin?err=Input+angka+tidak+valid!")

    name = str(data.get("name", "")).strip()
    desc = str(data.get("description", "")).strip()
    p_type = str(data.get("product_type", "regular")).strip()

    if not name:
        raise web.HTTPFound("/admin?err=Nama+produk+tidak+boleh+kosong!")

    db.update_product(pid, name=name, price=price, description=desc, product_type=p_type)
    logger.info("Admin mengedit produk #%d: '%s' (Rp %d)", pid, name, price)

    raise web.HTTPFound(f"/admin?msg=Produk+%23{pid}+berhasil+diperbarui!")


async def handle_product_delete(request: web.Request) -> web.Response:
    if not is_authenticated(request):
        raise web.HTTPFound("/admin/login")

    data = await request.post()
    try:
        pid = int(data.get("product_id", "0"))
    except ValueError:
        raise web.HTTPFound("/admin?err=ID+produk+tidak+valid!")

    product = db.get_product(pid)
    if product:
        db.delete_product(pid)
        logger.info("Admin menghapus produk #%d: '%s'", pid, product['name'])

    raise web.HTTPFound(f"/admin?msg=Produk+%23{pid}+berhasil+dihapus+dari+bot!")
