"""SQLite database layer dengan dukungan Kode Unik & Mutasi DANA."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

_conn: sqlite3.Connection | None = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  price INTEGER NOT NULL,
  description TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  username TEXT,
  first_name TEXT,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  total INTEGER NOT NULL,
  status TEXT DEFAULT 'pending',
  qris_ref TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_seen TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stock_subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS mutations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  raw_text TEXT,
  amount INTEGER,
  matched_order_id TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
"""


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def init_db(path: str) -> None:
    global _conn
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(_SCHEMA_SQL)
    try:
        _conn.execute("ALTER TABLE orders ADD COLUMN qris_ref TEXT")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        _conn.execute("ALTER TABLE orders ADD COLUMN message_id INTEGER")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        _conn.execute("ALTER TABLE products ADD COLUMN stock_file TEXT DEFAULT ''")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        _conn.execute("ALTER TABLE products ADD COLUMN product_type TEXT DEFAULT 'regular'")
        _conn.commit()
    except sqlite3.OperationalError:
        pass
    try:
        _conn.execute("UPDATE products SET product_type = 'regular' WHERE id = 1 AND (product_type IS NULL OR product_type = '')")
        _conn.commit()
    except Exception:
        pass
    _conn.commit()


# Products
def add_product(
    name: str,
    price: int,
    description: str = "",
    product_type: str = "regular",
    stock_file: str = "",
) -> int:
    assert _conn is not None
    cur = _conn.execute(
        "INSERT INTO products (name, price, description, product_type, stock_file) VALUES (?, ?, ?, ?, ?)",
        (name, price, description, product_type, stock_file),
    )
    _conn.commit()
    return cur.lastrowid


def list_products() -> list[dict]:
    assert _conn is not None
    rows = _conn.execute("SELECT * FROM products ORDER BY id ASC").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_product(pid: int) -> dict | None:
    assert _conn is not None
    row = _conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    return _row_to_dict(row)


def delete_product(pid: int) -> bool:
    assert _conn is not None
    cur = _conn.execute("DELETE FROM products WHERE id = ?", (pid,))
    _conn.commit()
    return cur.rowcount > 0


def update_product(
    pid: int,
    name: str | None = None,
    price: int | None = None,
    description: str | None = None,
    product_type: str | None = None,
    stock_file: str | None = None,
) -> bool:
    assert _conn is not None
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if price is not None:
        updates.append("price = ?")
        params.append(price)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if product_type is not None:
        updates.append("product_type = ?")
        params.append(product_type)
    if stock_file is not None:
        updates.append("stock_file = ?")
        params.append(stock_file)

    if not updates:
        return False

    params.append(pid)
    sql = f"UPDATE products SET {', '.join(updates)} WHERE id = ?"
    cur = _conn.execute(sql, tuple(params))
    _conn.commit()
    return cur.rowcount > 0


def get_orders_stats() -> dict:
    assert _conn is not None
    row_pending = _conn.execute("SELECT COUNT(*) as cnt FROM orders WHERE status = 'pending'").fetchone()
    row_paid = _conn.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(total), 0) as total_rev FROM orders WHERE status = 'paid'").fetchone()
    row_total = _conn.execute("SELECT COUNT(*) as cnt FROM orders").fetchone()
    return {
        "pending": row_pending["cnt"] if row_pending else 0,
        "paid": row_paid["cnt"] if row_paid else 0,
        "total": row_total["cnt"] if row_total else 0,
        "revenue": row_paid["total_rev"] if row_paid else 0,
    }



# Orders
def create_order(
    order_id: str,
    user_id: int,
    username: str | None,
    first_name: str | None,
    product_id: int,
    quantity: int,
    total: int,
    qris_ref: str | None = None,
) -> None:
    assert _conn is not None
    _conn.execute(
        """
        INSERT INTO orders
            (id, user_id, username, first_name, product_id, quantity, total, qris_ref)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (order_id, user_id, username, first_name, product_id, quantity, total, qris_ref),
    )
    _conn.commit()


def get_user_orders(user_id: int) -> list[dict]:
    assert _conn is not None
    rows = _conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_orders(limit: int = 50, status: str | None = None) -> list[dict]:
    assert _conn is not None
    if status is None:
        rows = _conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = _conn.execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_order_by_id(order_id: str) -> dict | None:
    assert _conn is not None
    row = _conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return _row_to_dict(row)


def update_order_status(order_id: str, status: str) -> bool:
    assert _conn is not None
    cur = _conn.execute(
        "UPDATE orders SET status = ? WHERE id = ?",
        (status, order_id),
    )
    _conn.commit()
    return cur.rowcount > 0


def set_order_message_id(order_id: str, message_id: int) -> None:
    assert _conn is not None
    _conn.execute(
        "UPDATE orders SET message_id = ? WHERE id = ?",
        (message_id, order_id),
    )
    _conn.commit()


def set_order_qris_ref(order_id: str, qris_ref: str) -> bool:
    assert _conn is not None
    cur = _conn.execute(
        "UPDATE orders SET qris_ref = ? WHERE id = ?",
        (qris_ref, order_id),
    )
    _conn.commit()
    return cur.rowcount > 0


def get_pending_qris_orders() -> list[dict]:
    assert _conn is not None
    rows = _conn.execute(
        "SELECT * FROM orders WHERE status = 'pending' AND qris_ref IS NOT NULL AND qris_ref NOT LIKE 'DANA-%' ORDER BY created_at ASC LIMIT 50"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# DANA Mutation matching helpers
def get_discounted_unique_total(base_amount: int) -> tuple[int, int]:
    """
    Menghasilkan (final_amount, discount) dengan ketentuan:
    - Nominal dikurangi kode unik (minus discount).
    - Prioritaskan angka terbesar / paling dekat dengan harga asli (contoh: 9000 -> 8998, discount 2).
    - Menghasilkan 2 digit akhir antara 50 s.d 99 (discount 2 s.d 50).
    - Jika nominal terbesar sudah dipakai oleh order pending lain, gunakan urutan berikutnya (8997, 8996, dst).
    """
    assert _conn is not None
    pending_rows = _conn.execute(
        "SELECT total FROM orders WHERE status = 'pending'"
    ).fetchall()
    pending_totals = {int(r["total"]) for r in pending_rows}

    # Prioritas angka terbesar (discount terkecil 2 s.d 50)
    for diff in range(2, 51):
        candidate = base_amount - diff
        if candidate > 0 and candidate not in pending_totals:
            return candidate, diff

    # Fallback 51 s.d 99 jika order padat
    for diff in range(51, 100):
        candidate = base_amount - diff
        if candidate > 0 and candidate not in pending_totals:
            return candidate, diff

    return base_amount - 2, 2


def get_unique_code_for_amount(base_amount: int) -> int:
    final_amount, discount = get_discounted_unique_total(base_amount)
    return discount


def find_pending_order_by_amount(amount: int) -> dict | None:
    """Cari order pending yang total tagihannya tepat sesuai nominal mutasi masuk."""
    assert _conn is not None
    row = _conn.execute(
        "SELECT * FROM orders WHERE status = 'pending' AND total = ? ORDER BY created_at DESC LIMIT 1",
        (amount,),
    ).fetchone()
    return _row_to_dict(row)


def record_mutation(title: str, text: str, amount: int | None, matched_order_id: str | None) -> int:
    assert _conn is not None
    cur = _conn.execute(
        "INSERT INTO mutations (title, raw_text, amount, matched_order_id) VALUES (?, ?, ?, ?)",
        (title, text, amount, matched_order_id),
    )
    _conn.commit()
    return cur.lastrowid


def expire_pending_orders(minutes: int = 30) -> list[dict]:
    """Batalkan order yang pending lebih dari X menit agar kode unik bisa dipakai kembali."""
    assert _conn is not None
    query = f"SELECT * FROM orders WHERE status = 'pending' AND created_at < datetime('now', '-{minutes} minutes')"
    rows = _conn.execute(query).fetchall()
    expired = [_row_to_dict(r) for r in rows]
    if expired:
        ids = [r["id"] for r in expired]
        placeholders = ",".join("?" * len(ids))
        _conn.execute(
            f"UPDATE orders SET status = 'cancelled' WHERE id IN ({placeholders})",
            ids,
        )
        _conn.commit()
    return expired


# Users
def upsert_user(user_id: int, username: str | None, first_name: str | None) -> None:
    assert _conn is not None
    _conn.execute(
        """
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_seen)
        VALUES (?, ?, ?, datetime('now'))
        """,
        (user_id, username, first_name),
    )
    _conn.commit()



def get_user(user_id: int) -> dict | None:
    """Ambil data profil pengguna Telegram dari database."""
    assert _conn is not None
    row = _conn.execute(
        "SELECT user_id, username, first_name, last_seen FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "last_seen": row["last_seen"],
    }

def get_all_user_ids() -> list[int]:
    assert _conn is not None
    rows = _conn.execute("SELECT user_id FROM users").fetchall()
    return [int(row["user_id"]) for row in rows]






def add_stock_subscription(user_id: int, product_id: int) -> bool:
    """Tambahkan langganan notifikasi stok untuk user dan produk."""
    assert _conn is not None
    cur = _conn.execute(
        "INSERT OR IGNORE INTO stock_subscriptions (user_id, product_id) VALUES (?, ?)",
        (user_id, product_id),
    )
    _conn.commit()
    return cur.rowcount > 0


def remove_stock_subscription(user_id: int, product_id: int) -> bool:
    """Hapus langganan notifikasi stok untuk user dan produk."""
    assert _conn is not None
    cur = _conn.execute(
        "DELETE FROM stock_subscriptions WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    )
    _conn.commit()
    return cur.rowcount > 0


def is_subscribed_to_stock(user_id: int, product_id: int) -> bool:
    """Cek apakah user sudah berlangganan notifikasi untuk produk ini."""
    assert _conn is not None
    row = _conn.execute(
        "SELECT 1 FROM stock_subscriptions WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    ).fetchone()
    return row is not None


def get_user_stock_subscriptions(user_id: int) -> list[int]:
    """Ambil daftar product_id yang disubscribe oleh user."""
    assert _conn is not None
    rows = _conn.execute(
        "SELECT product_id FROM stock_subscriptions WHERE user_id = ? ORDER BY product_id ASC",
        (user_id,),
    ).fetchall()
    return [int(r["product_id"]) for r in rows]


def get_subscribers_for_product(product_id: int) -> list[int]:
    """Ambil daftar user_id yang berlangganan notifikasi untuk produk ini."""
    assert _conn is not None
    rows = _conn.execute(
        "SELECT user_id FROM stock_subscriptions WHERE product_id = ? ORDER BY id ASC",
        (product_id,),
    ).fetchall()
    return [int(r["user_id"]) for r in rows]


def count_subscribers_for_product(product_id: int) -> int:
    """Hitung jumlah pelanggan yang berlangganan notifikasi produk ini."""
    assert _conn is not None
    row = _conn.execute(
        "SELECT COUNT(*) FROM stock_subscriptions WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    return row[0] if row else 0


def subscribe_all_products(user_id: int) -> int:
    """Berlangganan ke semua produk aktif."""
    assert _conn is not None
    cur = _conn.execute(
        "INSERT OR IGNORE INTO stock_subscriptions (user_id, product_id) SELECT ?, id FROM products",
        (user_id,),
    )
    _conn.commit()
    return cur.rowcount


def unsubscribe_all_products(user_id: int) -> int:
    """Hapus semua langganan produk untuk user ini."""
    assert _conn is not None
    cur = _conn.execute(
        "DELETE FROM stock_subscriptions WHERE user_id = ?",
        (user_id,),
    )
    _conn.commit()
    return cur.rowcount
