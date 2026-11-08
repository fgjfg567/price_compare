"""استخراج حجم المنتج من اسمه وحساب السعر لكل لتر/كجم (لمقارنة عادلة بين الأحجام)."""
import re

_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_U = r"(لتر|ليتر|ملل|مل|ل|كجم|كغم|كيلوجرام|كيلو|كلغ|جرام|غرام|جم|غم|liters|liter|litres|litre|ltr|ml|kg|gm|g|l)"
_END = r"(?![A-Za-z\u0600-\u06FF])"
_NUM = r"(\d+(?:[.,]\d+)?)"
_MULTI = re.compile(rf"(\d+)\s*[x×*]\s*{_NUM}\s*{_U}{_END}", re.I)
_SINGLE = re.compile(rf"{_NUM}\s*{_U}{_END}", re.I)

# الوحدة -> (المعامل، النوع)
_BASE = {}
for _k in ("لتر", "ليتر", "liters", "liter", "litres", "litre", "ltr", "l", "ل"):
    _BASE[_k] = (1.0, "L")
for _k in ("مل", "ملل", "ml"):
    _BASE[_k] = (0.001, "L")
for _k in ("كجم", "كغم", "كيلوجرام", "كيلو", "كلغ", "kg"):
    _BASE[_k] = (1.0, "kg")
for _k in ("جم", "غم", "جرام", "غرام", "gm", "g"):
    _BASE[_k] = (0.001, "kg")


def parse_size(text):
    """يرجع (الكمية بالوحدة الأساسية، 'L' أو 'kg') أو None."""
    t = (text or "").translate(_AR)
    m = _MULTI.search(t)
    if m:
        count, qty, unit = int(m.group(1)), m.group(2), m.group(3)
    else:
        m = _SINGLE.search(t)
        if not m:
            return None
        count, qty, unit = 1, m.group(1), m.group(2)
    factor, kind = _BASE[unit.lower()]
    total = count * float(qty.replace(",", ".")) * factor
    return (total, kind) if total > 0 else None


def unit_price(price, text):
    """يرجع (السعر لكل وحدة، النص مثل 'ر.س/لتر') أو (None, '')."""
    s = parse_size(text)
    if not s:
        return None, ""
    qty, kind = s
    return round(price / qty, 2), "ر.س/لتر" if kind == "L" else "ر.س/كجم"
