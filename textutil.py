"""تطبيع النص العربي ومطابقة البحث مع تسامح الأخطاء الإملائية."""
import re
from difflib import SequenceMatcher

STOPWORDS = {"من", "شركه", "ماركه", "نوع", "في", "عن", "او", "علامه", "تجاريه", "منتج"}


def normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[\u064B-\u0652\u0640]", "", t)
    t = re.sub("[أإآٱ]", "ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = re.sub(r"([\u0621-\u064A])\1+", r"\1", t)
    return re.sub(r"\s+", " ", t)


def _term_matches(term, words):
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
    keep = [t for t in terms if t not in STOPWORDS] or terms
    words = text_norm.split()
    return bool(keep) and all(_term_matches(t, words) for t in keep)
