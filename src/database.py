"""طبقة قاعدة البيانات (SQLite): تخزين المنتجات، الأسعار، وسجل التغيّر."""
import os
import re
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher

from units import unit_price

DATA_DIR = os.getenv("FLET_APP_STORAGE_DATA", ".")   # مجلد التخزين الدائم على أندرويد
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "prices_v3.db")
STOPWORDS = {"من", "شركه", "ماركه", "نوع", "في", "عن", "او", "علامه", "تجاريه", "منتج"}


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def normalize(text: str) -> str:
    """توحيد النص العربي: إزالة التشكيل، توحيد الهمزات والألف والياء والتاء المربوطة، وحذف تكرار الحروف."""
    t = (text or "").strip().lower()
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)
    t = re.sub("[أإآٱ]", "ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = re.sub(r"([\u0621-\u064A])\1+", r"\1", t)
    return re.sub(r"\s+", " ", t)


def term_matches(term: str, words: list) -> bool:
    for w in words:
        if term in w:
            return True
        if term.isdigit() or len(term) < 3:
            continue
        if SequenceMatcher(None, term, w).ratio() >= 0.75:
            return True
        if len(term) >= 4 and SequenceMatcher(None, term, w[:len(term)]).ratio() >= 0.85:
            return True
    return False


def fuzzy_match(query: str, text_norm: str) -> bool:
    terms = normalize(query).split()
    keep = [t for t in terms if t not in STOPWORDS] or terms   # «زيت من شركة نور» -> زيت + نور
    words = text_norm.split()
    return bool(keep) and all(term_matches(t, words) for t in keep)


def init_db(store_names):
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS stores(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id),
            name TEXT NOT NULL, unit TEXT DEFAULT '', name_norm TEXT NOT NULL,
            price REAL NOT NULL, unit_price REAL, unit_label TEXT DEFAULT '',
            url TEXT DEFAULT '', updated_at TEXT NOT NULL,
            UNIQUE(store_id, name_norm));
        CREATE TABLE IF NOT EXISTS price_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            price REAL NOT NULL, recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fetch_log(
            store_id INTEGER NOT NULL, query_norm TEXT NOT NULL, fetched_at TEXT NOT NULL,
            PRIMARY KEY(store_id, query_norm));
        """)
        for n in store_names:
            c.execute("INSERT OR IGNORE INTO stores(name) VALUES(?)", (n,))


def get_stores(names=None) -> dict:
    with conn() as c:
        rows = c.execute("SELECT id, name FROM stores ORDER BY id").fetchall()
    return {r["name"]: r["id"] for r in rows if names is None or r["name"] in names}


def last_fetch(store_id, query_norm):
    with conn() as c:
        r = c.execute("SELECT fetched_at FROM fetch_log WHERE store_id=? AND query_norm=?",
                      (store_id, query_norm)).fetchone()
    return datetime.fromisoformat(r["fetched_at"]) if r else None


def mark_fetched(store_id, query_norm):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO fetch_log VALUES(?,?,?)",
                  (store_id, query_norm, datetime.now().isoformat(timespec="seconds")))


def clear_store(store_id):
    """حذف بيانات متجر (عند تغيير إعداده، مثلاً من تجريبي إلى مباشر)."""
    with conn() as c:
        c.execute("DELETE FROM price_history WHERE product_id IN (SELECT id FROM products WHERE store_id=?)", (store_id,))
        c.execute("DELETE FROM products WHERE store_id=?", (store_id,))
        c.execute("DELETE FROM fetch_log WHERE store_id=?", (store_id,))


def upsert_products(store_id, items):
    """إضافة الجديد وتحديث السعر فقط إذا تغيّر. يرجع (جديد، تغيّر سعره)."""
    now = datetime.now().isoformat(timespec="seconds")
    new = changed = 0
    with conn() as c:
        for it in items:
            full = f"{it['name']} {it.get('unit', '')}".strip()
            norm = normalize(full)
            price = float(it["price"])
            up, ul = unit_price(price, full)
            row = c.execute("SELECT id, price FROM products WHERE store_id=? AND name_norm=?",
                            (store_id, norm)).fetchone()
            if row is None:
                cur = c.execute(
                    "INSERT INTO products(store_id,name,unit,name_norm,price,unit_price,unit_label,url,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (store_id, it["name"], it.get("unit", ""), norm, price, up, ul, it.get("url", ""), now))
                c.execute("INSERT INTO price_history(product_id,price,recorded_at) VALUES(?,?,?)",
                          (cur.lastrowid, price, now))
                new += 1
            elif abs(row["price"] - price) > 1e-9:
                c.execute("UPDATE products SET price=?, unit_price=?, unit_label=?, url=?, updated_at=? WHERE id=?",
                          (price, up, ul, it.get("url", ""), now, row["id"]))
                c.execute("INSERT INTO price_history(product_id,price,recorded_at) VALUES(?,?,?)",
                          (row["id"], price, now))
                changed += 1
            else:
                c.execute("UPDATE products SET updated_at=? WHERE id=?", (now, row["id"]))
    return new, changed


def search_products(query, store_ids, by="price"):
    """بحث متسامح مع الأخطاء الإملائية، مرتّب من الأرخص إلى الأغلى (بالسعر أو بسعر الوحدة)."""
    if not normalize(query) or not store_ids:
        return []
    marks = ",".join("?" * len(store_ids))
    with conn() as c:
        rows = c.execute(
            f"SELECT s.name AS store, p.name, p.unit, p.price, p.unit_price, p.unit_label, "
            f"p.url, p.updated_at, p.name_norm FROM products p JOIN stores s ON s.id=p.store_id "
            f"WHERE p.store_id IN ({marks})", list(store_ids)).fetchall()
    out = [dict(r) for r in rows if fuzzy_match(query, r["name_norm"])]
    for r in out:
        r.pop("name_norm")
    if by == "unit":
        return sorted(out, key=lambda r: (r["unit_price"] is None, r["unit_price"] or 0, r["price"]))
    return sorted(out, key=lambda r: r["price"])


def stats():
    with conn() as c:
        p = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
        h = c.execute("SELECT COUNT(*) n FROM price_history").fetchone()["n"]
    return p, h
