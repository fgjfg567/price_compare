"""مزوّدو بيانات المتاجر + حفظ/قراءة إعداد المتاجر (stores.json).

أوضاع المتجر:
  demo    بيانات تجريبية (للتجربة بلا إنترنت)
  http    بحث حي: يرسل طلب بحث لموقع المتجر ويقرأ النتائج
  browser بحث حي عبر متصفح آلي (للمواقع التي تعرض النتائج بجافاسكربت)
"""
import datetime
import hashlib
import json
import os
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlparse, urlunparse

from database import fuzzy_match, normalize
from scraper import ScrapeError, extract_products, fetch_html

CONFIG_PATH = os.path.join(os.getenv("FLET_APP_STORAGE_DATA", "."), "stores.json")
MODE_LABEL = {"demo": "تجريبي", "http": "مباشر (HTTP)", "browser": "مباشر (متصفح)"}
DEFAULT_STORES = [
    {"name": "بندة", "url": "", "mode": "demo", "factor": 1.00},
    {"name": "هنجرستيشن", "url": "", "mode": "demo", "factor": 1.06},
    {"name": "نينجا", "url": "", "mode": "demo", "factor": 0.98},
    {"name": "العثيم", "url": "", "mode": "demo", "factor": 0.96},
    {"name": "كيو", "url": "", "mode": "demo", "factor": 1.02},
    {"name": "نون", "url": "", "mode": "demo", "factor": 1.03},
    {"name": "امازون", "url": "", "mode": "demo", "factor": 1.08},
]

CATALOG = [
    ("حليب المراعي كامل الدسم", "1 لتر", 7.50), ("حليب المراعي قليل الدسم", "1 لتر", 7.25),
    ("حليب نادك طويل الأجل", "1 لتر", 6.95), ("بيض أبيض", "30 حبة", 19.00),
    ("أرز بسمتي الشعلان", "5 كجم", 48.00), ("أرز بسمتي أبو كاس", "5 كجم", 52.00),
    ("زيت عافية ذرة", "1.5 لتر", 17.50), ("زيت نور ذرة", "1.5 لتر", 16.50),
    ("زيت نور ذرة", "3 لتر", 31.00), ("زيت نور عباد الشمس", "1.8 لتر", 19.50),
    ("سكر الأسرة ناعم", "2 كجم", 9.75), ("شاي ليبتون أسود", "100 كيس", 21.00),
    ("قهوة نسكافيه جولد", "200 جم", 46.00), ("جبنة كيري مثلثات", "24 قطعة", 16.50),
    ("عصير المراعي برتقال", "1 لتر", 8.50), ("مياه نوفا", "12 × 330 مل", 9.00),
    ("تونة هيلثي بوينت", "185 جم", 8.25), ("حفاضات بامبرز مقاس 4", "58 حفاضة", 89.00),
]


def _h(s):
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16)


class BaseProvider:
    def __init__(self, name):
        self.name = name

    def search(self, query):
        raise NotImplementedError

    def diagnose(self, query):
        try:
            items = self.search(query)
            return {"ok": True, "count": len(items), "relevant": len(items), "samples": items[:5]}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class DemoProvider(BaseProvider):
    def __init__(self, name, factor=1.0, coverage=0.85):
        super().__init__(name)
        self.factor, self.coverage = factor, coverage

    def search(self, query):
        week = datetime.date.today().isocalendar()[1]
        out = []
        for name, unit, base in CATALOG:
            key = f"{self.name}|{name}|{unit}"
            if (_h(key) % 100) / 100 > self.coverage:
                continue
            if not fuzzy_match(query, normalize(f"{name} {unit}")):
                continue
            spread = 1 + ((_h(key) % 9) - 4) / 100
            drift = 1 + ((_h(key + str(week)) % 11) - 5) / 100
            out.append({"name": name, "unit": unit, "price": round(base * self.factor * spread * drift, 2), "url": ""})
        return out


class WebSearchProvider(BaseProvider):
    """يبحث في موقع المتجر كأي مستخدم، ويقرأ الأسعار من صفحة النتائج."""

    def __init__(self, name, url, mode="http"):
        super().__init__(name)
        self.url, self.mode = url, mode

    def _run(self, query):
        if "{q}" not in self.url:
            raise ScrapeError("رابط البحث لم يُضبط. افتح تبويب «إعداد المتاجر» وضع رابط يحتوي {q}.")
        url = self.url.replace("{q}", quote_plus(query))
        html = fetch_html(url, self.mode)
        rel_fn = lambda n: fuzzy_match(query, normalize(n))
        items, strategy = extract_products(html, url, relevant=rel_fn)
        return url, items, [i for i in items if rel_fn(i["name"])], strategy, len(html)

    def search(self, query):
        url, items, rel, strategy, size = self._run(query)
        if not items:
            hint = ("الصفحة قد تُحمَّل بجافاسكربت، جرّب وضع «المتصفح»." if self.mode == "http"
                    else "لم أتعرف على منتجات في الصفحة، قد يكون الموقع حجب الطلب أو تغيّر شكل الصفحة.")
            raise ScrapeError(f"لم أجد منتجات وأسعاراً في الصفحة. {hint}")
        return [{"name": i["name"], "unit": "", "price": i["price"], "url": i["url"]} for i in rel]

    def diagnose(self, query):
        try:
            url, items, rel, strategy, size = self._run(query)
            return {"ok": bool(items), "url": url, "strategy": strategy, "count": len(items),
                    "relevant": len(rel), "page_kb": size // 1024, "samples": (rel or items)[:5],
                    "error": None if items else "لم أجد منتجات وأسعاراً في الصفحة (جرّب وضع المتصفح أو راجع الرابط)."}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def make_provider(s):
    if s.get("mode") in ("http", "browser"):
        return WebSearchProvider(s["name"], s.get("url", ""), s["mode"])
    return DemoProvider(s["name"], s.get("factor", 1.0))


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return [dict(s) for s in DEFAULT_STORES]


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def build_providers(cfg):
    return {s["name"]: make_provider(s) for s in cfg}


_PARAMS = ("q", "k", "query", "search", "keyword", "keywords", "text", "term", "s", "searchterm", "search_query")


def to_template(url, word=""):
    """يحوّل رابط نتائج بحث منسوخ من المتجر إلى قالب يحتوي {q}."""
    url = (url or "").strip()
    if not url or "{q}" in url:
        return url
    if word:
        for v in (word, quote(word), quote_plus(word)):
            if v and v in url:
                return url.replace(v, "{q}", 1)
    p = urlparse(url)
    qs = parse_qsl(p.query, keep_blank_values=True)
    for i, (k, _) in enumerate(qs):
        if k.lower() in _PARAMS:
            qs[i] = (k, "{q}")
            return urlunparse(p._replace(query=urlencode(qs, safe="{}")))
    return url
