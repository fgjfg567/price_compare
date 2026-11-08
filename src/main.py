"""مقارنة أسعار التوصيل السريع — نسخة أندرويد (Flet)."""
import threading

import flet as ft

from logic import Service

MODES = {"demo": "تجريبي", "http": "مباشر"}
TTL_HOURS = 6


def main(page: ft.Page):
    page.title = "مقارنة الأسعار"
    page.padding = 10
    svc = Service()
    state = {"query": ""}

    def toast(msg):
        page.open(ft.SnackBar(ft.Text(msg)))

    # ======================= تبويب البحث =======================
    query_f = ft.TextField(label="اسم المنتج", hint_text="مثال: زيت من شركة نور", text_align=ft.TextAlign.RIGHT,
                           expand=True, on_submit=lambda e: run_search())
    search_btn = ft.ElevatedButton("بحث", icon=ft.Icons.SEARCH, on_click=lambda e: run_search())
    refresh_btn = ft.TextButton("🔄 تحديث الأسعار", on_click=lambda e: run_search(force=True))
    sort_dd = ft.Dropdown(label="الترتيب", value="price", width=190, on_change=lambda e: render(),
                          options=[ft.dropdown.Option("price", "السعر"), ft.dropdown.Option("unit", "السعر لكل لتر/كجم")])
    checks = {n: ft.Checkbox(label=n, value=True) for n in svc.stores}
    demo_warn = ft.Text("", color=ft.Colors.ORANGE_800, size=12)
    progress = ft.ProgressBar(visible=False)
    status_col = ft.Column(spacing=2)
    status_tile = ft.ExpansionTile(title=ft.Text("حالة كل متجر"), controls=[status_col], visible=False)
    banner = ft.Text("", weight=ft.FontWeight.BOLD, color=ft.Colors.GREEN_800, visible=False)
    results = ft.ListView(expand=True, spacing=8)

    def set_all(v):
        for c in checks.values():
            c.value = v
        page.update()

    def selected():
        return [n for n, c in checks.items() if c.value]

    def refresh_demo_warning():
        d = svc.demo_names()
        demo_warn.value = ("⚠️ أسعار تجريبية (غير حقيقية) في: " + "، ".join(d) + " — اضبطها من تبويب «المتاجر».") if d else ""

    def card(i, r):
        unit = f"{r['unit_price']:.2f} {r['unit_label']}" if r["unit_price"] is not None else ""
        url = r.get("url") or ""
        return ft.Container(
            content=ft.Row([
                ft.Container(ft.Text(str(i), color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
                             bgcolor=ft.Colors.GREEN if i == 1 else ft.Colors.BLUE_GREY, width=30, height=30,
                             border_radius=15, alignment=ft.alignment.center),
                ft.Column([ft.Text(r["name"], weight=ft.FontWeight.BOLD, size=14),
                           ft.Text(r["store"], size=12, color=ft.Colors.GREY),
                           ft.Text(unit, size=12, color=ft.Colors.BLUE)], expand=True, spacing=2),
                ft.Column([ft.Text(f"{r['price']:.2f}", size=20, weight=ft.FontWeight.BOLD),
                           ft.Text("ر.س", size=11)], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=12, border_radius=12, bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.PRIMARY),
            on_click=(lambda e, u=url: page.launch_url(u)) if url else None)

    def render():
        if not state["query"]:
            return
        rows = svc.results(selected(), state["query"], sort_dd.value)
        results.controls = [card(i, r) for i, r in enumerate(rows, 1)]
        if rows:
            banner.value = f"الأرخص: {rows[0]['store']} — {rows[0]['price']:.2f} ر.س   |   الأغلى: {rows[-1]['price']:.2f}" \
                if sort_dd.value == "price" else f"الأرخص للوحدة: {rows[0]['store']}"
            banner.visible = True
        else:
            banner.visible = False
            results.controls = [ft.Text("لا توجد نتائج. افتح «حالة كل متجر» لمعرفة السبب.")]
        page.update()

    def run_search(force=False):
        q = (query_f.value or "").strip()
        names = selected()
        if not q:
            return toast("اكتب اسم المنتج")
        if not names:
            return toast("اختر متجراً واحداً على الأقل")
        state["query"] = q
        progress.visible, banner.visible = True, False
        results.controls = []
        page.update()

        def work():
            try:
                status = svc.ensure_fresh(names, q, TTL_HOURS, force)
                status_col.controls = [ft.Text(f"{n}: {status.get(n, '—')}", size=12) for n in names]
                status_tile.visible = True
                status_tile.initially_expanded = any(v.startswith("❌") for v in status.values())
                render()
            except Exception as ex:
                toast(f"حدث خطأ: {ex}")
            finally:
                progress.visible = False
                page.update()

        threading.Thread(target=work, daemon=True).start()

    search_tab = ft.Column([
        ft.Row([query_f, search_btn]),
        ft.ExpansionTile(title=ft.Text("المتاجر (الافتراضي: الكل)"), controls=[
            ft.Row([ft.TextButton("الكل", on_click=lambda e: set_all(True)),
                    ft.TextButton("مسح", on_click=lambda e: set_all(False))]),
            ft.Row(list(checks.values()), wrap=True)]),
        demo_warn,
        ft.Row([sort_dd, refresh_btn], wrap=True),
        progress, banner, status_tile, results,
    ], expand=True)

    # ======================= تبويب المتاجر =======================
    word_f = ft.TextField(label="الكلمة التي بحثت بها في موقع المتجر (لتحويل الرابط تلقائياً)", hint_text="مثال: زيت")
    test_f = ft.TextField(label="كلمة الاختبار", value="زيت نور")
    refs = {}
    tiles = ft.Column()

    def build_tiles():
        tiles.controls.clear()
        refs.clear()
        for s in svc.cfg:
            url_f = ft.TextField(label="رابط صفحة نتائج البحث", value=s.get("url", ""), hint_text="https://.../search?q={q}")
            mode_d = ft.Dropdown(label="الوضع", value=s["mode"], options=[ft.dropdown.Option(k, v) for k, v in MODES.items()])
            out = ft.Text("", selectable=True, size=12)
            refs[s["name"]] = (url_f, mode_d)

            def on_test(e, s=s, url_f=url_f, mode_d=mode_d, out=out):
                out.value = "جارٍ الاختبار..."
                page.update()

                def work():
                    info = svc.test(s, url_f.value, mode_d.value, word_f.value.strip(), test_f.value.strip() or "زيت")
                    if info.get("ok"):
                        lines = [f"✅ نجح: {info['count']} منتج في الصفحة، {info['relevant']} مطابق"]
                        lines += [f"• {x['name']} — {x['price']}" for x in info["samples"]]
                        if info.get("template") and info["template"] != url_f.value:
                            url_f.value = info["template"]
                    else:
                        lines = [f"❌ {info.get('error')}"]
                    out.value = "\n".join(lines)
                    page.update()

                threading.Thread(target=work, daemon=True).start()

            tiles.controls.append(ft.ExpansionTile(
                title=ft.Text(s["name"]), subtitle=ft.Text(MODES.get(s["mode"], s["mode"])),
                controls=[ft.Container(ft.Column([url_f, mode_d, ft.OutlinedButton("🧪 اختبار هذا المتجر", on_click=on_test), out]),
                                       padding=10)]))
        page.update()

    def save(e):
        new_cfg = [{**s, "url": refs[s["name"]][0].value.strip(), "mode": refs[s["name"]][1].value} for s in svc.cfg]
        svc.save(new_cfg, word_f.value.strip())
        refresh_demo_warning()
        build_tiles()
        toast("تم الحفظ")

    settings_tab = ft.Column([
        ft.Text("افتح موقع المتجر في متصفح الجوال، ابحث عن أي كلمة (مثل «زيت»)، انسخ رابط صفحة النتائج والصقه هنا. "
                "اكتب الكلمة التي بحثت بها في الخانة الأولى ليتحول الرابط تلقائياً، ثم اضغط «اختبار».", size=12),
        word_f, test_f, tiles,
        ft.ElevatedButton("💾 حفظ الإعدادات", icon=ft.Icons.SAVE, on_click=save),
    ], scroll=ft.ScrollMode.AUTO, expand=True)

    refresh_demo_warning()
    build_tiles()
    page.add(ft.Tabs(expand=True, tabs=[
        ft.Tab(text="🔍 البحث", content=ft.Container(search_tab, padding=ft.padding.only(top=10))),
        ft.Tab(text="⚙️ المتاجر", content=ft.Container(settings_tab, padding=ft.padding.only(top=10))),
    ]))


ft.app(target=main)
