"""سجلّ المتاجر + البحث التلقائي (بدون إدخال روابط) + اكتشاف رابط البحث من موقع المتجر.

لكل متجر قائمة «قوالب بحث» معروفة تُجرَّب بالترتيب. وإن لم تنجح، يفتح التطبيق الصفحة الرئيسية للمتجر
ويكتشف نموذج البحث بنفسه (OpenSearch / SearchAction / نموذج HTML)، ثم يتذكر القالب الناجح.
لا تُخزَّن أي أسعار: النتائج تُجلب حيّة في كل بحث.
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus, urlencode, urljoin

from bs4 import BeautifulSoup

from scraper import ScrapeError, check_url, extract_products, fetch_html
from textutil import fuzzy_match, normalize
from units import unit_price

DATA_DIR = os.getenv("FLET_APP_STORAGE_DATA", ".")
os.makedirs(DATA_DIR, exist_ok=True)
PREFS_PATH = os.path.join(DATA_DIR, "prefs.json")

# القوالب أفضل تخمين معروف وقد تحتاج تعديلاً؛ الاكتشاف التلقائي يعوّض عند الفشل.
STORES = [
    {"key": "panda", "name": "بندة", "color": "#E4002B", "logo": "panda.sa", "bare": True, "product_re": r"/p/\d+",
     "homes": ["https://panda.sa/ar", "https://panda.sa/en"],
     "patterns": ["https://panda.sa/ar/plp?search={q}", "https://panda.sa/ar/plp?q={q}",
                  "https://panda.sa/en/plp?search={q}", "https://panda.sa/ar/search?q={q}"]},
    {"key": "noon", "name": "نون", "color": "#F5D800", "fg": "#111111", "logo": "noon.com", "product_re": r"/p/?(?:\?|$)",
     "homes": ["https://www.noon.com/saudi-ar/"], "patterns": ["https://www.noon.com/saudi-ar/search/?q={q}"]},
    {"key": "amazon", "name": "امازون", "color": "#FF9900", "fg": "#111111", "logo": "amazon.sa",
     "homes": ["https://www.amazon.sa/"], "patterns": ["https://www.amazon.sa/s?k={q}"]},
    {"key": "carrefour", "name": "كارفور", "color": "#0A4DA2", "logo": "carrefourksa.com", "product_re": r"/p/\d+",
     "homes": ["https://www.carrefourksa.com/mafsau/ar"],
     "patterns": ["https://www.carrefourksa.com/mafsau/ar/search?keyword={q}"]},
    {"key": "lulu", "name": "لولو", "color": "#00833E", "logo": "luluhypermarket.com", "bare": True, "product_re": r"/p/\d+",
     "homes": ["https://gcc.luluhypermarket.com/ar-sa/"],
     "patterns": ["https://gcc.luluhypermarket.com/ar-sa/search/?text={q}"]},
    {"key": "othaim", "name": "العثيم", "color": "#0A7D3E", "logo": "othaimmarkets.com",
     "homes": ["https://www.othaimmarkets.com/", "https://www.othaimmarkets.com/ar"],
     "patterns": ["https://www.othaimmarkets.com/search?q={q}", "https://www.othaimmarkets.com/ar/search?q={q}"]},
]
BY_KEY = {s["key"]: s for s in STORES}


# ---------------------------------------------------------------- التفضيلات (لا أسعار)
def load_prefs():
    p = {"disabled": [], "overrides": {}, "discovered": {}, "recent": []}
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
        out.append({"store_key": store["key"], "store": store["name"], "name": i["name"],
                    "price": i["price"], "url": i["url"], "link": "product", "unit_price": up, "unit_label": ul})
    return out


def _fix_links(items, tpl):
    """يتأكد أن روابط المنتجات تعمل؛ الرابط المفقود أو المعطّل يُستبدل بصفحة نتائج البحث عن المنتج نفسه."""
    items.sort(key=lambda r: r["price"])
    probe = [it for it in items if it["url"]][:8]
    if probe:
        with ThreadPoolExecutor(max_workers=4) as ex:
            oks = list(ex.map(lambda it: check_url(it["url"]), probe))
        bad = {id(it) for it, ok in zip(probe, oks) if ok is False}
    else:
        bad = set()
    for it in items:
        if not it["url"] or id(it) in bad:
            it["url"] = tpl.replace("{q}", quote_plus(it["name"][:70]))
            it["link"] = "search"


def search_store(store, query, prefs):
    """يرجع dict: state = ok | empty | blocked | unsupported | network | unconfigured."""
    if store.get("unconfigured"):
        return _result(store, "unconfigured", "هذا المتجر غير معرّف بعد. أخبرني ما هو «كيو» تحديداً لأضيفه.")
    rel = lambda n: fuzzy_match(query, normalize(n))
    cands, seen = [], set()
    for t in [prefs["overrides"].get(store["key"]), prefs["discovered"].get(store["key"])] + store["patterns"]:
        if t and "{q}" in t and t not in seen:
            seen.add(t)
            cands.append(t)
    codes = []

    ext = {"product_re": store.get("product_re"), "bare": store.get("bare", False)}
    empty_tpl = []

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
                _fix_links(made, tpl)
                return _result(store, "ok", f"{len(made)} نتيجة", made, tpl)
            empty_tpl.append(tpl)       # الصفحة سليمة لكن بلا مطابقات؛ جرّب القالب التالي
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
    if store.get("app_only"):
        return _result(store, "unsupported", "المتجر يعمل عبر تطبيق الجوال فقط ولا يوفّر بحثاً عبر الويب.")
    return _result(store, "unsupported", "لم أتمكن من قراءة نتائج البحث من موقع هذا المتجر (قد يعرضها بجافاسكربت).")
