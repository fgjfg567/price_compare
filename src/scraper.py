"""جلب صفحة نتائج البحث واستخراج (الاسم، السعر، الرابط) منها تلقائياً.

يجرّب 3 طرق ويختار الأفضل:
  1) JSON-LD (بيانات المنتجات المنظّمة)   2) JSON مضمّن في الصفحة (Next.js وغيره)
  3) تحليل HTML: يبحث عن الأسعار ويستنتج بطاقة المنتج حولها.
يحترم robots.txt ويضع فاصلاً زمنياً بين الطلبات لنفس الموقع.
"""
import json
import re
import threading
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


class ScrapeError(Exception):
    pass


# ---------------------------------------------------------------- الجلب
_last, _lock, _robots = {}, threading.Lock(), {}


def _throttle(domain, gap=1.5):
    with _lock:
        now = time.time()
        slot = max(now, _last.get(domain, 0) + gap)
        _last[domain] = slot
    if slot > now:
        time.sleep(slot - now)


def _allowed_by_robots(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=8)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch("*", url)


def _fetch_http(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    if r.status_code in (401, 403, 429, 503):
        raise ScrapeError(f"الموقع رفض الطلب (HTTP {r.status_code}). جرّب وضع «المتصفح» أو قد يكون الموقع يحجب الطلبات الآلية.")
    if r.status_code >= 400:
        raise ScrapeError(f"خطأ من الموقع (HTTP {r.status_code}). تأكد من صحة رابط البحث.")
    r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
    return r.text


def _fetch_browser(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ScrapeError("وضع المتصفح يحتاج تثبيت: pip install playwright  ثم  playwright install chromium")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(user_agent=UA, locale="ar-SA", viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            for _ in range(3):  # تمرير لتحميل المنتجات الكسولة
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(600)
            return page.content()
        finally:
            browser.close()


def fetch_html(url, mode="http"):
    if not _allowed_by_robots(url):
        raise ScrapeError("ملف robots.txt للموقع يمنع الوصول الآلي إلى صفحة البحث، لذلك لن أجلبها.")
    _throttle(urlparse(url).netloc)
    try:
        return _fetch_browser(url) if mode == "browser" else _fetch_http(url)
    except requests.RequestException as e:
        raise ScrapeError(f"تعذّر الاتصال بالموقع: {e.__class__.__name__}")


# ---------------------------------------------------------------- مساعدات
NAME_KEYS = ("name", "title", "productName", "product_name", "nameAr", "name_ar", "itemName",
             "displayName", "product_title")
PRICE_KEYS = ("price", "salePrice", "sale_price", "finalPrice", "final_price", "currentPrice",
              "current_price", "offerPrice", "offer_price", "sellingPrice", "selling_price",
              "specialPrice", "special_price", "priceValue", "price_value", "lowPrice")
URL_KEYS = ("url", "link", "href", "permalink", "productUrl", "product_url", "slug")


def _to_price(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        p = float(v)
    elif isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v.translate(_AR))
        if not s:
            return None
        if "," in s and "." not in s and re.search(r",\d{1,2}$", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            p = float(s)
        except ValueError:
            return None
    elif isinstance(v, dict):
        for k in ("amount", "value", "current", "price", "min"):
            if k in v:
                return _to_price(v[k])
        return None
    else:
        return None
    return p if 0 < p < 100000 else None


def _to_name(v):
    if isinstance(v, dict):
        v = v.get("ar") or v.get("ar_SA") or next((x for x in v.values() if isinstance(x, str)), None)
    if isinstance(v, str):
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) >= 3 and not v.startswith(("http", "/")):
            return v[:160]
    return None


def _item(name, price, url, base):
    return {"name": name, "price": price, "url": urljoin(base, url) if url else ""}


def _dedup(items):
    seen, out = set(), []
    for it in items:
        k = (it["name"], it["price"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


# ---------------------------------------------------------------- الطريقة 1: JSON-LD
def _jsonld(soup, base):
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            t = o.get("@type")
            types = t if isinstance(t, list) else [t]
            if "Product" in types:
                offers = o.get("offers")
                offers = offers if isinstance(offers, list) else [offers] if offers else []
                prices = [_to_price(of.get("price") or of.get("lowPrice")) for of in offers if isinstance(of, dict)]
                prices = [p for p in prices if p]
                name = _to_name(o.get("name"))
                if name and prices:
                    out.append(_item(name, min(prices), o.get("url"), base))
            for v in o.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(s.string or s.get_text() or ""))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------- الطريقة 2: JSON مضمّن
def _embedded_json(soup, base):
    out = []

    def walk(o, depth=0):
        if depth > 25:
            return
        if isinstance(o, dict):
            name = next((n for n in (_to_name(o.get(k)) for k in NAME_KEYS if k in o) if n), None)
            price = next((p for p in (_to_price(o.get(k)) for k in PRICE_KEYS if k in o) if p), None)
            if name and price:
                url = next((o[k] for k in URL_KEYS if isinstance(o.get(k), str)), "")
                out.append(_item(name, price, url, base))
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for x in o[:5000]:
                if isinstance(x, (dict, list)):
                    walk(x, depth + 1)

    for s in soup.find_all("script"):
        if s.get("type") == "application/ld+json":
            continue
        txt = (s.string or s.get_text() or "").strip()
        if len(txt) < 50 or len(txt) > 8_000_000:
            continue
        data = None
        try:
            if "json" in (s.get("type") or "") or s.get("id") == "__NEXT_DATA__":
                data = json.loads(txt)
            else:
                m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
                if m:
                    data = json.loads(m.group(1))
        except Exception:
            data = None
        if data is not None:
            walk(data)
    return out


# ---------------------------------------------------------------- الطريقة 3: تحليل HTML
PRICE_RE = re.compile(
    r"(?:SAR|SR|ر\s?\.?\s?س\.?|ريال)\s*(\d+(?:\s?[.,]\s?\d{1,2})?)"
    r"|(\d+(?:\s?[.,]\s?\d{1,2})?)\s*(?:SAR|SR|ر\s?\.?\s?س\.?|ريال)", re.I)


def _prices_in(text):
    vals = []
    for m in PRICE_RE.finditer(text.translate(_AR)):
        s = re.sub(r"\s", "", m.group(1) or m.group(2)).replace(",", ".")
        try:
            v = float(s)
        except ValueError:
            continue
        if 0 < v < 100000:
            vals.append(v)
    return vals


def _card_name(card):
    for h in card.find_all(["h1", "h2", "h3", "h4", "h5"]):
        t = h.get_text(" ", strip=True)
        if len(t) >= 3 and not PRICE_RE.search(t):
            return t
    for el in card.find_all(attrs={"title": True}):
        t = el["title"].strip()
        if len(t) >= 3 and not PRICE_RE.search(t):
            return t
    for el in card.find_all(class_=re.compile(r"name|title", re.I)):
        t = el.get_text(" ", strip=True)
        if len(t) >= 3 and not PRICE_RE.search(t):
            return t
    img = card.find("img", alt=True)
    if img and len(img["alt"].strip()) >= 3:
        return img["alt"].strip()
    parts = [s.strip() for s in card.stripped_strings
             if len(s.strip()) >= 3 and not PRICE_RE.search(s) and not s.strip().isdigit()]
    return max(parts, key=len) if parts else None


def _html_cards(soup, base):
    out, used = [], set()
    for node in soup.find_all(string=PRICE_RE):
        if node.parent is None or node.parent.name in ("script", "style", "noscript"):
            continue
        cur = node.parent
        for _ in range(8):
            if cur is None or cur.name in ("body", "html"):
                break
            link = cur if cur.name == "a" and cur.get("href") else cur.find("a", href=True)
            if link is not None:
                if len(cur.find_all(string=PRICE_RE)) > 4:   # حاوية قائمة وليست بطاقة
                    break
                name = _card_name(cur)
                if name and id(cur) not in used:
                    used.add(id(cur))
                    prices = _prices_in(cur.get_text(" ", strip=True))
                    if prices:
                        out.append(_item(re.sub(r"\s+", " ", name)[:160], min(prices), link.get("href"), base))
                    break
            cur = cur.parent
    return out


# ---------------------------------------------------------------- الواجهة
def extract_products(html, base_url="", relevant=None):
    """يرجع (قائمة المنتجات، اسم الطريقة). يختار الطريقة ذات أكثر نتائج مطابقة للبحث."""
    soup = BeautifulSoup(html, "html.parser")
    best, best_name, best_score = [], "none", -1
    for name, fn in (("JSON-LD", _jsonld), ("JSON مضمّن", _embedded_json), ("تحليل HTML", _html_cards)):
        try:
            items = _dedup(fn(soup, base_url))
        except Exception:
            items = []
        score = sum(1 for i in items if relevant(i["name"])) if relevant else len(items)
        if items and (score > best_score or (score == best_score == 0 and len(items) > len(best))):
            best, best_name, best_score = items, name, score
    return best, best_name
