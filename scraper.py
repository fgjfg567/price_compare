"""جلب صفحة نتائج البحث واستخراج (الاسم، السعر الحالي، رابط المنتج) كما تظهر للمستخدم.

الأولوية لما يظهر في الصفحة نفسها (DOM) لأنه نفس السعر الذي يراه المستخدم:
  1) أمازون: قارئ مخصص (السعر الحالي + رابط /dp/ASIN)   2) بطاقات المنتجات عبر روابط /p/
  3) تحليل HTML عام   4) JSON-LD   5) JSON مضمّن في الصفحة.
يحترم robots.txt ويضع فاصلاً زمنياً بين الطلبات لنفس الموقع.
"""
import json
import re
import threading
import time
from urllib import robotparser
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Mobile Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_MARKS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


class ScrapeError(Exception):
    def __init__(self, msg, code="error"):
        super().__init__(msg)
        self.code = code   # robots | blocked | http | network | browser | noitems | error


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
        raise ScrapeError(f"الموقع رفض الطلب (HTTP {r.status_code}) — يحجب الطلبات الآلية.", "blocked")
    if r.status_code >= 400:
        raise ScrapeError(f"خطأ من الموقع (HTTP {r.status_code}).", "http")
    r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
    return r.text


def fetch_html(url, mode="http"):
    if not _allowed_by_robots(url):
        raise ScrapeError("الموقع يمنع الوصول الآلي لصفحة البحث (robots.txt).", "robots")
    _throttle(urlparse(url).netloc)
    try:
        return _fetch_http(url)
    except requests.RequestException:
        raise ScrapeError("تعذّر الاتصال بالموقع (تحقق من الإنترنت).", "network")


def check_url(url, timeout=8):
    """True = الرابط يعمل، False = معطّل (404 أو تحويل للرئيسية)، None = غير معروف (الموقع يحجب الفحص)."""
    try:
        if not _allowed_by_robots(url):
            return None
        _throttle(urlparse(url).netloc, 0.3)
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True, allow_redirects=True)
        code, final = r.status_code, urlparse(r.url).path.strip("/")
        r.close()
        if code in (404, 410):
            return False
        if code >= 400:
            return None
        if not final and urlparse(url).path.strip("/"):
            return False
        return True
    except requests.RequestException:
        return None


# ---------------------------------------------------------------- الروابط
_TRACK = re.compile(r"^(utm_|ref|pd_rd|pf_rd|qid|sr$|keywords|crid|sprefix|content-id|dib|psc|th$|spLa|sp_csd|fbclid|gclid|___)", re.I)
_BAD_LINK = re.compile(r"(cart|checkout|login|signin|wishlist|compare|add-?to|account|help|javascript|mailto|tel:|/c/|/category|/brand)", re.I)


def clean_url(raw, base):
    """يحوّل الرابط إلى رابط مطلق نظيف. يرجع '' إن لم يكن رابط منتج صالحاً."""
    if not raw or not isinstance(raw, str):
        return ""
    raw = raw.strip()
    if raw.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    url = urljoin(base, raw)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    if p.path.startswith("/sspa/click"):               # إعلان أمازون ← الرابط الحقيقي داخل المعامل url
        inner = parse_qs(p.query).get("url", [""])[0]
        return clean_url(inner, base) if inner else ""
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", p.path)
    if m and "amazon" in p.netloc:
        return f"{p.scheme}://{p.netloc}/dp/{m.group(1)}"
    q = "&".join(x for x in p.query.split("&") if x and not _TRACK.match(x.split("=")[0]))
    return urlunparse((p.scheme, p.netloc, p.path, "", q, ""))


# ---------------------------------------------------------------- الأسعار
PRICE_RE = re.compile(
    r"(?:SAR|SR|ر\s?\.?\s?س\.?|ريال|﷼)\s*(\d+(?:\s?[.,]\s?\d{1,3})?)"
    r"|(\d+(?:\s?[.,]\s?\d{1,3})?)\s*(?:SAR|SR|ر\s?\.?\s?س\.?|ريال|﷼)", re.I)
BARE_RE = re.compile(r"^\d{1,5}[.,]\d{1,3}$")
_UNIT_TOKENS = {"l", "ml", "kg", "g", "gm", "pcs", "pc", "لتر", "مل", "ملل", "جم", "كجم", "غم", "حبة", "قطعة", "x"}
_AR_START = r"(?<![\u0621-\u064A])"          # بداية كلمة عربية (حتى لا تُطابَق «وفر» داخل «متوفر»)
_INSTALL = re.compile(_AR_START + r"(?:تقسيط|قسط|دفعات|دفعة|شهري|توفير|وفّر|وفر|شحن|ضريبة)|tamara|tabby|installments?|payments? of|/\s*month|per month|\bsave\b|shipping|\bvat\b", re.I)
_DISCOUNT = re.compile(r"%\s*off|off\s*%|\d\s*%|" + _AR_START + r"(?:خصم|تخفيض|وفر)|\bsave\b", re.I)
_UNAVAIL = re.compile(r"(نفد|نفذ|غير متوفر|غير متاح|out of stock|sold out|currently unavailable|unavailable)", re.I)
_STRUCK = {"old", "was", "strike", "strikethrough", "striked", "original", "before", "regular", "compare", "cross",
           "crossed", "deleted", "linethrough", "oldprice", "listprice", "wasprice", "mrp", "prev", "previous"}


def _to_float(s):
    s = re.sub(r"\s", "", s).replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 0 < v < 100000 else None


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


def _is_struck(node, card):
    for a in node.parents:
        if a.name in ("del", "s", "strike"):
            return True
        meta = (" ".join(a.get("class", [])) + " " + (a.get("id") or "")).lower()
        if "line-through" in meta or "a-text-price" in meta or "linethrough" in meta:
            return True
        if _STRUCK & set(re.split(r"[\s_\-]+", meta)) or "line-through" in (a.get("style") or "").lower():
            return True
        if a is card:
            break
    return False


def _currency_prices(text):
    out = []
    for m in PRICE_RE.finditer(text):
        after = text[m.end():m.end() + 14]
        before = text[max(0, m.start() - 28):m.start()]
        if re.match(r"\s*(/|لكل|per\b|\()", after) or _INSTALL.search(before) or _INSTALL.match(after.strip()):
            continue
        v = _to_float(m.group(1) or m.group(2))
        if v:
            out.append(v)
    return out


def _bare_prices(tokens):
    out = []
    for i, t in enumerate(tokens):
        if BARE_RE.match(t):
            nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
            if nxt in _UNIT_TOKENS or nxt.startswith("%"):
                continue
            v = _to_float(t)
            if v:
                out.append(v)
    return out


def _card_info(card, bare=False):
    """السعر الحالي كما يظهر في البطاقة + السعر القديم (إن وُجد) + هل المنتج متوفر."""
    tokens, struck, all_tokens = [], [], []
    for node in card.find_all(string=True):
        if node.parent.name in ("script", "style", "noscript"):
            continue
        t = _MARKS.sub("", str(node)).replace("\xa0", " ").translate(_AR).strip()
        if not t:
            continue
        all_tokens.append(t)
        (struck if _is_struck(node, card) else tokens).append(t)
    text = " ".join(tokens)
    prices = _currency_prices(text)
    if not prices and bare:
        prices = _bare_prices(tokens)
    if not prices:
        prices = _currency_prices(" ".join(all_tokens))
    if not prices:
        return None
    cur, old = prices[0], None
    if len(prices) >= 2 and _DISCOUNT.search(text):    # سعر قديم + جديد: الحالي هو الأقل
        cur = min(prices[:2])
        old = max(prices[:2]) if max(prices[:2]) > cur else None
    if old is None:
        sp = _currency_prices(" ".join(struck)) or (_bare_prices(struck) if bare else [])
        if sp and max(sp) > cur:
            old = max(sp)
    return {"price": cur, "old": old, "available": not _UNAVAIL.search(" ".join(all_tokens))}


def _card_price(card, bare=False):
    info = _card_info(card, bare)
    return info["price"] if info else None


# ---------------------------------------------------------------- أسماء/روابط البطاقات
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
    if img and len(img["alt"].strip()) >= 6:
        return img["alt"].strip()
    parts = [s.strip() for s in card.stripped_strings
             if len(s.strip()) >= 6 and not PRICE_RE.search(s) and not BARE_RE.match(s.strip())]
    return max(parts, key=len) if parts else None


def _best_link(card, base):
    best, score = "", -99
    for a in card.find_all("a", href=True):
        href = a["href"]
        sc = 0
        if a.find(["h1", "h2", "h3", "h4", "h5"]) or a.get("title"):
            sc += 3
        if a.find("img"):
            sc += 2
        if re.search(r"/(p|dp|product|products|pd|item|prod)/|/p/|sku|\.html?$", href, re.I):
            sc += 3
        if _BAD_LINK.search(href):
            sc -= 6
        url = clean_url(href, base)
        if url and sc > score:
            best, score = url, sc
    return best if score > -3 else ""


def _item(name, price, url, base, old=None, available=True):
    return {"name": re.sub(r"\s+", " ", name).strip()[:160], "price": price,
            "url": clean_url(url, base) if url else "", "old": old, "available": available}


def _dedup(items):
    seen, out = set(), []
    for it in items:
        k = (it["name"], it["price"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


# ---------------------------------------------------------------- أمازون
def _amazon(soup, base, **_):
    out = []
    origin = "{0.scheme}://{0.netloc}".format(urlparse(base))
    for div in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = div.get("data-asin")
        if not asin:
            continue
        h2 = div.find("h2")
        name = (h2.get("aria-label") or h2.get_text(" ", strip=True)) if h2 else None
        price = old = None
        for p in div.select("span.a-price"):
            off = p.select_one("span.a-offscreen")
            val = _to_price(_MARKS.sub("", (off or p).get_text(" ", strip=True)).translate(_AR))
            if "a-text-price" in (p.get("class") or []):
                old = old or val                           # السعر القديم المشطوب
            elif val and not price:
                price = val
        if old and price and old <= price:
            old = None
        avail = not _UNAVAIL.search(div.get_text(" ", strip=True))
        if name and price:
            out.append({"name": re.sub(r"\s+", " ", name).strip()[:160], "price": price,
                        "url": f"{origin}/dp/{asin}", "old": old, "available": avail})
    return out


# ---------------------------------------------------------------- ماجنتو (العثيم وغيرها)
def _magento(soup, base, **_):
    out = []
    for li in soup.select("li.product-item, div.product-item"):
        a = li.select_one("a.product-item-link") or li.select_one(".product-item-name a")
        if not a or not a.get("href"):
            continue
        fp = li.select_one('[data-price-type="finalPrice"]')
        price = _to_price(fp.get("data-price-amount")) if fp is not None else None
        if not price:
            sp = li.select_one(".special-price .price, .price-box .price")
            price = _to_price(sp.get_text(" ", strip=True)) if sp is not None else None
        if not price:
            continue
        op = li.select_one('[data-price-type="oldPrice"]')
        old = _to_price(op.get("data-price-amount")) if op is not None else None
        out.append(_item(a.get_text(" ", strip=True), price, a["href"], base,
                         old if old and old > price else None, not _UNAVAIL.search(li.get_text(" ", strip=True))))
    return out


# ---------------------------------------------------------------- بطاقات عبر روابط المنتجات
def _link_cards(soup, base, product_re=None, bare=False, **_):
    if not product_re:
        return []
    rx = re.compile(product_re)
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        if not rx.search(a["href"]):
            continue
        cur = a
        for _i in range(8):
            parent = cur.parent
            if parent is None or parent.name in ("body", "html"):
                break
            distinct = {x["href"].split("?")[0] for x in parent.find_all("a", href=True) if rx.search(x["href"])}
            if len(distinct) > 1:
                break
            cur = parent
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        name = (a.get("title") or "").strip() or _card_name(cur)
        info = _card_info(cur, bare)
        url = clean_url(a["href"], base)
        if name and info and url:
            out.append({"name": re.sub(r"\s+", " ", name).strip()[:160], "price": info["price"], "url": url,
                        "old": info["old"], "available": info["available"]})
    return out


# ---------------------------------------------------------------- تحليل HTML عام
def _html_cards(soup, base, bare=False, **_):
    out, used = [], set()
    pred = (lambda s: PRICE_RE.search(s) or BARE_RE.match(s.strip())) if bare else PRICE_RE
    for node in soup.find_all(string=pred):
        if node.parent is None or node.parent.name in ("script", "style", "noscript"):
            continue
        cur = node.parent
        for _i in range(8):
            if cur is None or cur.name in ("body", "html"):
                break
            has_link = (cur.name == "a" and cur.get("href")) or cur.find("a", href=True)
            if has_link:
                if id(cur) in used or len(cur.find_all(string=pred)) > 4:   # بطاقة مُعالَجة أو حاوية قائمة
                    break
                name = _card_name(cur)
                if name:
                    used.add(id(cur))
                    info = _card_info(cur, bare)
                    if info:
                        out.append(_item(name, info["price"], _best_link(cur, base), base, info["old"], info["available"]))
                    break
            cur = cur.parent
    return out


# ---------------------------------------------------------------- JSON-LD و JSON المضمّن
def _jsonld(soup, base, **_):
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            t = o.get("@type")
            if "Product" in (t if isinstance(t, list) else [t]):
                offers = o.get("offers")
                offers = offers if isinstance(offers, list) else [offers] if offers else []
                price = next((p for p in (_to_price(of.get("price") or of.get("lowPrice"))
                                          for of in offers if isinstance(of, dict)) if p), None)
                name = o.get("name")
                if isinstance(name, str) and len(name) >= 3 and price:
                    out.append(_item(name, price, o.get("url"), base))
            for v in o.values():
                if isinstance(v, (list, dict)):
                    walk(v)

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(s.string or s.get_text() or ""))
        except Exception:
            continue
    return out


NAME_KEYS = ("name", "title", "productName", "product_name", "nameAr", "name_ar", "itemName", "displayName", "product_title")
SALE_KEYS = ("salePrice", "sale_price", "specialPrice", "special_price", "offerPrice", "offer_price", "finalPrice",
             "final_price", "currentPrice", "current_price", "sellingPrice", "selling_price", "discountedPrice")
REG_KEYS = ("price", "priceValue", "price_value", "lowPrice")
URL_KEYS = ("url", "link", "href", "permalink", "productUrl", "product_url")


def _to_name(v):
    if isinstance(v, dict):
        v = v.get("ar") or v.get("ar_SA") or next((x for x in v.values() if isinstance(x, str)), None)
    if isinstance(v, str):
        v = re.sub(r"\s+", " ", v).strip()
        if len(v) >= 3 and not v.startswith(("http", "/")):
            return v[:160]
    return None


def _embedded_json(soup, base, **_):
    out = []

    def walk(o, depth=0):
        if depth > 25:
            return
        if isinstance(o, dict):
            name = next((n for n in (_to_name(o.get(k)) for k in NAME_KEYS if k in o) if n), None)
            sale = next((p for p in (_to_price(o.get(k)) for k in SALE_KEYS if k in o) if p), None)
            reg = next((p for p in (_to_price(o.get(k)) for k in REG_KEYS if k in o) if p), None)
            price = sale if sale else reg                     # السعر بعد الخصم أولاً
            if name and price:
                url = next((o[k] for k in URL_KEYS if isinstance(o.get(k), str) and "/" in o[k]), "")
                out.append(_item(name, price, url, base, reg if sale and reg and reg > sale else None))
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


# ---------------------------------------------------------------- الواجهة
def extract_products(html, base_url="", relevant=None, product_re=None, bare=False):
    """يرجع (المنتجات، اسم الطريقة). يختار الطريقة ذات أكثر نتائج مطابقة؛ وعند التعادل الأولى بالترتيب."""
    soup = BeautifulSoup(html, "html.parser")
    best, best_name, best_score = [], "none", -1
    for name, fn in (("أمازون", _amazon), ("ماجنتو", _magento), ("بطاقات المنتجات", _link_cards), ("تحليل HTML", _html_cards),
                     ("JSON-LD", _jsonld), ("JSON مضمّن", _embedded_json)):
        try:
            items = _dedup(fn(soup, base_url, product_re=product_re, bare=bare))
        except Exception:
            items = []
        score = sum(1 for i in items if relevant(i["name"])) if relevant else len(items)
        if items and (score > best_score or (score == best_score == 0 and len(items) > len(best))):
            best, best_name, best_score = items, name, score
    return best, best_name
