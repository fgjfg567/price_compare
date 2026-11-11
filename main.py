"""مساعد التسوق من أمازون — Flet / أندرويد.
البحث • العروض • الكوبونات • المزيد. المحتوى (عروض/كوبونات/مؤقت/عمولة) يُدار من لوحة التحكم عن بُعد."""
import threading
import time

import flet as ft

import amazon
import app_config
import remote

# ألوان تشبه أمازون
NAVY, NAVY2, ORANGE, ORANGE_L, YELLOW = "#131921", "#232F3E", "#FF9900", "#FEBD69", "#FFD814"
BG, CARD, TEXT, MUTED, LINK = "#EAEDED", "#FFFFFF", "#0F1111", "#565959", "#007185"
PRICE, RED, GREEN, PRIME, BORDER = "#0F1111", "#CC0C39", "#007600", "#00A8E1", "#D5D9D9"
_AR = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")


def ic(name, fallback="CIRCLE"):
    return getattr(ft.Icons, name, None) or getattr(ft.Icons, fallback, None)


def fmt_remaining(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def to_num(s):
    try:
        return float(str(s or "").translate(_AR).replace(",", ".").strip())
    except ValueError:
        return None


def main(page: ft.Page):
    page.title = app_config.APP_NAME
    page.padding = 0
    page.spacing = 0
    page.bgcolor = BG
    try:
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(color_scheme_seed=ORANGE)
    except Exception:
        pass

    st = {"query": "", "sort": "price_desc", "prime": False, "min": None, "max": None, "page": 1, "has_more": False,
          "items": [], "busy": False, "gen": 0, "error": None, "tab": 0}
    rem = {"data": remote.normalize({}), "status": "none"}

    # ================================================================ أدوات
    def cfg(key, default=""):
        return rem["data"]["config"].get(key, default)

    def tag():
        return str(cfg("affiliate_tag", "") or app_config.DEFAULT_AFFILIATE_TAG or "").strip()

    def show_prices():
        return cfg("show_prices", True) is not False

    def toast(msg):
        try:
            page.open(ft.SnackBar(ft.Text(msg)))
        except Exception:
            pass

    def open_url(u):
        if u:
            page.launch_url(u)

    def shadow():
        try:
            return ft.BoxShadow(blur_radius=8, color=ft.Colors.with_opacity(0.10, ft.Colors.BLACK), offset=ft.Offset(0, 2))
        except Exception:
            return None

    def btn(text, on_click, bg=YELLOW, fg=TEXT, icon=None, expand=False):
        row = [ft.Text(text, size=14, weight=ft.FontWeight.W_600, color=fg)]
        if icon:
            row.append(ft.Icon(ic(icon), size=18, color=fg))
        return ft.Container(ft.Row(row, alignment=ft.MainAxisAlignment.CENTER, spacing=6), bgcolor=bg, border_radius=20,
                            padding=ft.padding.symmetric(horizontal=16, vertical=10), ink=True, on_click=on_click,
                            expand=expand, alignment=ft.alignment.center)

    def pill(text, bg, fg="#FFFFFF"):
        return ft.Container(ft.Text(text, size=11, color=fg, weight=ft.FontWeight.BOLD), bgcolor=bg, border_radius=4,
                            padding=ft.padding.symmetric(horizontal=6, vertical=2))

    def chip(text, on, on_click):
        return ft.Container(ft.Text(text, size=12, color=TEXT, weight=ft.FontWeight.W_600 if on else None),
                            bgcolor="#FFF3D6" if on else CARD, border=ft.border.all(1.5 if on else 1, ORANGE if on else BORDER),
                            border_radius=18, padding=ft.padding.symmetric(horizontal=12, vertical=6), ink=True, on_click=on_click)

    def header(title, sub, extra=None):
        col = [ft.Row([ft.Column([ft.Text(title, size=20, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                                  ft.Text(sub, size=12, color=ORANGE_L)], spacing=0,
                                 horizontal_alignment=ft.CrossAxisAlignment.END)], alignment=ft.MainAxisAlignment.END)]
        if extra is not None:
            col.append(extra)
        return ft.Container(ft.Column(col, spacing=10), bgcolor=NAVY, padding=ft.padding.only(left=14, right=14, top=12, bottom=12),
                            border=ft.border.only(bottom=ft.BorderSide(3, ORANGE)))

    def empty_box(icon_name, text, sub=""):
        return ft.Container(ft.Column([
            ft.Icon(ic(icon_name), size=56, color="#B0B7BD"),
            ft.Text(text, size=16, weight=ft.FontWeight.W_600, text_align=ft.TextAlign.CENTER, color=TEXT),
            ft.Text(sub, size=12, color=MUTED, text_align=ft.TextAlign.CENTER) if sub else ft.Container()],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8), padding=28, alignment=ft.alignment.center)

    def disclosure():
        return ft.Text(cfg("disclosure", "") or app_config.DISCLOSURE, size=10, color=MUTED, text_align=ft.TextAlign.CENTER)

    # ================================================================ صفحة البحث
    query_f = ft.TextField(hint_text="ابحث في أمازون… (جوال، سماعات، عطر)", border_radius=8, filled=True, bgcolor="#FFFFFF",
                           color=TEXT, border_color="transparent", text_align=ft.TextAlign.RIGHT, expand=True,
                           content_padding=ft.padding.symmetric(horizontal=14, vertical=12), on_submit=lambda e: new_search())
    search_btn = ft.Container(ft.Icon(ic("SEARCH"), color=NAVY), bgcolor=ORANGE, border_radius=8, width=48, height=48,
                              alignment=ft.alignment.center, ink=True, on_click=lambda e: new_search())
    announce = ft.Container(visible=False, bgcolor="#FFF3D6", border=ft.border.all(1, ORANGE), border_radius=10, padding=10)
    sort_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=6, alignment=ft.MainAxisAlignment.END)
    min_f = ft.TextField(label="أقل سعر", width=110, text_size=13, on_change=lambda e: set_range())
    max_f = ft.TextField(label="أعلى سعر", width=110, text_size=13, on_change=lambda e: set_range())
    progress = ft.ProgressBar(visible=False, color=ORANGE)
    status = ft.Container(visible=False, border_radius=10, padding=12)
    results = ft.ListView(expand=True, spacing=10, padding=ft.padding.only(bottom=16))

    def paint_sort():
        opts = [(k, t) for k, t, _ in amazon.SORTS]
        row = [chip(t, st["sort"] == k, lambda e, k=k: set_sort(k)) for k, t in opts] if show_prices() else []
        row.append(chip("برايم فقط", st["prime"], lambda e: toggle_prime()))
        sort_row.controls = row

    def set_sort(k):
        st["sort"] = k
        paint_sort()
        if st["query"] and k != "discount":
            new_search(st["query"])            # إعادة الجلب بترتيب أمازون نفسه
        else:
            render()

    def toggle_prime():
        st["prime"] = not st["prime"]
        paint_sort()
        render()

    def set_range():
        st["min"], st["max"] = to_num(min_f.value), to_num(max_f.value)
        render()

    def visible_items():
        items = list(st["items"])
        if st["prime"]:
            items = [i for i in items if i["prime"]]
        lo, hi = st["min"], st["max"]
        if lo is not None or hi is not None:       # المنتجات بلا سعر لا تُستبعد
            items = [i for i in items if i["price"] is None or ((lo is None or i["price"] >= lo) and (hi is None or i["price"] <= hi))]
        if not show_prices():
            return items
        k = st["sort"]
        priced = [i for i in items if i["price"] is not None]
        unpriced = [i for i in items if i["price"] is None]
        if k == "price_desc":
            priced.sort(key=lambda i: -i["price"])
        elif k == "price_asc":
            priced.sort(key=lambda i: i["price"])
        elif k == "rating":
            priced.sort(key=lambda i: (-(i["rating"] or 0), -(i["reviews"] or 0)))
            unpriced.sort(key=lambda i: (-(i["rating"] or 0)))
        elif k == "discount":
            priced.sort(key=lambda i: (-i["discount_pct"], i["price"]))
        return priced + unpriced

    def stars(it):
        if not it["rating"]:
            return ft.Container()
        n = f" ({it['reviews']:,})" if it.get("reviews") else ""
        return ft.Text(f"★ {it['rating']:.1f}{n}", size=12, color="#B8860B")

    def card(it):
        tg = tag()
        has_price = show_prices() and it["price"] is not None
        if has_price:
            price_part = [ft.Row([ft.Text(f"{it['price']:,.2f}", size=22, weight=ft.FontWeight.BOLD, color=PRICE),
                                  ft.Text("ر.س", size=12, color=PRICE)], spacing=4, alignment=ft.MainAxisAlignment.END)]
            if it["old_price"]:
                price_part.append(ft.Row([ft.Text(f"{it['old_price']:,.2f}", size=12, color=MUTED,
                                                  style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH)),
                                          pill(f"-{it['discount_pct']}%", RED)], spacing=6, alignment=ft.MainAxisAlignment.END))
        else:
            price_part = [ft.Text("السعر متاح على أمازون" if show_prices() else "شاهد السعر على أمازون", size=13, color=MUTED)]
        tags = []
        if it["prime"]:
            tags.append(pill("prime", PRIME))
        if it["sponsored"]:
            tags.append(ft.Text("ممول", size=10, color=MUTED))
        info = [ft.Text(it["name"], size=14, color=TEXT, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.RIGHT),
                stars(it)] + price_part
        if tags:
            info.append(ft.Row(tags, alignment=ft.MainAxisAlignment.END, spacing=6))
        link = amazon.product_url(it["asin"], tg)
        img = ft.Container(ft.Image(src=it["image"], width=96, height=96, fit=ft.ImageFit.CONTAIN,
                                    error_content=ft.Icon(ic("IMAGE_NOT_SUPPORTED", "IMAGE"), color="#B0B7BD")) if it["image"]
                           else ft.Icon(ic("IMAGE_NOT_SUPPORTED", "IMAGE"), color="#B0B7BD"),
                           width=96, height=96, alignment=ft.alignment.center)
        return ft.Container(ft.Column([
            ft.Row([ft.Column(info, expand=True, spacing=4, horizontal_alignment=ft.CrossAxisAlignment.END), img],
                   vertical_alignment=ft.CrossAxisAlignment.START, spacing=12),
            ft.Row([btn("عرض على أمازون", lambda e, u=link: open_url(u), YELLOW, TEXT, "OPEN_IN_NEW", expand=True)])], spacing=10),
            bgcolor=CARD, border_radius=10, padding=12, border=ft.border.all(1, BORDER), shadow=shadow())

    def show_status(msg, kind="warn", search_link=True):
        col = {"warn": "#FFF3D6", "err": "#FDE8E8"}[kind]
        rows = [ft.Text(msg, size=13, color=TEXT, text_align=ft.TextAlign.RIGHT)]
        if search_link and st["query"]:
            rows.append(btn("افتح البحث في أمازون", lambda e: open_url(amazon.search_page_url(st["query"], tag())), YELLOW, TEXT, "OPEN_IN_NEW"))
        status.content = ft.Column(rows, spacing=8, horizontal_alignment=ft.CrossAxisAlignment.END)
        status.bgcolor, status.visible = col, True

    def render():
        items = visible_items()
        cards = [card(i) for i in items]
        if items and st["has_more"] and not st["busy"]:
            cards.append(btn("عرض المزيد من النتائج", lambda e: more(), CARD, LINK, "ADD"))
        if not items and not st["busy"] and st["query"] and not status.visible:
            cards = [empty_box("SEARCH_OFF", "لا توجد نتائج", "جرّب كلمة أخرى أو ألغِ الفلاتر.")]
        if not st["query"]:
            cards = [empty_box("SHOPPING_BAG_OUTLINED", "ابحث عن أي منتج في أمازون",
                               "ستظهر النتائج بالترتيب الذي تختاره، ومنها المنتجات التي لا يتوفر سعرها حالياً.")]
        results.controls = cards
        page.update()

    def fetch_page(gen, pg):
        try:
            res = amazon.search(st["query"], pg, next((s for k, _, s in amazon.SORTS if k == st["sort"]), ""))
            if gen != st["gen"]:
                return
            known = {i["asin"] for i in st["items"]}
            st["items"] += [i for i in res["items"] if i["asin"] not in known]
            st["has_more"], st["page"] = res["has_more"], pg
            st["error"] = None
        except amazon.AmazonError as e:
            if gen == st["gen"]:
                show_status(str(e) + (" يمكنك متابعة التسوق مباشرة عبر أمازون." if e.code != "network" else ""), "err", e.code != "network")
        except Exception as e:
            if gen == st["gen"]:
                show_status(f"حدث خطأ غير متوقع: {e}", "err")
        finally:
            if gen == st["gen"]:
                st["busy"] = False
                progress.visible = False
                render()

    def new_search(q=None):
        q = (q if isinstance(q, str) else query_f.value or "").strip()
        if not q:
            return toast("اكتب اسم المنتج أولاً")
        go(0)
        st.update(query=q, items=[], page=1, has_more=False, busy=True, gen=st["gen"] + 1)
        status.visible = False
        progress.visible = True
        results.controls = []
        page.update()
        threading.Thread(target=fetch_page, args=(st["gen"], 1), daemon=True).start()

    def more():
        if st["busy"]:
            return
        st["busy"] = True
        progress.visible = True
        page.update()
        threading.Thread(target=fetch_page, args=(st["gen"], st["page"] + 1), daemon=True).start()

    search_view = ft.Column(expand=True, spacing=0, controls=[
        header(app_config.APP_NAME, "تسوّق من أمازون بذكاء", ft.Row([search_btn, query_f], spacing=8)),
        ft.Container(expand=True, padding=ft.padding.only(left=12, right=12, top=10), content=ft.Column(
            expand=True, spacing=8, controls=[announce, sort_row,
                                              ft.ExpansionTile(title=ft.Text("تصفية السعر", size=13), controls=[
                                                  ft.Container(ft.Row([max_f, min_f], alignment=ft.MainAxisAlignment.END), padding=8)]),
                                              progress, status, results, disclosure()]))])

    # ================================================================ صفحة العروض
    offers_list = ft.Column(spacing=10)

    def build_offers():
        rows = []
        for o in rem["data"]["offers"]:
            link = amazon.affiliate(o["url"], tag())
            rows.append(ft.Container(ft.Column([
                ft.Row([ft.Text(o["title"], size=16, weight=ft.FontWeight.BOLD, color=TEXT, expand=True, text_align=ft.TextAlign.RIGHT),
                        ft.Icon(ic("LOCAL_OFFER", "STAR"), color=ORANGE)], vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Row([btn("اذهب للعرض", lambda e, u=link: open_url(u), YELLOW, TEXT, "OPEN_IN_NEW", expand=True)])], spacing=10),
                bgcolor=CARD, border_radius=10, padding=14, border=ft.border.all(1, BORDER),
                shadow=shadow()))
        offers_list.controls = rows or [empty_box("LOCAL_OFFER", "لا توجد عروض الآن", "تابعنا، نضيف عروضاً جديدة باستمرار.")]

    offers_view = ft.Column(expand=True, spacing=0, visible=False, controls=[
        header("العروض والخصومات", "عروض أمازون المتجددة"),
        ft.Container(expand=True, padding=ft.padding.only(left=12, right=12, top=12), content=ft.Column(
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=10, controls=[offers_list, disclosure()]))])

    # ================================================================ صفحة الكوبونات
    coupons_list = ft.Column(spacing=10)
    timer_slot = ft.Column()
    timer_digits = ft.Text("", size=34, weight=ft.FontWeight.BOLD, color=ORANGE)

    def copy_code(c):
        try:
            page.set_clipboard(c["code"])
            toast(f"تم نسخ الكود: {c['code']}")
        except Exception:
            toast("تعذّر النسخ")

    def timer_remaining():
        t = rem["data"]["timer"]
        return (t["end_ts"] - remote.server_now()) if t else 0

    def build_coupons():
        t = rem["data"]["timer"]
        if t and timer_remaining() > 0:
            timer_digits.value = fmt_remaining(timer_remaining())
            timer_slot.controls = [ft.Container(ft.Column([
                ft.Text(t["text"], size=15, color="#FFFFFF", weight=ft.FontWeight.W_600),
                timer_digits], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2),
                bgcolor=NAVY2, border_radius=12, padding=16, alignment=ft.alignment.center, border=ft.border.all(2, ORANGE))]
        else:
            timer_slot.controls = []
        rows = []
        for c in rem["data"]["coupons"]:
            rows.append(ft.Container(ft.Column([
                ft.Text(c.get("name") or "كود خصم", size=15, weight=ft.FontWeight.BOLD, color=TEXT, text_align=ft.TextAlign.RIGHT),
                ft.Container(ft.Text(c["code"], size=20, weight=ft.FontWeight.BOLD, color=NAVY, selectable=True,
                                     text_align=ft.TextAlign.CENTER), bgcolor="#FFF8E7", border=ft.border.all(1.5, ORANGE),
                             border_radius=8, padding=ft.padding.symmetric(vertical=10), alignment=ft.alignment.center),
                ft.Row([btn("نسخ الكود", lambda e, c=c: copy_code(c), YELLOW, TEXT, "CONTENT_COPY", expand=True)]),
                ft.Text("الصق الكود عند الدفع في أمازون.", size=11, color=MUTED, text_align=ft.TextAlign.CENTER)],
                spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                bgcolor=CARD, border_radius=10, padding=14, border=ft.border.all(1, BORDER), shadow=shadow()))
        coupons_list.controls = rows or [empty_box("CONFIRMATION_NUMBER", "لا يوجد كود خصم الآن", "سنعرض الأكواد هنا فور توفرها.")]

    coupons_view = ft.Column(expand=True, spacing=0, visible=False, controls=[
        header("كوبونات الخصم", "انسخ الكود بضغطة واحدة"),
        ft.Container(expand=True, padding=ft.padding.only(left=12, right=12, top=12), content=ft.Column(
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=10, controls=[timer_slot, coupons_list, disclosure()]))])

    # ================================================================ صفحة المزيد
    more_col = ft.Column(spacing=10)

    def build_more():
        rows = [ft.Container(ft.Column([
            ft.Text("عن التطبيق", weight=ft.FontWeight.BOLD, color=TEXT),
            ft.Text("هذا التطبيق يساعدك على البحث في أمازون السعودية ومقارنة الأسعار ومتابعة العروض والكوبونات. "
                    "الأسعار تُعرض كما تظهر وقت البحث وقد تختلف عند الشراء، والسعر النهائي هو المعروض في صفحة المنتج على أمازون.",
                    size=12, color=MUTED, text_align=ft.TextAlign.RIGHT),
            disclosure()], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.END),
            bgcolor=CARD, border_radius=10, padding=14, border=ft.border.all(1, BORDER))]
        if cfg("privacy_url"):
            rows.append(btn("سياسة الخصوصية", lambda e: open_url(cfg("privacy_url")), CARD, LINK, "PRIVACY_TIP"))
        if cfg("contact"):
            rows.append(ft.Container(ft.Text(f"للتواصل: {cfg('contact')}", size=13, color=TEXT, selectable=True,
                                             text_align=ft.TextAlign.CENTER), padding=8))
        rows.append(btn("تحديث العروض والكوبونات", lambda e: refresh_remote(True), YELLOW, TEXT, "REFRESH"))
        if rem["status"] == "not_configured":
            rows.append(ft.Text("تنبيه للمشرف: لم يُضبط رابط قاعدة البيانات في app_config.py", size=11, color=RED, text_align=ft.TextAlign.CENTER))
        elif rem["status"] in ("cache", "none"):
            rows.append(ft.Text("تعذّر تحديث المحتوى من الإنترنت" + (" — يُعرض آخر محتوى محفوظ." if rem["status"] == "cache" else "."),
                                size=11, color=MUTED, text_align=ft.TextAlign.CENTER))
        more_col.controls = rows

    more_view = ft.Column(expand=True, spacing=0, visible=False, controls=[
        header("المزيد", "معلومات وإعدادات"),
        ft.Container(expand=True, padding=ft.padding.only(left=12, right=12, top=12), content=ft.Column(
            expand=True, scroll=ft.ScrollMode.AUTO, spacing=10, controls=[more_col]))])

    # ================================================================ المحتوى البعيد والمؤقت
    def apply_remote():
        a = cfg("announcement", "")
        announce.visible = bool(str(a).strip())
        announce.content = ft.Text(str(a), size=13, color=TEXT, text_align=ft.TextAlign.RIGHT)
        paint_sort()
        build_offers()
        build_coupons()
        build_more()
        if st["query"]:
            render()
        else:
            page.update()

    def refresh_remote(manual=False):
        def work():
            data, status_ = remote.fetch()
            rem["data"], rem["status"] = data, status_
            apply_remote()
            if manual:
                toast("تم التحديث" if status_ == "ok" else "تعذّر التحديث من الإنترنت")
        threading.Thread(target=work, daemon=True).start()

    def ticker():
        n = 0
        while True:
            time.sleep(1)
            n += 1
            try:
                t = rem["data"]["timer"]
                if t:
                    left = timer_remaining()
                    if left > 0 and st["tab"] == 2:
                        timer_digits.value = fmt_remaining(left)
                        page.update()
                    elif left <= 0 and timer_slot.controls:      # انتهى المؤقت: حدّث المحتوى فوراً
                        refresh_remote()
                        n = 0
                if n >= 60:
                    n = 0
                    refresh_remote()
            except Exception:
                pass

    # ================================================================ التنقل
    views = [search_view, offers_view, coupons_view, more_view]
    nav_row = ft.Row(alignment=ft.MainAxisAlignment.SPACE_AROUND)

    def paint_nav():
        items = [("SEARCH", "بحث"), ("LOCAL_OFFER", "العروض"), ("CONFIRMATION_NUMBER", "الكوبونات"), ("MENU", "المزيد")]
        nav_row.controls = [ft.Container(ft.Column([
            ft.Icon(ic(n, "CIRCLE"), size=24, color=ORANGE if st["tab"] == i else "#B0B7BD"),
            ft.Text(lbl, size=11, color="#FFFFFF" if st["tab"] == i else "#B0B7BD",
                    weight=ft.FontWeight.BOLD if st["tab"] == i else None)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=2), padding=ft.padding.symmetric(horizontal=14, vertical=6),
            border_radius=12, ink=True, on_click=lambda e, i=i: go(i)) for i, (n, lbl) in enumerate(items)]

    def go(i):
        st["tab"] = i
        for j, v in enumerate(views):
            v.visible = (j == i)
        paint_nav()
        if i == 2:
            build_coupons()
        page.update()

    # ================================================================ التركيب
    paint_sort()
    build_offers()
    build_coupons()
    build_more()
    render()
    paint_nav()
    page.add(ft.SafeArea(expand=True, content=ft.Column(expand=True, spacing=0, controls=[
        ft.Column(views, expand=True, spacing=0),
        ft.Container(nav_row, bgcolor=NAVY2, padding=ft.padding.only(top=6, bottom=6))])))
    refresh_remote()
    threading.Thread(target=ticker, daemon=True).start()


ft.app(target=main)
