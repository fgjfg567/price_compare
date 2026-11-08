"""منطق التطبيق المشترك (بدون واجهة) — تستخدمه نسخة أندرويد."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import database as db
from providers import build_providers, load_config, make_provider, save_config, to_template


class Service:
    def __init__(self):
        self.reload()

    def reload(self):
        self.cfg = load_config()
        for s in self.cfg:   # وضع «المتصفح» غير متاح على الجوال
            if s.get("mode") not in ("demo", "http"):
                s["mode"] = "http" if s.get("url") else "demo"
        self.providers = build_providers(self.cfg)
        db.init_db(list(self.providers))
        self.stores = db.get_stores(list(self.providers))

    def demo_names(self):
        return [s["name"] for s in self.cfg if s["mode"] == "demo"]

    def _fetch_one(self, name, query):
        try:
            return name, self.providers[name].search(query), None
        except Exception as e:
            return name, None, str(e)

    def ensure_fresh(self, names, query, ttl=6, force=False):
        """يبحث في المتاجر بالتوازي، ولا يعيد الجلب إن كانت الأسعار المحفوظة حديثة."""
        qn = db.normalize(query)
        status, todo = {}, []
        for n in names:
            last = db.last_fetch(self.stores[n], qn)
            if last and not force and datetime.now() - last < timedelta(hours=ttl):
                status[n] = "✔️ من الأسعار المحفوظة"
            else:
                todo.append(n)
        if todo:
            with ThreadPoolExecutor(max_workers=len(todo)) as ex:
                for n, items, err in ex.map(lambda n: self._fetch_one(n, query), todo):
                    if err:
                        status[n] = f"❌ {err}"
                    else:
                        new, changed = db.upsert_products(self.stores[n], items)
                        db.mark_fetched(self.stores[n], qn)
                        status[n] = f"✅ {len(items)} منتج ({new} جديد، {changed} تغيّر سعره)"
        return status

    def results(self, names, query, by="price"):
        return db.search_products(query, [self.stores[n] for n in names], by)

    def test(self, store_cfg, url, mode, word, query):
        url = to_template(url, word)
        info = make_provider({**store_cfg, "url": url, "mode": mode}).diagnose(query)
        info["template"] = url
        return info

    def save(self, new_cfg, word=""):
        for old, new in zip(self.cfg, new_cfg):
            new["url"] = to_template(new["url"], word)
            if (old.get("url"), old["mode"]) != (new["url"], new["mode"]):
                db.clear_store(self.stores[old["name"]])
        save_config(new_cfg)
        self.reload()
