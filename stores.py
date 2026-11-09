"""سجلّ المتاجر + البحث التلقائي + مقارنة السلة + تتبّع المفضلة + فحص المتاجر.

لا تُخزَّن أسعار نتائج البحث. المخزَّن فقط: تفضيلاتك، سلتك، مفضلتك (مع سعر وقت الحفظ للمقارنة).
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import quote_plus, urlencode, urljoin

from bs4 import BeautifulSoup

from scraper import ScrapeError, check_url, extract_products, fetch_html
from textutil import fuzzy_match, normalize
from units import parse_size, unit_price

DATA_DIR = os.getenv("FLET_APP_STORAGE_DATA", ".")
os.makedirs(DATA_DIR, exist_ok=True)
PREFS_PATH = os.path.join(DATA_DIR, "prefs.json")

STORES = [
    {"key": "amazon", "name": "امازون", "color": "#FF9900", "fg": "#111111", "logo": "amazon.sa",
     "homes": ["https://www.amazon.sa/"], "patterns": ["https://www.amazon.sa/s?k={q}"]},
    {"key": "carrefour", "name": "كارفور", "color": "#0A4DA2", "logo": "carrefourksa.com", "product_re": r"/p/\d+",
     "homes": ["https://www.carrefourksa.com/mafsau/ar"],
     "patterns": ["https://www.carrefourksa.com/mafsau/ar/search?keyword={q}"]},
    {"key": "othaim", "name": "العثيم", "color": "#0A7D3E", "logo": "othaimmarkets.com",
     "homes": ["https://www.othaimmarkets.com/"],       # ماجنتو: رابط البحث القياسي
     "patterns": ["https://www.othaimmarkets.com/catalogsearch/result/?q={q}&___store=ar",
                  "https://www.othaimmarkets.com/catalogsearch/result/?q={q}"]},
    {"key": "panda", "name": "بندة", "color": "#E4002B", "logo": "panda.sa", "bare": True, "product_re": r"/p/\d+",
     "homes": ["https://panda.sa/ar", "https://panda.sa/en"],
     "patterns": ["https://panda.sa/ar/plp?search={q}", "https://panda.sa/ar/plp?q={q}",
                  "https://panda.sa/en/plp?search={q}", "https://panda.sa/ar/search?q={q}"]},
    {"key": "lulu", "name": "لولو", "color": "#00833E", "logo": "luluhypermarket.com", "bare": True, "product_re": r"/p/\d+",
     "homes": ["https://gcc.luluhypermarket.com/ar-sa/"],
     "patterns": ["https://gcc.luluhypermarket.com/ar-sa/search/?text={q}"]},
    {"key": "noon", "name": "نون", "color": "#F5D800", "fg": "#111111", "logo": "noon.com", "bare": True,
     "product_re": r"/p/?(?:\?|$)", "homes": ["https://www.noon.com/saudi-ar/"],
     "patterns": ["https://www.noon.com/saudi-ar/search/?q={q}"]},
    {"key": "danube", "name": "دانوب", "color": "#E30613", "logo": "danube.sa", "product_re": r"/products/",
     "homes": ["https://danube.sa/ar", "https://danube.sa/en"],
     "patterns": ["https://danube.sa/ar/search?q={q}", "https://danube.sa/en/search?q={q}"]},
    {"key": "tamimi", "name": "التميمي", "color": "#C8102E", "logo": "tamimimarkets.com", "product_re": r"/product/",
     "homes": ["https://shop.tamimimarkets.com/"],
     "patterns": ["https://shop.tamimimarkets.com/search?q={q}"]},
]
BY_KEY = {s["key"]: s for s in STORES}


# ---------------------------------------------------------------- التفضيلات
def load_prefs():
    p = {"disabled": [], "overrides": {}, "discovered": {}, "recent": [], "theme": "system",
         "basket": [], "favorites": [], "health": {}}
    try:
        with open(PREFS_PATH, encoding="utf-8") as f:
            p.update(json.load(f))
    except Exception:
        pass
    return p


def save_prefs(p):
    try:
        with open(PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------- اكتشاف رابط البحث
_SEARCH_NAMES = re.compile(r"^(q|query|search|keyword|keywords|k|text|term|s|searchterm|search_query)$", re.I)


def _from_jsonld(soup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except Exception:
            continue
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, list):
                stack.extend(o)
            elif isinstance(o, dict):
                pa = o.get("potentialAction")
                for a in (pa if isinstance(pa, list) else [pa]):
                    if isinstance(a, dict) and "Search" in str(a.get("@type")):
                        t = a.get("target")
                        t = t.get("urlTemplate") if isinstance(t, dict) else t
                        if isinstance(t, str) and "{" in t:
                            return re.sub(r"\{[^}]+\}", "{q}", t)
                stack.extend(v for v in o.values() if isinstance(v, (dict, list)))
    return None


def _from_opensearch(soup, home):
    link = soup.find("link", attrs={"type": re.compile("opensearch", re.I)})
    if not link or not link.get("href"):
        return None
    try:
        xml = BeautifulSoup(fetch_html(urljoin(home, link["href"]), "http"), "html.parser")
    except ScrapeError:
        return None
    for u in xml.find_all("url"):
        t = u.get("template", "")
        if "searchterms" in t.lower() and "html" in (u.get("type") or "html"):
            return re.sub(r"\{searchTerms\??\}", "{q}", t, flags=re.I)
    return None


def _from_form(soup, home):
    for form in soup.find_all("form"):
        if (form.get("method") or "get").lower() != "get":
            continue
        inp = form.find("input", attrs={"type": "search"}) or form.find("input", attrs={"name": _SEARCH_NAMES})
        if not inp or not inp.get("name"):
            continue
        params = [(i["name"], i.get("value", "")) for i in form.find_all("input", attrs={"type": "hidden"}) if i.get("name")]
        params.append((inp["name"], "{q}"))
        action = urljoin(home, form.get("action") or home)
        return action + ("&" if "?" in action else "?") + urlencode(params, safe="{}")
    return None


def discover_template(store):
    for home in store["homes"]:
        try:
            html = fetch_html(home, "http")
        except ScrapeError:
            continue
        soup = BeautifulSoup(html, "html.parser")
        tpl = _from_jsonld(soup) or _from_opensearch(soup, home) or _from_form(soup, home)
        if tpl:
            return tpl
    return None


# ---------------------------------------------------------------- البحث في متجر
def _result(store, state, note, items=None, template=""):
    return {"key": store["key"], "name": store["name"], "state": state, "note": note,
            "items": items or [], "template": template}


def _make_items(store, found):
    out = []
    for i in found:
        up, ul = unit_price(i["price"], i["name"])
        old = i.get("old")
        pct = round((old - i["price"]) / old * 100) if old and old > i["price"] else 0
        out.append({"store_key": store["key"], "store": store["name"], "name": i["name"], "price": i["price"],
                    "old_price": old if pct else None, "discount_pct": pct, "available": i.get("available", True),
                    "url": i["url"], "link": "product", "unit_price": up, "unit_label": ul})
    return out


def _fix_links(items, tpl):
    """يتأكد أن روابط المنتجات تعمل؛ الرابط المفقود أو المعطّل يُستبدل بصفحة نتائج البحث عن المنتج نفسه."""
    items.sort(key=lambda r: r["price"])
    probe = [it for it in items if it["url"]][:8]
    bad = set()
    if probe:
        with ThreadPoolExecutor(max_workers=4) as ex:
            oks = list(ex.map(lambda it: check_url(it["url"]), probe))
        bad = {id(it) for it, ok in zip(probe, oks) if ok is False}
    for it in items:
        if not it["url"] or id(it) in bad:
            it["url"] = tpl.replace("{q}", quote_plus(it["name"][:70]))
            it["link"] = "search"


def search_store(store, query, prefs, verify_links=True):
    """يرجع dict: state = ok | empty | blocked | unsupported | network."""
    rel = lambda n: fuzzy_match(query, normalize(n))
    cands, seen = [], set()
    for t in [prefs["overrides"].get(store["key"]), prefs["discovered"].get(store["key"])] + store["patterns"]:
        if t and "{q}" in t and t not in seen:
            seen.add(t)
            cands.append(t)
    codes, empty_tpl = [], []
    ext = {"product_re": store.get("product_re"), "bare": store.get("bare", False)}

    def attempt(tpl):
        url = tpl.replace("{q}", quote_plus(query))
        items, _ = extract_products(fetch_html(url, "http"), url, relevant=rel, **ext)
        if not items:
            raise ScrapeError("لا منتجات في الصفحة", "noitems")
        return [i for i in items if rel(i["name"])]

    def run(tpls):
        for tpl in tpls:
            try:
                found = attempt(tpl)
            except ScrapeError as e:
                codes.append(e.code)
                continue
            if found:
                made = _make_items(store, found)
                if verify_links:
                    _fix_links(made, tpl)
                else:
                    made.sort(key=lambda r: r["price"])
                return _result(store, "ok", f"{len(made)} نتيجة", made, tpl)
            empty_tpl.append(tpl)
        return None

    res = run(cands)
    if res:
        return res
    try:
        tpl = discover_template(store) if store["homes"] else None
    except Exception:
        tpl = None
    if tpl and tpl not in seen:
        res = run([tpl])
        if res:
            prefs["discovered"][store["key"]] = tpl
            return res
    if empty_tpl:
        return _result(store, "empty", "لا توجد منتجات مطابقة في هذا المتجر", [], empty_tpl[0])
    if "network" in codes and not any(c in codes for c in ("robots", "blocked", "noitems")):
        return _result(store, "network", "تعذّر الاتصال. تحقق من الإنترنت.")
    if "robots" in codes or "blocked" in codes:
        return _result(store, "blocked", "الموقع يحجب البحث الآلي عن المنتجات.")
    return _result(store, "unsupported", "لم أتمكن من قراءة نتائج البحث من موقع هذا المتجر (قد يعرضها بجافاسكربت).")


def search_stores(query, active, prefs, verify_links=False):
    """بحث متوازٍ في عدة متاجر. يرجع {key: result}."""
    def one(s):
        try:
            return s["key"], search_store(s, query, prefs, verify_links)
        except Exception as e:
            return s["key"], _result(s, "unsupported", f"خطأ: {e}")
    with ThreadPoolExecutor(max_workers=max(1, len(active))) as ex:
        return dict(ex.map(one, active))


# ---------------------------------------------------------------- مقارنة السلة
def pick_for_query(items, query):
    """أرخص منتج متوفر يطابق البحث (ويطابق الحجم المطلوب إن ذُكر)."""
    pool = [i for i in items if i.get("available", True)]
    want = parse_size(query)
    if want:
        sized = []
        for i in pool:
            ps = parse_size(i["name"])
            if ps and ps[1] == want[1] and abs(ps[0] - want[0]) / want[0] <= 0.06:
                sized.append(i)
        pool = sized or pool
    return min(pool, key=lambda r: r["price"]) if pool else None


def compare_basket(entries, per_query, active):
    """entries: [{'q','qty'}]، per_query: {q: {store_key: [items]}} ← ملخص أرخص سلة."""
    n = len(entries)
    lines = []
    for e in entries:
        by = {s["key"]: pick_for_query(per_query.get(e["q"], {}).get(s["key"], []), e["q"]) for s in active}
        found = [(k, it) for k, it in by.items() if it]
        best = min(found, key=lambda x: x[1]["price"])[1] if found else None
        lines.append({"q": e["q"], "qty": e.get("qty", 1), "by_store": by, "best": best})
    totals = []
    for s in active:
        got = [(l, l["by_store"][s["key"]]) for l in lines if l["by_store"][s["key"]]]
        totals.append({"key": s["key"], "name": s["name"], "found": len(got), "complete": len(got) == n and n > 0,
                       "total": round(sum(it["price"] * l["qty"] for l, it in got), 2)})
    totals.sort(key=lambda t: (not t["complete"], -t["found"], t["total"]))
    complete = [t for t in totals if t["complete"]]
    mixed = round(sum(l["best"]["price"] * l["qty"] for l in lines if l["best"]), 2)
    return {"n": n, "lines": lines, "totals": totals, "best": complete[0] if complete else None,
            "worst": complete[-1] if complete else None, "mixed_total": mixed,
            "mixed_complete": all(l["best"] for l in lines) and n > 0}


def run_basket(entries, active, prefs, on_progress=None):
    per = {}
    for i, e in enumerate(entries, 1):
        per[e["q"]] = {k: r["items"] for k, r in search_stores(e["q"], active, prefs).items()}
        if on_progress:
            on_progress(i, len(entries))
    return compare_basket(entries, per, active)


# ---------------------------------------------------------------- المفضلة
def refresh_favorite(fav, prefs):
    """يجلب السعر الحالي للمنتج المحفوظ من متجره. يرجع (العنصر أو None, نتيجة المتجر)."""
    store = BY_KEY.get(fav["store_key"])
    if not store:
        return None, None
    res = search_store(store, fav["name"][:70], prefs, verify_links=False)
    target, best, br = normalize(fav["name"]), None, 0.0
    for it in res["items"]:
        r = SequenceMatcher(None, target, normalize(it["name"])).ratio()
        if r > br:
            best, br = it, r
    return (best if br >= 0.75 else None), res


# ---------------------------------------------------------------- فحص المتاجر
def health_check(store, prefs, query="زيت"):
    res = search_store(store, query, prefs, verify_links=False)
    info = {"state": res["state"], "count": len(res["items"]), "note": res["note"],
            "ts": datetime.now().strftime("%H:%M")}
    prefs["health"][store["key"]] = info
    return info
