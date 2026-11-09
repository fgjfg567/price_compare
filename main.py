"""مقارنة الأسعار — النسخة 4 (Flet / أندرويد).
صفحات: البحث • السلة • المفضلة • المتاجر والإعدادات."""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import flet as ft

import stores as S
from units import parse_size

TEAL, SKY, GREEN, ORANGE, RED, GREY = "#0F766E", "#0EA5E9", "#16A34A", "#EA580C", "#DC2626", "#6B7280"
POPULAR = ["زيت", "حليب", "أرز", "سكر", "بيض", "قهوة", "شاي", "حفاضات", "مياه", "دجاج"]
VERSION = "4.0"
STATE_ICON = {"ok": ("CHECK_CIRCLE", GREEN), "empty": ("REMOVE_CIRCLE_OUTLINE", GREY), "blocked": ("BLOCK", RED),
              "unsupported": ("ERROR_OUTLINE", ORANGE), "network": ("WIFI_OFF", RED)}
STATE_TEXT = {"ok": "يعمل", "empty": "يعمل (لا نتائج)", "blocked": "محجوب", "unsupported": "غير مدعوم", "network": "لا اتصال"}


def ic(name, fallback="CIRCLE"):
    return getattr(ft.Icons, name, None) or getattr(ft.Icons, fallback, None)


def size_text(name):
    s = parse_size(name)
    if not s:
        return ""
    q, kind = s
    return f"{('%g' % round(q, 3))} {'لتر' if kind == 'L' else 'كجم'}"


def main(page: ft.Page):
    page.title = "مقارنة الأسعار"
    page.padding = 0
    page.spacing = 0
    try:
        page.theme = ft.Theme(color_scheme_seed=TEAL)
    except Exception:
        pass
    prefs = S.load_prefs()
    st = {"gen": 0, "query": "", "items": [], "status": {}, "sort": "price", "avail": True, "busy": False,
          "fav_cur": {}, "tab": 0}

    # ================================================================ أدوات مشتركة
    def apply_theme():
        try:
            page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(prefs.get("theme"), ft.ThemeMode.SYSTEM)
        except Exception:
            pass

    def toast(msg):
        try:
            page.open(ft.SnackBar(ft.Text(msg)))
        except Exception:
            pass

    def surface(o=0.06):
        return ft.Colors.with_opacity(o, ft.Colors.ON_SURFACE)

    def muted():
        return ft.Colors.with_opacity(0.6, ft.Colors.ON_SURFACE)

    def shadow():
        try:
            return ft.BoxShadow(blur_radius=14, color=ft.Colors.with_opacity(0.10, ft.Colors.BLACK), offset=ft.Offset(0, 4))
        except Exception:
            return None

    def strike(value, size=11):
        try:
            return ft.Text(value, size=size, color=muted(), style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH))
        except Exception:
            return ft.Text(value, size=size, color=muted())

    def pill(text, bg, fg="#FFFFFF", size=11):
        return ft.Container(ft.Text(text, size=size, color=fg, weight=ft.FontWeight.BOLD), bgcolor=bg, border_radius=10,
                            padding=ft.padding.symmetric(horizontal=8, vertical=2))

    def toggle_btn(text, on, on_click):
        return ft.Container(ft.Text(text, size=12, color="#FFFFFF" if on else None),
                            bgcolor=TEAL if on else surface(), padding=ft.padding.symmetric(horizontal=12, vertical=7),
                            border_radius=16, ink=True, on_click=on_click)

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

    def header(title, subtitle, extra=None):
        col = [ft.Row([ft.Column([ft.Text(title, size=22, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                                  ft.Text(subtitle, size=12, color="#D1FAE5")],
                                 spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END)],
                      alignment=ft.MainAxisAlignment.END)]
        if extra is not None:
            col.append(extra)
        return ft.Container(
            gradient=ft.LinearGradient(begin=ft.alignment.top_left, end=ft.alignment.bottom_right, colors=[TEAL, SKY]),
            padding=ft.padding.only(left=16, right=16, top=14, bottom=16),
            border_radius=ft.border_radius.only(bottom_left=28, bottom_right=28), content=ft.Column(col, spacing=12))

    def empty_box(icon_name, text, sub=""):
        return ft.Container(ft.Column([
            ft.Icon(ic(icon_name), size=60, color=ft.Colors.with_opacity(0.25, ft.Colors.ON_SURFACE)),
            ft.Text(text, text_align=ft.TextAlign.CENTER, weight=ft.FontWeight.W_600),
            ft.Text(sub, size=12, color=muted(), text_align=ft.TextAlign.CENTER) if sub else ft.Container()],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8), padding=30, alignment=ft.alignment.center)

    def active():
        return [s for s in S.STORES if s["key"] not in prefs["disabled"]]

    def save():
        S.save_prefs(prefs)

    # ---- المفضلة: مفاتيح وتبديل
    def fkey(store_key, name):
        return (store_key, S.normalize(name))

    def is_fav(it):
        return any(fkey(f["store_key"], f["name"]) == fkey(it["store_key"], it["name"]) for f in prefs["favorites"])

    def toggle_fav(it):
        k = fkey(it["store_key"], it["name"])
        if is_fav(it):
            prefs["favorites"] = [f for f in prefs["favorites"] if fkey(f["store_key"], f["name"]) != k]
            toast("أُزيل من المفضلة")
        else:
            prefs["favorites"].append({"store_key": it["store_key"], "name": it["name"], "url": it["url"],
                                       "price": it["price"], "query": st["query"], "ts": datetime.now().strftime("%Y-%m-%d")})
            toast("أُضيف إلى المفضلة")
        save()
        render()
        build_fav()

    def add_basket(it):
        q = st["query"]
        if not parse_size(q):
            sz = size_text(it["name"])
            q = f"{q} {sz}".strip() if sz else q
        if any(e["q"] == q for e in prefs["basket"]):
            return toast("موجود في السلة مسبقاً")
        prefs["basket"].append({"q": q, "qty": 1})
        save()
        build_basket()
        toast(f"أُضيف إلى السلة: {q}")

    # ================================================================ صفحة البحث
    query_f = ft.TextField(hint_text="ابحث عن منتج… مثال: زيت من شركة نور", prefix_icon=ft.Icons.SEARCH,
                           border_radius=30, filled=True, bgcolor="#FFFFFF", color="#111827", border_color="transparent",
                           text_align=ft.TextAlign.RIGHT, content_padding=ft.padding.symmetric(horizontal=16, vertical=14),
                           on_submit=lambda e: start_search(), expand=True)
    go_btn = ft.IconButton(icon=ic("ARROW_BACK_IOS_NEW"), icon_color="#FFFFFF", bgcolor="#0B5F59",
                           on_click=lambda e: start_search())
    chips_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=8)
    chip_slots = {}
    sort_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=6, alignment=ft.MainAxisAlignment.END)
    banner = ft.Container(visible=False, border_radius=16, padding=14)
    progress = ft.ProgressBar(visible=False, color=TEAL)
    results = ft.ListView(expand=True, spacing=10, padding=ft.padding.only(bottom=16))
    recent_row = ft.Row(wrap=True, spacing=6, run_spacing=6, alignment=ft.MainAxisAlignment.END)

    def build_chips():
        chips_row.controls.clear()
        chip_slots.clear()
        for s in active():
            slot = ft.Container(width=18, height=18)
            chip_slots[s["key"]] = slot
            chips_row.controls.append(ft.Container(
                content=ft.Row([slot, ft.Text(s["name"], size=13), badge(s, 26)], spacing=6),
                padding=ft.padding.symmetric(horizontal=10, vertical=6), border_radius=20, bgcolor=surface(0.07), ink=True,
                on_click=lambda e, k=s["key"]: show_note(k)))

    def set_chip(key, res=None):
        slot = chip_slots.get(key)
        if not slot:
            return
        if res is None:
            slot.content = ft.ProgressRing(width=14, height=14, stroke_width=2)
        else:
            name, color = STATE_ICON.get(res["state"], STATE_ICON["empty"])
            slot.content = ft.Icon(ic(name), color=color, size=18)

    def show_note(key):
        res, s = st["status"].get(key), S.BY_KEY[key]
        dlg = ft.AlertDialog(title=ft.Text(s["name"]), content=ft.Text(res["note"] if res else "لم يُبحث في هذا المتجر بعد."),
                             actions=[ft.TextButton("حسناً", on_click=lambda e: page.close(dlg))])
        page.open(dlg)

    def paint_sort():
        opts = [("price", "الأرخص سعراً"), ("unit", "الأرخص للوحدة"), ("discount", "أعلى خصم")]
        sort_row.controls = [toggle_btn(t, st["sort"] == k, lambda e, k=k: set_sort(k)) for k, t in opts] + \
                            [toggle_btn("المتوفر فقط", st["avail"], lambda e: set_avail())]

    def set_sort(k):
        st["sort"] = k
        paint_sort()
        render()

    def set_avail():
        st["avail"] = not st["avail"]
        paint_sort()
        render()

    def visible_items():
        items = [i for i in st["items"] if i["available"] or not st["avail"]]
        if st["sort"] == "unit":
            items.sort(key=lambda r: (r["unit_price"] is None, r["unit_price"] or 0, r["price"]))
        elif st["sort"] == "discount":
            items.sort(key=lambda r: (-r["discount_pct"], r["price"]))
        else:
            items.sort(key=lambda r: r["price"])
        return items

    def summary_text():
        lines = [f"مقارنة أسعار: {st['query']}"]
        for i, it in enumerate(visible_items()[:6], 1):
            lines.append(f"{i}. {it['store']} — {it['price']:.2f} ر.س — {it['name']}")
        return "\n".join(lines)

    def copy_summary(e=None):
        try:
            page.set_clipboard(summary_text())
            toast("تم نسخ المقارنة")
        except Exception:
            toast("تعذّر النسخ")

    def result_card(rank, it, best):
        s = S.BY_KEY[it["store_key"]]
        unit = f"{it['unit_price']:.2f} {it['unit_label']}" if it["unit_price"] is not None else ""
        tags = []
        if best:
            tags.append(pill("الأرخص", GREEN))
        if it["discount_pct"]:
            tags.append(pill(f"-{it['discount_pct']}%", ORANGE))
        if not it["available"]:
            tags.append(pill("غير متوفر", GREY))
        price_col = [ft.Text(f"{it['price']:.2f}", size=22, weight=ft.FontWeight.BOLD, color=GREEN if best else None),
                     ft.Text("ر.س", size=11)]
        if it.get("old_price"):
            price_col.append(strike(f"{it['old_price']:.2f}"))
        if unit:
            price_col.append(ft.Text(unit, size=10, color=SKY))
        fav = is_fav(it)
        info = [ft.Text(it["name"], size=14, weight=ft.FontWeight.W_600, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS,
                        text_align=ft.TextAlign.RIGHT),
                ft.Row(tags + [ft.Text(s["name"], size=12, color=muted())], alignment=ft.MainAxisAlignment.END, spacing=6, wrap=True)]
        if it.get("link") == "search":
            info.append(ft.Text("يفتح نتائج البحث في المتجر", size=10, color=ORANGE))
        actions = ft.Row([
            ft.IconButton(ic("CONTENT_COPY", "SHARE"), icon_size=18, tooltip="نسخ", on_click=lambda e, it=it: copy_item(it)),
            ft.IconButton(ic("ADD_SHOPPING_CART", "ADD"), icon_size=18, tooltip="للسلة", on_click=lambda e, it=it: add_basket(it)),
            ft.IconButton(ic("FAVORITE" if fav else "FAVORITE_BORDER"), icon_size=18, icon_color=RED if fav else None,
                          tooltip="مفضلة", on_click=lambda e, it=it: toggle_fav(it))],
            alignment=ft.MainAxisAlignment.END, spacing=0)
        info.append(actions)
        return ft.Container(
            content=ft.Row([ft.Column(price_col, spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Column(info, expand=True, spacing=4, horizontal_alignment=ft.CrossAxisAlignment.END),
                            badge(s, 44)], vertical_alignment=ft.CrossAxisAlignment.START, spacing=12),
            padding=12, border_radius=16, ink=True, bgcolor=surface(0.05), shadow=shadow(),
            border=ft.border.all(2, GREEN) if best else ft.border.all(1, surface(0.08)),
            opacity=1 if it["available"] else 0.6, on_click=lambda e, it=it: open_details(it))

    def copy_item(it):
        try:
            page.set_clipboard(f"{it['name']} — {it['store']} — {it['price']:.2f} ر.س\n{it['url']}")
            toast("تم النسخ")
        except Exception:
            toast("تعذّر النسخ")

    def open_details(it):
        s = S.BY_KEY[it["store_key"]]
        rows = [ft.Text(it["name"], size=15, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.RIGHT),
                ft.Row([ft.Text(f"{it['price']:.2f} ر.س", size=22, weight=ft.FontWeight.BOLD, color=GREEN)] +
                       ([strike(f"{it['old_price']:.2f}", 13), pill(f"-{it['discount_pct']}%", ORANGE)] if it.get("old_price") else []),
                       alignment=ft.MainAxisAlignment.END, spacing=8)]
        if it["unit_price"] is not None:
            rows.append(ft.Text(f"السعر للوحدة: {it['unit_price']:.2f} {it['unit_label']}", size=13, color=SKY))
        rows.append(ft.Text("متوفر" if it["available"] else "غير متوفر حالياً", size=13, color=GREEN if it["available"] else GREY))
        if it.get("link") == "search":
            rows.append(ft.Text("تعذّر الوصول لصفحة المنتج، سيفتح الرابط نتائج البحث في المتجر.", size=11, color=ORANGE))
        dlg = ft.AlertDialog(
            title=ft.Row([ft.Text(s["name"]), badge(s, 36)], alignment=ft.MainAxisAlignment.END),
            content=ft.Container(ft.Column(rows, tight=True, spacing=8, horizontal_alignment=ft.CrossAxisAlignment.END), width=320),
            actions=[ft.TextButton("فتح في المتجر", on_click=lambda e: page.launch_url(it["url"])),
                     ft.TextButton("للسلة", on_click=lambda e: add_basket(it)),
                     ft.TextButton("مفضلة", on_click=lambda e: toggle_fav(it)),
                     ft.TextButton("إغلاق", on_click=lambda e: page.close(dlg))])
        page.open(dlg)

    def render():
        items = visible_items()
        if not items:
            results.controls = [] if st["busy"] else [empty_box("SEARCH_OFF", "لا توجد نتائج مطابقة",
                                                                  "اضغط على اسم أي متجر بالأعلى لمعرفة السبب. جرّب كلمة أقصر أو ألغِ «المتوفر فقط».")]
            banner.visible = False
        else:
            prices = [i["price"] for i in items]
            cheap = min((i for i in items if i["available"]), key=lambda r: r["price"], default=items[0])
            banner.visible = True
            banner.gradient = ft.LinearGradient(colors=[GREEN, "#22C55E"])
            banner.content = ft.Row([
                ft.IconButton(ic("CONTENT_COPY", "SHARE"), icon_color="#FFFFFF", tooltip="نسخ المقارنة", on_click=copy_summary),
                ft.Column([ft.Text(f"الأرخص: {cheap['store']} — {cheap['price']:.2f} ر.س", color="#FFFFFF",
                                   weight=ft.FontWeight.BOLD, size=15),
                           ft.Text(f"توفير يصل إلى {max(prices) - min(prices):.2f} ر.س مقارنة بالأغلى • {len(items)} نتيجة",
                                   color="#DCFCE7", size=12)], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END, expand=True)])
            top = cheap
            results.controls = [result_card(i, it, it is top) for i, it in enumerate(items, 1)]
        page.update()

    def home_state():
        recent_row.controls = [toggle_btn(q, False, lambda e, q=q: run_query(q)) for q in (prefs["recent"][:8] or POPULAR[:6])]
        results.controls = [ft.Container(ft.Column([
            ft.Icon(ic("SHOPPING_BAG_OUTLINED"), size=64, color=ft.Colors.with_opacity(0.25, ft.Colors.ON_SURFACE)),
            ft.Text("ابحث عن أي منتج لمقارنة سعره في كل المتاجر", text_align=ft.TextAlign.CENTER, weight=ft.FontWeight.W_600),
            ft.Text("عمليات البحث الأخيرة" if prefs["recent"] else "اقتراحات سريعة", size=12, color=muted()), recent_row],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=10), padding=24, alignment=ft.alignment.center)]

    def run_query(q):
        query_f.value = q
        start_search()

    def start_search(force=None):
        q = (query_f.value or "").strip()
        act = active()
        if not q:
            return toast("اكتب اسم المنتج أولاً")
        if not act:
            return toast("فعّل متجراً واحداً على الأقل من صفحة المتاجر")
        go(0)
        st["gen"] += 1
        gen = st["gen"]
        st.update(query=q, items=[], status={}, busy=True)
        prefs["recent"] = ([q] + [x for x in prefs["recent"] if x != q])[:8]
        build_chips()
        for s in act:
            set_chip(s["key"])
        progress.visible, banner.visible = True, False
        results.controls = []
        page.update()

        def work():
            try:
                with ThreadPoolExecutor(max_workers=len(act)) as ex:
                    futs = {ex.submit(S.search_store, s, q, prefs): s for s in act}
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
                    save()
                    render()

        threading.Thread(target=work, daemon=True).start()

    search_view = ft.Column(expand=True, spacing=0, controls=[
        header("مقارنة الأسعار", "ابحث مرة واحدة… وقارن كل المتاجر",
               ft.Row([go_btn, query_f], spacing=8)),
        ft.Container(expand=True, padding=ft.padding.only(left=14, right=14, top=12), content=ft.Column(
            expand=True, spacing=8, controls=[chips_row, sort_row, progress, banner, results]))])

    # ================================================================ صفحة السلة
    basket_in = ft.TextField(hint_text="أضف منتجاً… مثال: زيت نور 1.5 لتر", border_radius=24, filled=True,
                             text_align=ft.TextAlign.RIGHT, expand=True, on_submit=lambda e: basket_add_text(),
                             content_padding=ft.padding.symmetric(horizontal=16, vertical=12))
    basket_list = ft.Column(spacing=8)
    basket_result = ft.Column(spacing=10)
    basket_prog = ft.Text("", size=12, color=muted())
    basket_bar = ft.ProgressBar(visible=False, color=TEAL)

    def basket_add_text():
        q = (basket_in.value or "").strip()
        if not q:
            return
        if not any(e["q"] == q for e in prefs["basket"]):
            prefs["basket"].append({"q": q, "qty": 1})
            save()
        basket_in.value = ""
        build_basket()

    def basket_qty(e, d):
        e["qty"] = max(1, min(99, e["qty"] + d))
        save()
        build_basket()

    def basket_del(e):
        prefs["basket"] = [x for x in prefs["basket"] if x is not e]
        save()
        build_basket()

    def build_basket():
        rows = []
        for e in prefs["basket"]:
            rows.append(ft.Container(ft.Row([
                ft.IconButton(ic("DELETE_OUTLINE", "CLOSE"), icon_color=RED, icon_size=20, on_click=lambda ev, e=e: basket_del(e)),
                ft.IconButton(ic("REMOVE", "CLOSE"), icon_size=18, on_click=lambda ev, e=e: basket_qty(e, -1)),
                ft.Text(str(e["qty"]), weight=ft.FontWeight.BOLD),
                ft.IconButton(ic("ADD"), icon_size=18, on_click=lambda ev, e=e: basket_qty(e, 1)),
                ft.Text(e["q"], expand=True, text_align=ft.TextAlign.RIGHT, weight=ft.FontWeight.W_600)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                padding=ft.padding.symmetric(horizontal=10, vertical=4), border_radius=14, bgcolor=surface(0.05)))
        basket_list.controls = rows or [empty_box("SHOPPING_CART_OUTLINED", "السلة فارغة",
                                                  "أضف منتجاتك هنا (أو من نتائج البحث) ثم اضغط «قارن السلة» لتعرف أرخص متجر لكل السلة.")]
        page.update()

    def basket_compare(e=None):
        if not prefs["basket"]:
            return toast("أضف منتجاً واحداً على الأقل")
        act = active()
        if not act:
            return toast("فعّل متجراً واحداً على الأقل")
        basket_bar.visible = True
        basket_result.controls = []
        page.update()

        def work():
            try:
                def prog(i, n):
                    basket_prog.value = f"جارٍ البحث… {i}/{n}"
                    page.update()
                res = S.run_basket([dict(x) for x in prefs["basket"]], act, prefs, prog)
                st["basket_res"] = res
                render_basket(res)
            except Exception as ex:
                basket_result.controls = [ft.Text(f"تعذّرت المقارنة: {ex}", color=RED)]
            finally:
                basket_bar.visible = False
                basket_prog.value = ""
                save()
                page.update()

        threading.Thread(target=work, daemon=True).start()

    def basket_summary(res):
        lines = ["مقارنة السلة:"]
        if res["best"]:
            lines.append(f"أرخص متجر لكل السلة: {res['best']['name']} — {res['best']['total']:.2f} ر.س")
        if res["mixed_complete"]:
            lines.append(f"موزعة على أرخص المتاجر: {res['mixed_total']:.2f} ر.س")
        for l in res["lines"]:
            if l["best"]:
                lines.append(f"- {l['q']} ×{l['qty']}: {l['best']['store']} {l['best']['price']:.2f}")
        return "\n".join(lines)

    def render_basket(res):
        out = []
        by_key = {s["key"]: s for s in S.STORES}
        if res["best"]:
            saving = (res["worst"]["total"] - res["best"]["total"]) if res["worst"] and res["worst"] is not res["best"] else 0
            out.append(ft.Container(ft.Column([
                ft.Text("أرخص متجر لكل السلة", color="#DCFCE7", size=12),
                ft.Text(f"{res['best']['name']} — {res['best']['total']:.2f} ر.س", color="#FFFFFF", size=18, weight=ft.FontWeight.BOLD),
                ft.Text(f"توفّر {saving:.2f} ر.س مقارنة بأغلى متجر" if saving else "", color="#DCFCE7", size=12)],
                spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END),
                gradient=ft.LinearGradient(colors=[GREEN, "#22C55E"]), border_radius=16, padding=14))
        else:
            out.append(ft.Container(ft.Text("لا يوجد متجر واحد يوفّر كل منتجات السلة. راجع التوزيع أدناه.", color=ORANGE),
                                    padding=10, border_radius=12, bgcolor=surface(0.05)))
        if res["mixed_complete"]:
            extra = res["mixed_total"] - res["best"]["total"] if res["best"] else 0
            out.append(ft.Container(ft.Column([
                ft.Text("لو وزّعت الشراء على عدة متاجر", size=12, color=muted()),
                ft.Text(f"{res['mixed_total']:.2f} ر.س" + (f"  (أوفر بـ {-extra:.2f})" if extra < -0.005 else ""),
                        size=16, weight=ft.FontWeight.BOLD)], spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END),
                border_radius=16, padding=14, bgcolor=surface(0.06)))
        out.append(ft.Text("إجمالي كل متجر", weight=ft.FontWeight.BOLD))
        for t in res["totals"]:
            s = by_key[t["key"]]
            out.append(ft.Container(ft.Row([
                ft.Text(f"{t['total']:.2f} ر.س" if t["found"] else "—", weight=ft.FontWeight.BOLD, color=GREEN if t is res["best"] else None),
                ft.Text("كامل" if t["complete"] else f"ناقص ({t['found']}/{res['n']})", size=12, color=GREEN if t["complete"] else ORANGE, expand=True),
                ft.Text(s["name"], weight=ft.FontWeight.W_600), badge(s, 30)], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10),
                padding=10, border_radius=14, bgcolor=surface(0.05),
                border=ft.border.all(2, GREEN) if t is res["best"] else None))
        out.append(ft.Text("أرخص سعر لكل منتج", weight=ft.FontWeight.BOLD))
        for l in res["lines"]:
            if l["best"]:
                s = by_key[l["best"]["store_key"]]
                out.append(ft.Container(ft.Row([
                    ft.Text(f"{l['best']['price'] * l['qty']:.2f}", weight=ft.FontWeight.BOLD),
                    ft.Column([ft.Text(f"{l['q']} ×{l['qty']}", size=13, text_align=ft.TextAlign.RIGHT),
                               ft.Text(l["best"]["name"], size=11, color=muted(), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)],
                              expand=True, spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END), badge(s, 30)],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10), padding=10, border_radius=14, bgcolor=surface(0.05),
                    on_click=lambda e, it=l["best"]: open_details(it)))
            else:
                out.append(ft.Container(ft.Text(f"{l['q']}: غير متوفر في أي متجر", color=ORANGE, size=13), padding=10,
                                        border_radius=14, bgcolor=surface(0.05)))
        out.append(ft.OutlinedButton("نسخ ملخص السلة", icon=ic("CONTENT_COPY", "SHARE"),
                                     on_click=lambda e: (page.set_clipboard(basket_summary(res)), toast("تم النسخ"))))
        basket_result.controls = out
        page.update()

    basket_view = ft.Column(expand=True, spacing=0, visible=False, controls=[
        header("سلة المقارنة", "اعرف أرخص متجر لكل مشترياتك"),
        ft.Container(expand=True, padding=ft.padding.only(left=14, right=14, top=12), content=ft.Column(
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=10, controls=[
                ft.Row([ft.IconButton(ic("ADD"), icon_color="#FFFFFF", bgcolor=TEAL, on_click=lambda e: basket_add_text()), basket_in]),
                basket_list,
                ft.ElevatedButton("قارن السلة", icon=ic("COMPARE_ARROWS", "SEARCH"), bgcolor=TEAL, color="#FFFFFF", on_click=basket_compare),
                basket_bar, basket_prog, basket_result]))])

    # ================================================================ صفحة المفضلة
    fav_list = ft.Column(spacing=8)
    fav_bar = ft.ProgressBar(visible=False, color=TEAL)

    def fav_del(f):
        prefs["favorites"] = [x for x in prefs["favorites"] if x is not f]
        save()
        build_fav()
        render()

    def build_fav():
        rows = []
        for f in prefs["favorites"]:
            s = S.BY_KEY.get(f["store_key"])
            if not s:
                continue
            cur = st["fav_cur"].get(fkey(f["store_key"], f["name"]))
            price_line = [ft.Text(f"محفوظ: {f['price']:.2f}", size=12, color=muted())]
            if cur == "none":
                price_line.append(ft.Text("لم أجد المنتج الآن", size=12, color=ORANGE))
            elif cur:
                d = cur["price"] - f["price"]
                color = GREEN if d < -0.005 else RED if d > 0.005 else GREY
                arrow = ("▼ " if d < -0.005 else "▲ " if d > 0.005 else "= ")
                price_line.append(ft.Text(f"الآن: {cur['price']:.2f}  {arrow}{abs(d):.2f}", size=13, weight=ft.FontWeight.BOLD, color=color))
            rows.append(ft.Container(ft.Row([
                ft.Column([ft.IconButton(ic("DELETE_OUTLINE", "CLOSE"), icon_color=RED, icon_size=20, on_click=lambda e, f=f: fav_del(f)),
                           ft.IconButton(ic("OPEN_IN_NEW", "ADD"), icon_size=18, on_click=lambda e, u=(cur["url"] if isinstance(cur, dict) else f["url"]): page.launch_url(u))],
                          spacing=0),
                ft.Column([ft.Text(f["name"], size=14, weight=ft.FontWeight.W_600, text_align=ft.TextAlign.RIGHT, max_lines=2,
                                   overflow=ft.TextOverflow.ELLIPSIS)] + price_line,
                          expand=True, spacing=2, horizontal_alignment=ft.CrossAxisAlignment.END), badge(s, 42)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=10), padding=12, border_radius=16, bgcolor=surface(0.05), shadow=shadow()))
        fav_list.controls = rows or [empty_box("FAVORITE_BORDER", "لا توجد منتجات مفضلة",
                                               "اضغط ♡ على أي منتج في نتائج البحث لتتابع تغيّر سعره هنا.")]
        page.update()

    def fav_refresh(e=None):
        if not prefs["favorites"]:
            return toast("لا توجد مفضلة لتحديثها")
        fav_bar.visible = True
        page.update()

        def one(f):
            try:
                cur, _ = S.refresh_favorite(f, prefs)
            except Exception:
                cur = None
            return fkey(f["store_key"], f["name"]), (cur or "none")

        def work():
            try:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    for k, v in ex.map(one, list(prefs["favorites"])):
                        st["fav_cur"][k] = v
                build_fav()
            finally:
                fav_bar.visible = False
                save()
                page.update()

        threading.Thread(target=work, daemon=True).start()

    fav_view = ft.Column(expand=True, spacing=0, visible=False, controls=[
        header("المفضلة", "تابع تغيّر أسعار منتجاتك"),
        ft.Container(expand=True, padding=ft.padding.only(left=14, right=14, top=12), content=ft.Column(
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=10, controls=[
                ft.ElevatedButton("تحديث الأسعار الآن", icon=ic("REFRESH", "SEARCH"), bgcolor=TEAL, color="#FFFFFF", on_click=fav_refresh),
                fav_bar, fav_list]))])

    # ================================================================ صفحة المتاجر والإعدادات
    store_tiles = ft.Column(spacing=6)
    theme_row = ft.Row(spacing=6, alignment=ft.MainAxisAlignment.END)
    health_bar = ft.ProgressBar(visible=False, color=TEAL)

    def set_theme(m):
        prefs["theme"] = m
        save()
        apply_theme()
        build_settings()

    def set_enabled(key, on):
        dis = set(prefs["disabled"])
        (dis.discard if on else dis.add)(key)
        prefs["disabled"] = sorted(dis)
        save()
        build_chips()
        page.update()

    def set_override(key, value):
        v = (value or "").strip()
        if v:
            prefs["overrides"][key] = v
        else:
            prefs["overrides"].pop(key, None)
        prefs["discovered"].pop(key, None)
        save()

    def build_settings():
        theme_row.controls = [toggle_btn(t, prefs.get("theme", "system") == k, lambda e, k=k: set_theme(k))
                              for k, t in [("dark", "داكن"), ("light", "فاتح"), ("system", "تلقائي")]]
        tiles = []
        for s in S.STORES:
            h = prefs["health"].get(s["key"])
            sub = "لم يُفحص بعد" if not h else f"{STATE_TEXT.get(h['state'], h['state'])} • {h['ts']}"
            uf = ft.TextField(label="رابط بحث مخصص (اختياري — اتركه فارغاً)", hint_text="https://.../search?q={q}", text_size=12,
                              value=prefs["overrides"].get(s["key"], ""),
                              on_change=lambda e, k=s["key"]: set_override(k, e.control.value))
            icon_name, color = STATE_ICON.get(h["state"], ("HELP_OUTLINE", GREY)) if h else ("HELP_OUTLINE", GREY)
            tiles.append(ft.ExpansionTile(
                leading=badge(s, 34), title=ft.Text(s["name"], weight=ft.FontWeight.W_600),
                subtitle=ft.Text(sub, size=12, color=color),
                trailing=ft.Switch(value=s["key"] not in prefs["disabled"], on_change=lambda e, k=s["key"]: set_enabled(k, e.control.value)),
                controls=[ft.Container(ft.Column([ft.Text(h["note"] if h else "اضغط «فحص كل المتاجر» لاختبار هذا المتجر.", size=12, color=muted()), uf]), padding=10)]))
        store_tiles.controls = tiles
        page.update()

    def health_all(e=None):
        act = active()
        if not act:
            return toast("لا توجد متاجر مفعّلة")
        health_bar.visible = True
        page.update()

        def one(s):
            try:
                S.health_check(s, prefs)
            except Exception as ex:
                prefs["health"][s["key"]] = {"state": "unsupported", "count": 0, "note": f"خطأ: {ex}", "ts": datetime.now().strftime("%H:%M")}

        def work():
            try:
                with ThreadPoolExecutor(max_workers=len(act)) as ex:
                    for f in [ex.submit(one, s) for s in act]:
                        f.result()
                        build_settings()
            finally:
                health_bar.visible = False
                save()
                build_settings()

        threading.Thread(target=work, daemon=True).start()

    def clear_recent(e=None):
        prefs["recent"] = []
        save()
        home_state()
        page.update()
        toast("تم مسح سجل البحث")

    settings_view = ft.Column(expand=True, spacing=0, visible=False, controls=[
        header("المتاجر والإعدادات", "فعّل المتاجر وافحص عملها"),
        ft.Container(expand=True, padding=ft.padding.only(left=14, right=14, top=12), content=ft.Column(
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=10, controls=[
                ft.ElevatedButton("فحص كل المتاجر الآن", icon=ic("HEALTH_AND_SAFETY", "SEARCH"), bgcolor=TEAL, color="#FFFFFF", on_click=health_all),
                ft.Text("الفحص يبحث عن «زيت» في كل متجر ويعرض هل يعمل أم يحجب الطلبات.", size=12, color=muted(), text_align=ft.TextAlign.RIGHT),
                health_bar, store_tiles,
                ft.Text("المظهر", weight=ft.FontWeight.BOLD), theme_row,
                ft.OutlinedButton("مسح سجل البحث", icon=ic("DELETE_OUTLINE", "CLOSE"), on_click=clear_recent),
                ft.Text(f"الإصدار {VERSION} • الأسعار تُجلب مباشرة من مواقع المتاجر ولا تُحفظ، وقد تختلف حسب موقعك داخل المتجر.",
                        size=11, color=muted(), text_align=ft.TextAlign.CENTER)]))])

    # ================================================================ التنقل
    views = [search_view, basket_view, fav_view, settings_view]
    nav_row = ft.Row(alignment=ft.MainAxisAlignment.SPACE_AROUND)

    def paint_nav():
        items = [("SEARCH", "بحث"), ("SHOPPING_CART_OUTLINED", "السلة"), ("FAVORITE_BORDER", "المفضلة"), ("STOREFRONT", "المتاجر")]
        nav_row.controls = [ft.Container(ft.Column([
            ft.Icon(ic(icon_name, "CIRCLE"), size=24, color=TEAL if st["tab"] == i else muted()),
            ft.Text(label, size=11, color=TEAL if st["tab"] == i else muted(),
                    weight=ft.FontWeight.BOLD if st["tab"] == i else None)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
            padding=ft.padding.symmetric(horizontal=14, vertical=6), border_radius=14, ink=True,
            bgcolor=ft.Colors.with_opacity(0.10, TEAL) if st["tab"] == i else None, on_click=lambda e, i=i: go(i))
            for i, (icon_name, label) in enumerate(items)]

    def go(i):
        st["tab"] = i
        for j, v in enumerate(views):
            v.visible = (j == i)
        paint_nav()
        page.update()

    # ================================================================ التركيب
    apply_theme()
    build_chips()
    paint_sort()
    home_state()
    build_basket()
    build_fav()
    build_settings()
    paint_nav()
    page.add(ft.SafeArea(expand=True, content=ft.Column(expand=True, spacing=0, controls=[
        ft.Column(views, expand=True, spacing=0),
        ft.Container(nav_row, padding=ft.padding.only(top=6, bottom=6), shadow=shadow(), bgcolor=surface(0.04))])))


ft.app(target=main)
