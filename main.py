"""مقارنة أسعار — نسخة أندرويد (Flet). بحث حيّ تلقائي في المتاجر، بلا حفظ للأسعار."""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import flet as ft

import stores as S

TEAL, SKY = "#0F766E", "#0EA5E9"
STATE_ICON = {"ok": (ft.Icons.CHECK_CIRCLE, "#16A34A"), "empty": (ft.Icons.REMOVE_CIRCLE_OUTLINE, "#6B7280"),
              "blocked": (ft.Icons.BLOCK, "#DC2626"), "unsupported": (ft.Icons.ERROR_OUTLINE, "#EA580C"),
              "network": (ft.Icons.WIFI_OFF, "#DC2626"), "unconfigured": (ft.Icons.HELP_OUTLINE, "#6B7280")}


def main(page: ft.Page):
    page.title = "مقارنة الأسعار"
    page.padding = 0
    page.spacing = 0
    page.theme = ft.Theme(color_scheme_seed=TEAL)
    prefs = S.load_prefs()
    st = {"gen": 0, "query": "", "items": [], "status": {}, "sort": "price", "busy": False}

    def toast(msg):
        page.open(ft.SnackBar(ft.Text(msg)))

    # ------------------------------------------------------------ عناصر مشتركة
    def badge(store, size=40):
        fg = store.get("fg", "#FFFFFF")
        fallback = ft.Container(ft.Text(store["name"][0], color=fg, weight=ft.FontWeight.BOLD, size=size * 0.45),
                                width=size, height=size, bgcolor=store["color"], border_radius=size / 2,
                                alignment=ft.alignment.center)
        if not store.get("logo"):
            return fallback
        url = f"https://www.google.com/s2/favicons?domain={store['logo']}&sz=128"
        return ft.Container(ft.Image(src=url, width=size, height=size, fit=ft.ImageFit.COVER, error_content=fallback),
                            width=size, height=size, border_radius=size / 2, bgcolor="#FFFFFF",
                            clip_behavior=ft.ClipBehavior.ANTI_ALIAS, alignment=ft.alignment.center)

    # ------------------------------------------------------------ البحث
    query_f = ft.TextField(hint_text="ابحث عن منتج… مثال: زيت من شركة نور", prefix_icon=ft.Icons.SEARCH,
                           border_radius=30, filled=True, bgcolor="#FFFFFF", color="#111827", border_color="transparent",
                           text_align=ft.TextAlign.RIGHT, content_padding=ft.padding.symmetric(horizontal=16, vertical=14),
                           on_submit=lambda e: start_search(), expand=True)
    go_btn = ft.IconButton(icon=ft.Icons.ARROW_BACK_IOS_NEW, icon_color="#FFFFFF", bgcolor="#0B5F59",
                           on_click=lambda e: start_search())

    header = ft.Container(
        gradient=ft.LinearGradient(begin=ft.alignment.top_left, end=ft.alignment.bottom_right, colors=[TEAL, SKY]),
        padding=ft.padding.only(left=16, right=16, top=14, bottom=18),
        border_radius=ft.border_radius.only(bottom_left=28, bottom_right=28),
        content=ft.Column(spacing=12, controls=[
            ft.Row([ft.IconButton(ft.Icons.TUNE, icon_color="#FFFFFF", on_click=lambda e: open_settings()),
                    ft.Column([ft.Text("مقارنة الأسعار", size=22, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                               ft.Text("ابحث مرة واحدة… وقارن كل المتاجر", size=12, color="#D1FAE5")],
                              spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END)],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Row([go_btn, query_f], spacing=8),
        ]))

    chips_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=8)
    chip_slots = {}

    def build_chips():
        chips_row.controls.clear()
        chip_slots.clear()
        for s in S.STORES:
            if s["key"] in prefs["disabled"]:
                continue
            slot = ft.Container(width=18, height=18)
            chip_slots[s["key"]] = slot
            chips_row.controls.append(ft.Container(
                content=ft.Row([slot, ft.Text(s["name"], size=13), badge(s, 26)], spacing=6),
                padding=ft.padding.symmetric(horizontal=10, vertical=6), border_radius=20,
                bgcolor=ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE), ink=True,
                on_click=lambda e, k=s["key"]: show_note(k)))

    def set_chip(key, res=None):
        slot = chip_slots.get(key)
        if not slot:
            return
        if res is None:
            slot.content = ft.ProgressRing(width=14, height=14, stroke_width=2)
        else:
            icon, color = STATE_ICON.get(res["state"], STATE_ICON["empty"])
            slot.content = ft.Icon(icon, color=color, size=18)

    def show_note(key):
        res = st["status"].get(key)
        s = S.BY_KEY[key]
        body = res["note"] if res else "لم يُبحث في هذا المتجر بعد."
        page.open(ft.AlertDialog(title=ft.Text(s["name"]), content=ft.Text(body),
                                 actions=[ft.TextButton("حسناً", on_click=lambda e: page.close(e.control.parent))]))

    sort_btns = {}

    def sort_toggle():
        def mk(k, label):
            b = ft.Container(ft.Text(label, size=12), padding=ft.padding.symmetric(horizontal=12, vertical=6),
                             border_radius=16, ink=True, on_click=lambda e: set_sort(k))
            sort_btns[k] = b
            return b
        return ft.Row([mk("unit", "الأرخص لكل لتر/كجم"), mk("price", "الأرخص سعراً")], spacing=6)

    def paint_sort():
        for k, b in sort_btns.items():
            on = st["sort"] == k
            b.bgcolor = TEAL if on else ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE)
            b.content.color = "#FFFFFF" if on else None

    def set_sort(k):
        st["sort"] = k
        paint_sort()
        render()

    banner = ft.Container(visible=False, border_radius=16, padding=14)
    results = ft.ListView(expand=True, spacing=10, padding=ft.padding.only(bottom=24))
    sort_row = sort_toggle()
    paint_sort()
    progress = ft.ProgressBar(visible=False, color=TEAL)

    def card(rank, it, best):
        s = S.BY_KEY[it["store_key"]]
        unit = f"{it['unit_price']:.2f} {it['unit_label']}" if it["unit_price"] is not None else ""
        tag = ft.Container(ft.Text("الأرخص", size=11, color="#FFFFFF", weight=ft.FontWeight.BOLD),
                           bgcolor="#16A34A", border_radius=10, padding=ft.padding.symmetric(horizontal=8, vertical=2),
                           visible=best)
        return ft.Container(
            content=ft.Row([
                ft.Column([ft.Text(f"{it['price']:.2f}", size=22, weight=ft.FontWeight.BOLD, color="#16A34A" if best else None),
                           ft.Text("ر.س", size=11), ft.Text(unit, size=10, color=SKY) if unit else ft.Container()],
                          spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Column([ft.Text(it["name"], size=14, weight=ft.FontWeight.W_600, max_lines=3,
                                   overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.RIGHT),
                           ft.Row([tag, ft.Text(s["name"], size=12, color=ft.Colors.with_opacity(0.6, ft.Colors.ON_SURFACE))],
                                  alignment=ft.MainAxisAlignment.END, spacing=6),
                           ft.Text("يفتح نتائج البحث في المتجر", size=10, color="#EA580C") if it.get("link") == "search" else ft.Container()],
                          expand=True, spacing=4, horizontal_alignment=ft.CrossAxisAlignment.END),
                badge(s, 44),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
            padding=12, border_radius=16, ink=True,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.ON_SURFACE),
            border=ft.border.all(2, "#16A34A") if best else ft.border.all(1, ft.Colors.with_opacity(0.08, ft.Colors.ON_SURFACE)),
            on_click=(lambda e, u=it["url"]: page.launch_url(u)) if it.get("url") else None)

    def render():
        items = list(st["items"])
        if st["sort"] == "unit":
            items.sort(key=lambda r: (r["unit_price"] is None, r["unit_price"] or 0, r["price"]))
        else:
            items.sort(key=lambda r: r["price"])
        if not items:
            results.controls = [] if st["busy"] else [empty_state()]
            banner.visible = False
        else:
            prices = [i["price"] for i in items]
            cheap = min(items, key=lambda r: r["price"])
            saving = max(prices) - min(prices)
            banner.visible = True
            banner.gradient = ft.LinearGradient(colors=["#16A34A", "#22C55E"])
            banner.content = ft.Column([
                ft.Text(f"الأرخص: {cheap['store']} — {cheap['price']:.2f} ر.س", color="#FFFFFF", weight=ft.FontWeight.BOLD, size=15),
                ft.Text(f"توفير يصل إلى {saving:.2f} ر.س مقارنة بالأغلى • {len(items)} نتيجة", color="#DCFCE7", size=12)],
                spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END)
            top = items[0]
            results.controls = [card(i, it, it is top) for i, it in enumerate(items, 1)]
        page.update()

    def empty_state(text=None):
        failed = [r for r in st["status"].values() if r["state"] in ("blocked", "unsupported", "network", "unconfigured")]
        msg = text or ("لا توجد نتائج مطابقة." + (" اضغط على اسم أي متجر بالأعلى لمعرفة السبب." if failed else ""))
        return ft.Container(ft.Column([ft.Icon(ft.Icons.SEARCH_OFF, size=56, color=ft.Colors.with_opacity(0.3, ft.Colors.ON_SURFACE)),
                                       ft.Text(msg, text_align=ft.TextAlign.CENTER)],
                                      horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=40, alignment=ft.alignment.center)

    recent_row = ft.Row(wrap=True, spacing=6, run_spacing=6, alignment=ft.MainAxisAlignment.END)

    def home_state():
        recent_row.controls = [ft.Container(ft.Text(q, size=12), padding=ft.padding.symmetric(horizontal=12, vertical=6),
                                            border_radius=16, ink=True, bgcolor=ft.Colors.with_opacity(0.07, ft.Colors.ON_SURFACE),
                                            on_click=lambda e, q=q: run_query(q)) for q in prefs["recent"][:8]]
        results.controls = [ft.Container(ft.Column([
            ft.Icon(ft.Icons.SHOPPING_BAG_OUTLINED, size=64, color=ft.Colors.with_opacity(0.25, ft.Colors.ON_SURFACE)),
            ft.Text("ابحث عن أي منتج لمقارنة سعره في المتاجر", text_align=ft.TextAlign.CENTER),
            ft.Text("عمليات البحث الأخيرة" if prefs["recent"] else "", size=12), recent_row],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=30, alignment=ft.alignment.center)]

    def run_query(q):
        query_f.value = q
        start_search()

    def start_search():
        q = (query_f.value or "").strip()
        active = [s for s in S.STORES if s["key"] not in prefs["disabled"]]
        if not q:
            return toast("اكتب اسم المنتج أولاً")
        if not active:
            return toast("فعّل متجراً واحداً على الأقل من الإعدادات")
        st["gen"] += 1
        gen = st["gen"]
        st.update(query=q, items=[], status={}, busy=True)
        prefs["recent"] = ([q] + [x for x in prefs["recent"] if x != q])[:8]
        progress.visible = True
        for s in active:
            set_chip(s["key"])
        results.controls, banner.visible = [], False
        page.update()

        def work():
            try:
                with ThreadPoolExecutor(max_workers=len(active)) as ex:
                    futs = {ex.submit(S.search_store, s, q, prefs): s for s in active}
                    for f in as_completed(futs):
                        if gen != st["gen"]:
                            return
                        s = futs[f]
                        try:
                            r = f.result()
                        except Exception as e:
                            r = {"key": s["key"], "name": s["name"], "state": "unsupported", "note": f"خطأ: {e}", "items": []}
                        st["status"][s["key"]] = r
                        st["items"].extend(r["items"])
                        set_chip(s["key"], r)
                        render()
            finally:
                if gen == st["gen"]:
                    st["busy"] = False
                    progress.visible = False
                    S.save_prefs(prefs)
                    render()

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ الإعدادات
    def open_settings():
        switches, urls = {}, {}
        rows = []
        for s in S.STORES:
            sw = ft.Switch(value=s["key"] not in prefs["disabled"])
            switches[s["key"]] = sw
            uf = ft.TextField(label="رابط بحث مخصص (اختياري — اتركه فارغاً)", hint_text="https://.../search?q={q}",
                              value=prefs["overrides"].get(s["key"], ""), text_size=12)
            urls[s["key"]] = uf
            rows.append(ft.ExpansionTile(
                leading=badge(s, 30), title=ft.Text(s["name"]), trailing=sw,
                controls=[ft.Container(uf, padding=10)]))

        def save(e):
            prefs["disabled"] = [k for k, sw in switches.items() if not sw.value]
            for k, uf in urls.items():
                v = uf.value.strip()
                if v != prefs["overrides"].get(k, ""):
                    prefs["discovered"].pop(k, None)
                if v:
                    prefs["overrides"][k] = v
                else:
                    prefs["overrides"].pop(k, None)
            S.save_prefs(prefs)
            page.close(dlg)
            build_chips()
            toast("تم الحفظ")
            page.update()

        dlg = ft.AlertDialog(title=ft.Text("المتاجر"), content=ft.Container(ft.Column(rows, scroll=ft.ScrollMode.AUTO), width=380, height=420),
                             actions=[ft.TextButton("إلغاء", on_click=lambda e: page.close(dlg)),
                                      ft.ElevatedButton("حفظ", on_click=save)])
        page.open(dlg)

    # ------------------------------------------------------------ التركيب
    build_chips()
    home_state()
    page.add(ft.SafeArea(expand=True, content=ft.Column(expand=True, spacing=0, controls=[
        header,
        ft.Container(expand=True, padding=ft.padding.only(left=14, right=14, top=12), content=ft.Column(expand=True, spacing=10, controls=[
            chips_row, ft.Row([sort_row], alignment=ft.MainAxisAlignment.END), progress, banner, results]))])))


ft.app(target=main)
