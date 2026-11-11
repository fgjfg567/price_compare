"""بحث أمازون السعودية + تحويل أي رابط أمازون إلى رابط عمولة (Affiliate).

المنتج لا يُسقَط أبداً لأن سعره غير متاح: يُعرض بلا سعر مع زر «عرض على أمازون».
"""
import re
import threading
import time
from urllib import robotparser
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.amazon.sa"
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Mobile Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_MARKS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_TRACK = re.compile(r"^(ref|ref_|pd_rd|pf_rd|qid|sr$|crid|sprefix|content-id|dib|psc|th$|spla|sp_csd|fbclid|gclid|utm_|tag$|linkcode|linkid|ascsubtag)", re.I)
SHORT_HOSTS = ("amzn.to", "a.co", "amzn.eu", "amzn.asia", "amzn.me")

# (المفتاح، العنوان، معامل الترتيب في أمازون)
SORTS = [("price_desc", "الأغلى أولاً", "price-desc-rank"), ("price_asc", "الأرخص أولاً", "price-asc-rank"),
         ("rating", "الأعلى تقييماً", "review-rank"), ("discount", "أعلى خصم", "")]


class AmazonError(Exception):
    def __init__(self, msg, code="error"):
        super().__init__(msg)
        self.code = code   # robots | blocked | http | network | unsupported


# ---------------------------------------------------------------- الروابط
def affiliate(url, tag):
    """يحوّل رابط أمازون إلى رابط عمولة بمعرّفك. الروابط المختصرة (amzn.to) تُترك كما هي."""
    if not url:
        return ""
    p = urlparse(url if "//" in url else "https://" + url)
    host = p.netloc.lower()
    if host in SHORT_HOSTS or "amazon." not in host:
        return url
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", p.path)
    if m:
        path, query = f"/dp/{m.group(1)}", []
    else:
        path = p.path
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACK.match(k)]
    if tag:
        query.append(("tag", tag))
    return urlunparse((p.scheme or "https", p.netloc, path, "", urlencode(query), ""))


def product_url(asin, tag):
    return affiliate(f"{BASE}/dp/{asin}", tag)


def search_page_url(query, tag, sort=""):
    return affiliate(f"{BASE}/s?k={quote_plus(query)}" + (f"&s={sort}" if sort else ""), tag)


# ---------------------------------------------------------------- الجلب
_lock, _last, _robots = threading.Lock(), [0.0], {}


def _throttle(gap=1.2):
    with _lock:
        now = time.time()
        slot = max(now, _last[0] + gap)
        _last[0] = slot
    if slot > now:
        time.sleep(slot - now)


def _allowed(url):
    if "robots" not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            r = requests.get(BASE + "/robots.txt", headers=HEADERS, timeout=8)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots["robots"] = rp
    return _robots["robots"].can_fetch("*", url)


def _get(url):
    if not _allowed(url):
        raise AmazonError("أمازون يمنع البحث الآلي من هذه الصفحة.", "robots")
    _throttle()
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
    except requests.RequestException:
        raise AmazonError("تعذّر الاتصال بأمازون. تحقق من الإنترنت.", "network")
    html = r.text
    if r.status_code in (403, 429, 503) or "validateCaptcha" in html or "api-services-support@amazon" in html:
        raise AmazonError("أمازون طلب التحقق ومنع الطلب الآلي مؤقتاً.", "blocked")
    if r.status_code >= 400:
        raise AmazonError(f"خطأ من أمازون (HTTP {r.status_code}).", "http")
    return html


# ---------------------------------------------------------------- القراءة
def _to_price(text):
    s = re.sub(r"[^\d.,]", "", _MARKS.sub("", text or "").translate(_AR))
    if not s:
        return None
    if "," in s and "." not in s and re.search(r",\d{1,2}$", s):
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if 0 < v < 1_000_000 else None


def parse_results(html):
    soup = BeautifulSoup(html, "html.parser")
    divs = soup.select('div[data-component-type="s-search-result"][data-asin]')
    out = []
    for div in divs:
        asin = div.get("data-asin")
        if not asin:
            continue
        h2 = div.find("h2")
        name = (h2.get("aria-label") or h2.get_text(" ", strip=True)) if h2 else ""
        if not name:
            img = div.select_one("img.s-image")
            name = img.get("alt", "") if img else ""
        if not name:
            continue
        price = old = None
        for p in div.select("span.a-price"):
            off = p.select_one("span.a-offscreen")
            val = _to_price((off or p).get_text(" ", strip=True))
            if "a-text-price" in (p.get("class") or []):
                old = old or val
            elif val and not price:
                price = val
        if old and price and old <= price:
            old = None
        img = div.select_one("img.s-image")
        rating = reviews = None
        ra = div.select_one("span.a-icon-alt")
        if ra:
            m = re.search(r"\d+(?:[.,]\d+)?", ra.get_text().translate(_AR))
            rating = float(m.group().replace(",", ".")) if m else None
        rv = div.select_one("span.a-size-base.s-underline-text")
        if rv:
            digits = re.sub(r"\D", "", rv.get_text().translate(_AR))
            reviews = int(digits) if digits else None
        full = div.get_text(" ", strip=True).lower()
        out.append({
            "asin": asin, "name": re.sub(r"\s+", " ", name).strip()[:180], "price": price, "old_price": old,
            "discount_pct": round((old - price) / old * 100) if old and price else 0,
            "image": img.get("src", "") if img else "", "rating": rating, "reviews": reviews,
            "prime": bool(div.select_one("i.a-icon-prime")), "sponsored": ("sponsored" in full or "ممول" in full),
            "url": f"{BASE}/dp/{asin}"})
    return out, bool(divs), soup.get_text(" ").lower()


def search(query, page=1, sort=""):
    """يرجع {'items': [...], 'has_more': bool}. يرفع AmazonError عند المنع أو الفشل."""
    url = f"{BASE}/s?k={quote_plus(query.strip())}" + (f"&s={sort}" if sort else "") + (f"&page={page}" if page > 1 else "")
    items, found, text = parse_results(_get(url))
    if not found:
        if any(k in text for k in ("did not match any products", "no results for", "لم تتطابق", "لا توجد نتائج")):
            return {"items": [], "has_more": False}
        raise AmazonError("لم أتمكن من قراءة نتائج أمازون (قد يكون تغيّر شكل الصفحة).", "unsupported")
    return {"items": items, "has_more": len(items) >= 12}
