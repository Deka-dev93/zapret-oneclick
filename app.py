"""Zapret OneClick: обход блокировок Discord/YouTube одной кнопкой + прокси для Telegram.
Оформление — в стиле zapret2-gui (фиолетово-чёрное «стекло», неоновый акцент).
Отрисовка: Pillow (сглаживание суперсэмплингом) + плавные анимации по времени."""
import ctypes
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from PIL import Image, ImageChops, ImageDraw, ImageTk

try:  # чёткое окно на экранах с масштабом >100% (иначе Windows растягивает bitmap)
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import diag
import engine
from gfx import GfxMixin, ease_out, hex2rgb, mix
from tgproxy import TgProxy

APP = "Zapret OneClick"

# ── палитра zapret2-gui (index.css: «Violet-black glass») ───────────────────
C_APP, C_SIDEBAR, C_CARD, C_CARDIN = "#09070f", "#110d1c", "#171226", "#0d0a16"
C_RAISE, C_HOVER = "#1e1830", "#271f3e"
INK, INK2, INK3, INK4, INK5 = "#f6f8fd", "#dde4f0", "#c0bad8", "#968eb4", "#686082"
ACCENT, ACCENT_SOFT, ACCENT_FILL, ACCENT2 = "#a78bfa", "#c4b5fd", "#8b5cf6", "#e879f9"
OK, DANGER, WARN = "#34d399", "#ff453a", "#ffb340"
LINE = lambda bg: mix(bg, "#ffffff", 0.09)
LINE_STRONG = lambda bg: mix(bg, "#ffffff", 0.16)
MAIN_FILL = mix(C_APP, C_SIDEBAR, 0.7)
FONT = "Segoe UI"
MONO = "Consolas"
BLOBS = [(0.12, 0.05, 0.55, ACCENT_FILL, 0.34), (0.95, 0.95, 0.50, "#d946ef", 0.22),
         (0.65, 0.20, 0.30, ACCENT_FILL, 0.12)]
FADE_DUR = 0.26


def q(v, n=8):
    return round(v * n) / n


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, ""
    else:
        py = sys.executable.replace("python.exe", "pythonw.exe")
        exe = py if os.path.exists(py) else sys.executable
        params = f'"{os.path.abspath(sys.argv[0])}"'
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)


class App(GfxMixin, tk.Tk):
    PAGES = [("home", "Главная"), ("tg", "Telegram"), ("diag", "Диагностика"),
             ("log", "Журнал"), ("set", "Настройки")]

    def __init__(self):
        super().__init__()
        S = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.gfx_init(S)
        self.title(APP)
        try:  # иконка окна (в exe лежит рядом, в _MEIPASS)
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            ico = Image.open(os.path.join(base, "icon.png")).resize((64, 64), Image.LANCZOS)
            self._icon_ref = ImageTk.PhotoImage(ico)
            self.iconphoto(True, self._icon_ref)
        except Exception:
            pass
        self.geometry(f"{int(960 * S)}x{int(640 * S)}")
        self.minsize(int(900 * S), int(600 * S))
        self.configure(bg=C_APP)
        self._dark_titlebar()

        self.cfg = engine.load_config()
        engine.set_zapret_dir(engine.pick_initial_dir(self.cfg))
        self.proxy = TgProxy(self.cfg, lambda: engine.save_config(self.cfg))
        self.winws = engine.Winws()
        self.q: "queue.Queue" = queue.Queue()
        self.cancel = threading.Event()
        self.logs: list = []

        # состояние
        self.page = "home"
        self.on = self.busy = False
        self.status = "Отключено"
        self.prog = (0, 1, "", 0)
        self.best = self.cfg.get("best")
        self.score = (0, 0)
        self.choice = self.cfg.get("choice", "auto")
        self.tg_auto = self.cfg.get("tg_auto", True)
        self.regions, self.hover, self.pressed = [], None, None
        self.set_info = {}
        self._refresh_set_info()
        self.toasts: list = []
        self.dl_busy = False
        self.diag_items: list = []
        self.diag_verdict = None
        self.diag_running = False
        self.diag_stage = ""
        self.diag_off = 0

        # анимация
        self.bg = None
        self.photo = None
        self.size = (0, 0)
        self.fade = None            # (t0, src_img, kind)
        self.last_fr = None
        self.dirty = True
        self.last_t = time.perf_counter()
        self._resize_job = None

        self.cv = tk.Canvas(self, bg=C_APP, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.img_item = self.cv.create_image(0, 0, anchor="nw")
        self.logbox = tk.Text(self.cv, bg=C_CARDIN, fg=INK3, bd=0, highlightthickness=0,
                              font=(MONO, 9), wrap="word", state="disabled", padx=10, pady=8,
                              insertbackground=INK, selectbackground=ACCENT_FILL)
        self.logwin = self.cv.create_window(0, 0, window=self.logbox, anchor="nw", state="hidden")
        self.cv.bind("<Configure>", self._on_configure)
        self.cv.bind("<Motion>", self._motion)
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.cv.bind("<Leave>", lambda e: self._set_hover(None))
        self.cv.bind("<MouseWheel>", self._wheel)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self._pump)
        self.after(3000, self._watch)
        self.after(16, self._loop)
        self.log("Готово. Нажмите большую кнопку.")
        self.log(f"Папка zapret: {engine.zapret_dir()}")
        if self.proxy.imported:
            self.log("Telegram: используются настройки установленного TgWsProxy.")

    # ═══════════════════════════ окно / цикл ══════════════════════════════
    def _dark_titlebar(self):
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            dw = ctypes.windll.dwmapi.DwmSetWindowAttribute
            dw(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
            dw(hwnd, 35, ctypes.byref(ctypes.c_int(0x0F0709)), 4)
            dw(hwnd, 34, ctypes.byref(ctypes.c_int(0x0F0709)), 4)
        except Exception:
            pass

    def _on_configure(self, e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(50, self._rebuild)

    def _rebuild(self):
        pw, ph = self.cv.winfo_width(), self.cv.winfo_height()
        if pw < 100 or ph < 100:
            return
        if (pw, ph) != self.size:
            self.size = (pw, ph)
            self.bg = self.make_bg(pw, ph, C_APP, BLOBS)
            self.photo = ImageTk.PhotoImage(Image.new("RGB", (pw, ph)))
            self.cv.itemconfigure(self.img_item, image=self.photo)
            self.fade = None
        self.dirty = True

    def _loop(self):
        now = time.perf_counter()
        dt = min(0.05, now - self.last_t)
        self.last_t = now
        moving = self.tw_step(dt)
        live = ((self.on or self.busy) and self.page == "home") or bool(self.toasts) \
            or (self.diag_running and self.page == "diag")
        if self.bg is not None and (self.dirty or moving or self.fade or live):
            self._draw(now)
            self.dirty = False
        self.after(6 if (moving or self.fade or live) else 25, self._loop)

    def _snap(self, kind="state"):
        """Запомнить текущий кадр — следующие кадры плавно проявляются поверх него."""
        if self.last_fr is not None:
            self.fade = (time.perf_counter(), self.last_fr, kind)

    def _draw(self, now):
        pw, ph = self.size
        S = self.S
        W, H = pw / S, ph / S
        self.fr = self.bg.copy()
        self.regions = []
        self.oy = 0.0
        e = 1.0
        if self.fade:
            t0, src, kind = self.fade
            if src.size != self.fr.size or now - t0 > FADE_DUR:
                self.fade = None
            else:
                e = ease_out((now - t0) / FADE_DUR)
        self.cv.itemconfigure(self.logwin, state="hidden")
        self._sidebar(14, 14, 224, H - 14)
        mx1, my1, mx2, my2 = 238, 14, W - 14, H - 14
        self.rr(mx1, my1, mx2, my2, 18, (MAIN_FILL, 0.82), LINE(MAIN_FILL))
        self.hline(mx1 + 18, mx2 - 18, my1 + 1, mix(MAIN_FILL, "#ffffff", 0.12))
        if self.fade and self.fade[2] == "page":
            self.oy = (1 - e) * 10
        getattr(self, "_page_" + self.page)(mx1 + 28, my1 + 24, mx2 - 28, my2 - 24)
        self.oy = 0.0
        if self.fade:
            box = (int(mx1 * S) + 2, int(my1 * S) + 2, int(mx2 * S) - 2, int(my2 * S) - 2)
            new = self.fr.crop(box)
            self.fr.paste(Image.blend(self.fade[1].crop(box), new, e), box[:2])
        self._draw_toasts(now, W)
        self._upload()
        self.last_fr = self.fr

    def _upload(self):
        """Грузим в Tk только изменившийся прямоугольник (быстрее полной заливки кадра)."""
        prev = self.last_fr
        if prev is None or prev.size != self.fr.size:
            self.photo.paste(self.fr)
            return
        box = ImageChops.difference(prev, self.fr).getbbox()
        if box is None:
            return
        x0, y0, x1, y1 = box
        if (x1 - x0) * (y1 - y0) > 0.55 * self.fr.width * self.fr.height:
            self.photo.paste(self.fr)
            return
        patch = ImageTk.PhotoImage(self.fr.crop(box))
        self.tk.call(str(self.photo), "copy", str(patch), "-to", x0, y0)
        del patch

    # ═══════════════════════════ виджеты ══════════════════════════════════
    def reg(self, x1, y1, x2, y2, cb, key):
        self.regions.append((x1, y1, x2, y2, cb, key))

    def hv(self, key):
        return q(self.tw_get("h_" + key, 1.0 if self.hover == key else 0.0, 16))

    def pr(self, key):
        return q(self.tw_get("p_" + key, 1.0 if self.pressed == key else 0.0, 32), 4)

    def card(self, x1, y1, x2, y2, fill=C_CARD, r=16):
        self.rr(x1, y1, x2, y2, r, fill, LINE(fill))
        self.hline(x1 + r, x2 - r, y1 + 1, mix(fill, "#ffffff", 0.10))

    def button(self, x1, y1, x2, y2, label, cb, key, kind="ghost", size=10):
        hv, pr = self.hv(key), self.pr(key)
        if kind == "primary":
            fill = mix(mix(ACCENT_FILL, ACCENT, hv), "#6d28d9", pr * 0.5)
            out, tcol = mix(fill, "#ffffff", 0.18), "#ffffff"
        elif kind == "danger":
            fill = mix(mix(DANGER, "#ff6e64", hv), "#b02a22", pr * 0.5)
            out, tcol = mix(fill, "#ffffff", 0.18), "#ffffff"
        else:
            fill = mix(mix(C_RAISE, C_HOVER, hv), C_CARDIN, pr * 0.6)
            out, tcol = LINE_STRONG(fill), mix(INK2, INK, hv)
        if kind != "ghost" and hv > 0.05:  # мягкое свечение при наведении
            self.glow((x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) * 0.62, ACCENT_FILL if kind == "primary" else DANGER,
                      0.10 * hv, 2.4)
        i = pr * 1.0
        self.rr(x1 + i, y1 + i, x2 - i, y2 - i, 10, fill, out)
        self.txt((x1 + x2) / 2, (y1 + y2) / 2, label, size, tcol, True, "center")
        self.reg(x1, y1, x2, y2, cb, key)

    def chip(self, xr, y, label, color):
        c = self.tw_color("chip", color, 9)
        w = self.measure(label, 9, True) + 36
        self.rr(xr - w, y, xr, y + 26, 13, mix(C_CARD, c, 0.14), mix(C_CARD, c, 0.38))
        self.ell(xr - w + 15, y + 13, 4, c)
        self.txt(xr - w + 27, y + 13, label, 9, c, True, "w")

    def toggle(self, x, y, value, cb, key):
        k = self.tw_get("tg_" + key, 1.0 if value else 0.0, 14)
        hv = self.hv(key)
        track = mix(mix(C_RAISE, C_HOVER, hv), ACCENT_FILL, k)
        self.rr(x, y, x + 44, y + 24, 12, track, LINE_STRONG(track))
        self.ell(x + 12 + 20 * k, y + 12, 9, mix(INK4, "#ffffff", k))
        self.reg(x, y, x + 44, y + 24, cb, key)

    def grad_bar(self, x1, y1, x2, y2):
        px, py = self.sc(x1), self.sc(y1 + self.oy)
        w, h = self.sc(x2) - px, self.sc(y2 + self.oy) - py
        if w < 2 or h < 2:
            return

        def make():
            ss = 3
            W_, H_ = w * ss, h * ss
            grad = Image.linear_gradient("L").rotate(90).resize((W_, H_), Image.BILINEAR)
            im = Image.composite(Image.new("RGB", (W_, H_), hex2rgb(ACCENT_FILL)),
                                 Image.new("RGB", (W_, H_), hex2rgb(ACCENT2)), grad).convert("RGBA")
            mask = Image.new("L", (W_, H_), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, W_ - 1, H_ - 1], radius=H_ // 2, fill=255)
            im.putalpha(mask)
            return im.resize((w, h), Image.LANCZOS)
        self.blit(self._cached(("gb", w, h), make), px, py)

    # ═══════════════════════════ боковая панель ═══════════════════════════
    def _sidebar(self, x1, y1, x2, y2):
        fill = mix(C_APP, C_SIDEBAR, 1.0)
        self.rr(x1, y1, x2, y2, 18, (fill, 0.88), LINE(fill))
        self.hline(x1 + 18, x2 - 18, y1 + 1, mix(fill, "#ffffff", 0.12))
        # логотип
        self.glow(x1 + 34, y1 + 36, 34, ACCENT_FILL, 0.45, 2.0)
        self.ell(x1 + 34, y1 + 36, 16, ACCENT_FILL)
        self.ell(x1 + 34, y1 + 36, 8, ACCENT2)
        self.txt(x1 + 60, y1 + 24, "Zapret", 13, INK, True)
        self.txt(x1 + 60, y1 + 44, "OneClick", 9, ACCENT)
        y0 = y1 + 84
        idx = [k for k, _ in self.PAGES].index(self.page)
        ny = self.tw_get("nav_y", idx * 48.0, 15, 0.1)   # плавный слайд подсветки
        self.rr(x1 + 10, y0 + ny, x2 - 10, y0 + ny + 42, 12, mix(fill, ACCENT_FILL, 0.20),
                mix(fill, ACCENT, 0.38))
        self.rr(x1 + 10, y0 + ny + 11, x1 + 13, y0 + ny + 31, 2, ACCENT)
        for i, (key, label) in enumerate(self.PAGES):
            y = y0 + i * 48
            hv = self.hv("nav_" + key)
            act = q(self.tw_get("na_" + key, 1.0 if self.page == key else 0.0, 14), 12)
            if hv > 0.05 and act < 0.9:
                self.rr(x1 + 10, y, x2 - 10, y + 42, 12, (C_RAISE, hv * 0.9))
            c = mix(mix(INK4, INK2, hv), ACCENT_SOFT, act)
            self.txt(x1 + 36, y + 21, label, 11, c, act > 0.5, "w")
            self.reg(x1 + 10, y, x2 - 10, y + 42, lambda k=key: self._goto(k), "nav_" + key)
        self.rr(x1 + 12, y2 - 78, x2 - 12, y2 - 14, 12, C_CARDIN, LINE(C_CARDIN))
        for i, (name, on) in enumerate((("Обход DPI", self.on and not self.busy),
                                        ("Прокси Telegram", self.proxy.running))):
            yy = y2 - 62 + i * 24
            c = self.tw_color(("dot", i), OK if on else INK5, 8)
            if on:
                self.glow(x1 + 30, yy, 9, OK, 0.35, 1.8)
            self.ell(x1 + 30, yy, 4, c)
            self.txt(x1 + 42, yy, name, 9, INK3 if on else INK5, False, "w")

    # ═══════════════════════════ страницы ═════════════════════════════════
    def _header(self, x1, y1, x2, title, sub):
        self.txt(x1, y1, title, 20, INK, True)
        self.txt(x1, y1 + 34, sub, 10, INK4)

    def _page_home(self, x1, y1, x2, y2):
        self._header(x1, y1, x2, "Главная", "Обход блокировок Discord и YouTube · прокси для Telegram")
        if self.busy:
            self.chip(x2, y1 + 2, "Подбор…", WARN)
        elif self.on:
            self.chip(x2, y1 + 2, "Работает", OK)
        else:
            self.chip(x2, y1 + 2, "Отключено", INK5)
        hy1, hy2 = y1 + 70, y1 + 70 + 220
        self.card(x1, hy1, x2, hy2)
        cx, cy, R = x1 + 150, (hy1 + hy2) / 2, 68
        self._power(cx, cy, R)
        tx = x1 + 290
        title = "Подбираю стратегию" if self.busy else ("Защита включена" if self.on else "Защита отключена")
        self.txt(tx, hy1 + 40, title, 19, INK, True)
        if self.busy:
            i, n, name, sc = self.prog
            self.txt(tx, hy1 + 78, self.fit(self.status, 10, x2 - tx - 24), 10, INK3)
            bx2 = x2 - 30
            self.rr(tx, hy1 + 112, bx2, hy1 + 120, 4, C_CARDIN)
            frac = self.tw_get("prog", i / max(n, 1), 5)
            if frac > 0.005:
                self.grad_bar(tx, hy1 + 112, tx + max(8, (bx2 - tx) * frac), hy1 + 120)
            self.txt(tx, hy1 + 130, f"{i} из {n}", 9, INK4)
            self.button(tx, hy1 + 160, tx + 130, hy1 + 192, "Отмена", self._toggle, "cancel", "ghost")
        elif self.on:
            self.txt(tx, hy1 + 78, f"Стратегия: {self.best or '—'}", 11, ACCENT_SOFT, True)
            self.txt(tx, hy1 + 106, f"Доступно целей: {self.score[0]} из {self.score[1]}", 10, INK3)
            self.button(tx, hy1 + 150, tx + 150, hy1 + 184, "Отключить", self._toggle, "off", "danger")
        else:
            self.txt(tx, hy1 + 78, "Одна кнопка: сама скачает zapret (если нужно),\nпереберёт стратегии и оставит лучшую.",
                     10, INK4)
            self.button(tx, hy1 + 140, tx + 170, hy1 + 178, "Включить", self._toggle, "on", "primary", 11)
        sy1, sy2 = hy2 + 14, hy2 + 14 + 92
        cw = (x2 - x1 - 28) / 3
        stats = [
            ("Стратегия", self.best or "—", "авто" if self.choice == "auto" else "вручную"),
            ("Доступно целей", f"{self.score[0]}/{self.score[1]}" if self.score[1] else "—", "Discord · YouTube"),
            ("Telegram", "Работает" if self.proxy.running else "Выключен", f"{self.proxy.host}:{self.proxy.port}"),
        ]
        for i, (a, b, c) in enumerate(stats):
            sx = x1 + i * (cw + 14)
            self.card(sx, sy1, sx + cw, sy2, r=14)
            self.txt(sx + 16, sy1 + 14, a, 9, INK4)
            self.txt(sx + 16, sy1 + 36, self.fit(b, 14, cw - 32, True), 14,
                     OK if (i == 2 and self.proxy.running) else INK, True)
            self.txt(sx + 16, sy1 + 66, c, 9, INK5)
        ay1, ay2 = sy2 + 14, y2
        if ay2 - ay1 > 60:
            self.card(x1, ay1, x2, ay2, r=14)
            self.txt(x1 + 16, ay1 + 12, "Активность", 9, INK4)
            rows = max(1, int((ay2 - ay1 - 40) // 18))
            for k, line in enumerate(self.logs[-rows:]):
                self.txt(x1 + 16, ay1 + 34 + k * 18, self.fit(line, 9, x2 - x1 - 32, mono=True),
                         9, INK3, False, "nw", True)

    # ── круглая кнопка питания ──────────────────────────────────────────
    def _make_body(self, Rp, onq, hvq):
        ss = 3
        d = Rp * 2 + 6
        D = d * ss
        grad = self._radial.resize((D, D), Image.BICUBIC).point(
            [int(((v / 255) ** 1.25) * 255) for v in range(256)])
        c_out = mix(mix(C_RAISE, C_HOVER, hvq * 0.6), ACCENT_FILL, onq)
        c_in = mix(mix(C_HOVER, "#3a2f5c", hvq * 0.6), ACCENT_SOFT, onq)
        body = Image.composite(Image.new("RGB", (D, D), hex2rgb(c_in)),
                               Image.new("RGB", (D, D), hex2rgb(c_out)), grad).convert("RGBA")
        circ = Image.new("L", (D, D), 0)
        m = 3 * ss
        ImageDraw.Draw(circ).ellipse([m, m, D - m - 1, D - m - 1], fill=255)
        body.putalpha(circ)
        ring = mix(mix(LINE_STRONG(C_RAISE), ACCENT_SOFT, hvq * 0.5), ACCENT, onq)
        ImageDraw.Draw(body).ellipse([m, m, D - m - 1, D - m - 1], outline=hex2rgb(ring) + (255,),
                                     width=int(2.2 * self.S * ss))
        return body.resize((d, d), Image.LANCZOS)

    def _make_icon(self, Rp, color):
        ss = 4
        d = Rp * 2 + 6
        D = d * ss
        im = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        c = D / 2
        r = Rp * 0.40 * ss
        w = int(Rp * 0.092 * ss)
        rgba = hex2rgb(color) + (255,)
        rb = r + w / 2
        dr.arc([c - rb, c - rb + w * 0.4, c + rb, c + rb + w * 0.4], start=-55, end=235, fill=rgba, width=w)
        for a in (-55, 235):  # круглые концы дуги
            ex = c + r * math.cos(math.radians(a))
            ey = c + w * 0.4 + r * math.sin(math.radians(a))
            dr.ellipse([ex - w / 2 + 1, ey - w / 2 + 1, ex + w / 2 - 1, ey + w / 2 - 1], fill=rgba)
        y1_, y2_ = c - r * 1.25, c - r * 0.10
        dr.rounded_rectangle([c - w / 2, y1_, c + w / 2, y2_], radius=w // 2, fill=rgba)
        return im.resize((d, d), Image.LANCZOS)

    def _make_spin(self, rp, ang, aq):
        ss = 3
        d = (rp + self.sc(6)) * 2
        D = d * ss
        im = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        c = D / 2
        r = rp * ss
        w = int(4.2 * self.S * ss)
        rb = r + w / 2  # PIL рисует штрих внутрь bbox — центрируем его на радиусе r
        for a0, ext, colr in ((ang, 100, ACCENT2), (ang + 180, 62, ACCENT)):
            dr.arc([c - rb, c - rb, c + rb, c + rb], start=a0, end=a0 + ext, fill=hex2rgb(colr) + (255,), width=w)
            for a in (a0, a0 + ext):
                ex, ey = c + r * math.cos(math.radians(a)), c + r * math.sin(math.radians(a))
                dr.ellipse([ex - w / 2, ey - w / 2, ex + w / 2, ey + w / 2], fill=hex2rgb(colr) + (255,))
        im = im.resize((d, d), Image.LANCZOS)
        if aq < 1:
            a_ = im.getchannel("A").point(lambda v: int(v * aq))
            im.putalpha(a_)
        return im

    def _power(self, cx, cy, R):
        now = time.perf_counter()
        on_t = self.tw_get("pw_on", 1.0 if (self.on and not self.busy) else 0.0, 5)
        busy_t = self.tw_get("pw_busy", 1.0 if self.busy else 0.0, 7)
        hv = self.tw_get("h_power", 1.0 if self.hover == "power" else 0.0, 14)
        pr = self.tw_get("p_power", 1.0 if self.pressed == "power" else 0.0, 30)
        pulse = (math.sin(now * 2.3) + 1) / 2
        live = max(on_t, busy_t * 0.8)
        g = 0.10 * hv + live * (0.30 + 0.17 * pulse * on_t + 0.08 * busy_t)
        if g > 0.01:
            self.glow(cx, cy, R * 2.25, ACCENT_FILL, min(g, 0.9), 2.0)
            self.glow(cx + R * 0.35, cy + R * 0.4, R * 1.7, "#d946ef", live * (0.10 + 0.05 * pulse), 2.2)
        scale = 1 + 0.035 * hv - 0.05 * pr
        Rp = self.sc(R * scale)
        onq = round(on_t * 20) / 20
        hq = round(hv * 6) / 6
        body = self._cached(("pbody", Rp, onq, hq), lambda: self._make_body(Rp, onq, hq))
        px, py = self.sc(cx) - body.width // 2, self.sc(cy + self.oy) - body.height // 2
        self.blit(body, px, py)
        ic = mix(mix(INK4, ACCENT_SOFT, max(hv, busy_t)), "#ffffff", on_t)
        icon = self._cached(("pico", Rp, ic), lambda: self._make_icon(Rp, ic))
        self.blit(icon, px, py)
        if busy_t > 0.02:
            ang = int((now * 300) % 360 // 2 * 2)
            rp = self.sc(R + 12)
            sp = self._cached(("spin", rp, ang, round(busy_t * 6) / 6), lambda: self._make_spin(rp, ang, round(busy_t * 6) / 6))
            self.blit(sp, self.sc(cx) - sp.width // 2, self.sc(cy + self.oy) - sp.height // 2)
        self.reg(cx - R, cy - R, cx + R, cy + R, self._toggle, "power")

    # ── Telegram ────────────────────────────────────────────────────────
    def _page_tg(self, x1, y1, x2, y2):
        p = self.proxy
        self._header(x1, y1, x2, "Telegram", "Локальный MTProto-прокси: Telegram → WebSocket → дата-центр")
        self.chip(x2, y1 + 2, "Работает" if p.running else "Остановлен", OK if p.running else INK5)
        cy1 = y1 + 70
        self.card(x1, cy1, x2, cy1 + 262)
        rows = [("Адрес", f"{p.host}:{p.port}"),
                ("Secret", p.secret[:6] + "•" * 20 + p.secret[-4:]),
                ("Настройки", "TgWsProxy (config.json)" if p.imported else "собственные"),
                ("Резерв", "WebSocket → Cloudflare → TCP" if p.cfg.get("cfproxy") else "WebSocket → TCP")]
        for i, (a, b) in enumerate(rows):
            yy = cy1 + 22 + i * 34
            self.txt(x1 + 22, yy, a, 10, INK4)
            self.txt(x1 + 150, yy, b, 10, INK, False, "nw", a == "Secret")
        yl = cy1 + 22 + 4 * 34
        self.rr(x1 + 22, yl, x2 - 22, yl + 36, 10, C_CARDIN, LINE(C_CARDIN))
        self.txt(x1 + 34, yl + 18, self.fit(p.link, 9, x2 - x1 - 70, mono=True), 9, ACCENT_SOFT, False, "w", True)
        by = yl + 52
        if p.running:
            self.button(x1 + 22, by, x1 + 186, by + 36, "Остановить", self._tg_toggle, "tgs", "danger")
        else:
            self.button(x1 + 22, by, x1 + 186, by + 36, "Запустить", self._tg_toggle, "tgs", "primary")
        self.button(x1 + 198, by, x1 + 400, by + 36, "Подключить Telegram", self._open_tg, "tgo", "ghost")
        self.button(x1 + 412, by, x1 + 600, by + 36, "Копировать ссылку", self._copy_link, "tgc", "ghost")
        oy = cy1 + 262 + 14
        self.card(x1, oy, x2, oy + 66, r=14)
        self.txt(x1 + 22, oy + 14, "Запускать вместе с главной кнопкой", 11, INK, True)
        self.txt(x1 + 22, oy + 38, "Прокси стартует после включения обхода и останавливается вместе с ним", 9, INK4)
        self.toggle(x2 - 66, oy + 21, self.tg_auto, self._toggle_tg_auto, "tgauto")
        if p.error:
            self.rr(x1, oy + 80, x2, oy + 134, 12, mix(C_CARD, DANGER, 0.12), mix(C_CARD, DANGER, 0.4))
            self.txt(x1 + 16, oy + 107, self.fit(p.error, 10, x2 - x1 - 32), 10, "#ffb4ae", False, "w")
        else:
            self.txt(x1, oy + 84, "Как подключить: нажмите «Подключить Telegram» и подтвердите добавление прокси.\n"
                                  "Если порт занят — закройте TgWsProxy в трее.", 9, INK5)

    def _page_log(self, x1, y1, x2, y2):
        self._header(x1, y1, x2, "Журнал", "Ход подбора стратегий и события прокси")
        self.button(x2 - 110, y1 + 2, x2, y1 + 32, "Очистить", self._clear_log, "clr", "ghost", 9)
        S = self.S
        self.cv.coords(self.logwin, int(x1 * S), int((y1 + 70) * S))
        self.cv.itemconfigure(self.logwin, state="normal", width=int((x2 - x1) * S), height=int((y2 - y1 - 70) * S))

    def _refresh_set_info(self):
        zd = engine.zapret_dir()
        ok = engine.is_zapret_dir(zd)
        self.set_info = {
            "dir": str(zd), "ok": ok,
            "mode": engine.game_filter()[0] if ok else "—",
            "n": len(engine.load_strategies()) if ok else 0,
            "targets": len(engine.load_targets()) if ok else 0,
        }

    def _page_set(self, x1, y1, x2, y2):
        self._header(x1, y1, x2, "Настройки", "Папка zapret и выбор стратегии")
        info = self.set_info
        c1 = y1 + 70
        self.card(x1, c1, x2, c1 + 170)
        self.txt(x1 + 22, c1 + 16, "Папка zapret", 11, INK, True)
        self.rr(x1 + 22, c1 + 46, x2 - 22, c1 + 82, 10, C_CARDIN, LINE(C_CARDIN))
        self.txt(x1 + 34, c1 + 64, self.fit(info["dir"], 9, x2 - x1 - 70, mono=True), 9,
                 INK3 if info["ok"] else DANGER, False, "w", True)
        self.txt(x1 + 22, c1 + 94, f"Стратегий: {info['n']}   ·   Игровой фильтр: {info['mode']}   ·   "
                                   f"Целей проверки: {info['targets']}", 9, INK4)
        self.button(x1 + 22, c1 + 122, x1 + 190, c1 + 154, "Выбрать папку…", self._pick_dir, "pick", "ghost", 9)
        self.button(x1 + 202, c1 + 122, x1 + 420, c1 + 154,
                    "Скачивание…" if self.dl_busy else "Скачать свежий zapret", self._download, "dl", "ghost", 9)
        c2 = c1 + 170 + 14
        self.card(x1, c2, x2, c2 + 112)
        self.txt(x1 + 22, c2 + 16, "Стратегия", 11, INK, True)
        self.txt(x1 + 22, c2 + 40, "Авто — перебор всех и выбор лучшей по проверке Discord/YouTube", 9, INK4)
        label = "Авто (лучшая)" if self.choice == "auto" else self.choice
        self.button(x1 + 22, c2 + 64, x1 + 330, c2 + 96, label + "  ▾", self._strategy_menu, "smenu", "ghost", 9)
        self.txt(x1, y2 - 18, "Приложение не изменяет файлы в папке zapret (кроме создания *-user.txt, если их нет).",
                 9, INK5)

    # ── Диагностика ─────────────────────────────────────────────────────
    STATUS_COL = {"ok": OK, "warn": WARN, "fail": DANGER, "info": ACCENT}

    def _wrap(self, s, size, maxw, maxlines=2):
        words, lines, cur = s.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if self.measure(t, size) <= maxw:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) > maxlines:
            lines = lines[:maxlines]
            lines[-1] = self.fit(lines[-1] + " …", size, maxw)
        return [ln for ln in lines if ln]

    def _page_diag(self, x1, y1, x2, y2):
        self._header(x1, y1, x2, "Диагностика", "Права, файлы, антивирус, драйвер и сеть — и что именно мешает")
        if self.diag_running:
            self.button(x2 - 170, y1 + 2, x2, y1 + 34, "Идёт проверка…", lambda: None, "dg_run", "ghost", 9)
        else:
            self.button(x2 - 170, y1 + 2, x2, y1 + 34, "Запустить", self._run_diag, "dg_run", "primary", 10)
            if self.diag_items:
                self.button(x2 - 350, y1 + 2, x2 - 182, y1 + 34, "Копировать отчёт", self._copy_report,
                            "dg_copy", "ghost", 9)
        by1, by2 = y1 + 62, y1 + 62 + 76
        v = self.diag_verdict
        if self.diag_running:
            self.card(x1, by1, x2, by2, fill=mix(C_CARD, ACCENT_FILL, 0.08), r=14)
            ph = time.perf_counter()
            for k in range(3):
                a = (math.sin(ph * 5 - k * 0.7) + 1) / 2
                self.ell(x1 + 30 + k * 14, (by1 + by2) / 2, 3 + a * 2, mix(C_CARD, ACCENT, 0.4 + 0.6 * a))
            self.txt(x1 + 82, by1 + 22, "Идёт проверка", 12, INK, True)
            self.txt(x1 + 82, by1 + 46, self.fit(self.diag_stage, 10, x2 - x1 - 110), 10, INK4)
        elif v is not None:
            c = self.STATUS_COL[v.status]
            self.rr(x1, by1, x2, by2, 14, mix(C_CARD, c, 0.12), mix(C_CARD, c, 0.45))
            self.ell(x1 + 28, by1 + 26, 6, c)
            self.glow(x1 + 28, by1 + 26, 16, c, 0.35, 1.8)
            self.txt(x1 + 48, by1 + 14, self.fit(v.title, 12, x2 - x1 - 70, True), 12, INK, True)
            for k, ln in enumerate(self._wrap(v.detail, 9, x2 - x1 - 70, 3)):
                self.txt(x1 + 48, by1 + 38 + k * 15, ln, 9, INK3)
        else:
            self.card(x1, by1, x2, by2, r=14)
            self.txt(x1 + 22, by1 + 18, "Нажмите «Запустить»", 12, INK, True)
            self.txt(x1 + 22, by1 + 44, "Проверю права администратора, файлы zapret, антивирус, драйвер WinDivert,\n"
                                        "конфликтующие программы и сеть — и подскажу, что исправить.", 9, INK4)
            return
        # список проверок
        ly = by2 + 14
        items = self.diag_items
        yy, shown, first = ly, 0, min(self.diag_off, max(0, len(items) - 1))
        for it in items[first:]:
            dl = self._wrap(it.detail, 9, x2 - x1 - 66, 2) if it.detail else []
            h = 30 + 15 * len(dl)
            if yy + h > y2 + 4:
                break
            c = self.STATUS_COL[it.status]
            self.ell(x1 + 12, yy + 12, 4.5, c)
            self.txt(x1 + 30, yy + 4, self.fit(it.title, 10, x2 - x1 - 66, True), 10, INK, True)
            for k, ln in enumerate(dl):
                self.txt(x1 + 30, yy + 24 + k * 15, ln, 9, INK4)
            yy += h + 6
            shown += 1
            self.hline(x1 + 30, x2, yy - 3, mix(MAIN_FILL, "#ffffff", 0.06))
        if len(items) > shown + first or first > 0:
            tot = max(1, len(items))
            th = max(24, (y2 - ly) * shown / tot)
            ty = ly + (y2 - ly - th) * (first / max(1, tot - shown))
            self.rr(x2 - 4, ty, x2, ty + th, 2, mix(MAIN_FILL, ACCENT, 0.5))

    def _wheel(self, e):
        if self.page == "diag" and self.diag_items:
            self.diag_off = max(0, min(len(self.diag_items) - 1, self.diag_off + (-1 if e.delta > 0 else 1)))
            self.dirty = True

    def _run_diag(self):
        if self.diag_running:
            return
        self.diag_items, self.diag_verdict, self.diag_off = [], None, 0
        self.diag_running, self.diag_stage = True, "Подготовка…"
        self.toast("Диагностика запущена", "info", key="diag", sticky=True,
                   sub="Проверяю права, файлы, антивирус и сеть")
        self.dirty = True

        def work():
            try:
                v = diag.run(engine.zapret_dir(), self.winws, lambda it: self.q.put(("diag_item", it)),
                             lambda s: self.q.put(("diag_stage", s)))
            except Exception as e:  # noqa: BLE001
                v = diag.Item("fail", "Ошибка диагностики", str(e))
            self.q.put(("diag_done", v))
        threading.Thread(target=work, daemon=True).start()

    def _copy_report(self):
        text = diag.report_text(self.diag_items, self.diag_verdict or diag.Item("info", "—"))
        self.clipboard_clear()
        self.clipboard_append(text)
        self.toast("Отчёт скопирован", "ok", sub="Вставьте его в сообщение, чтобы показать разработчику")

    # ── Плашки-уведомления ──────────────────────────────────────────────
    TOAST_COL = {"info": ACCENT, "ok": OK, "error": DANGER, "warn": WARN}

    def toast(self, msg, kind="info", key=None, sticky=False, sub="", dur=4.5):
        self.q.put(("toast", msg, kind, key, sticky, sub, dur))

    def toast_end(self, key, msg=None, kind=None, sub="", dur=4.5):
        self.q.put(("toast_end", key, msg, kind, sub, dur))

    def _add_toast(self, msg, kind, key, sticky, sub, dur):
        now = time.perf_counter()
        t = next((x for x in self.toasts if key and x["key"] == key), None)
        if t:
            t.update(msg=msg, kind=kind, sub=sub, sticky=sticky, out=None,
                     until=None if sticky else now + dur)
        else:
            self.toasts.append(dict(key=key, msg=msg, kind=kind, sub=sub, sticky=sticky, t0=now, out=None,
                                    until=None if sticky else now + dur))

    def _end_toast(self, key, msg, kind, sub, dur):
        t = next((x for x in self.toasts if x["key"] == key), None)
        if not t:
            if msg:
                self._add_toast(msg, kind or "info", key, False, sub, dur)
            return
        if msg:
            t["msg"] = msg
        if kind:
            t["kind"] = kind
        t["sub"], t["sticky"] = sub, False
        t["until"] = time.perf_counter() + dur

    def _dismiss_toast(self, t):
        if t["out"] is None:
            t["out"] = time.perf_counter()
            self.dirty = True

    def _draw_toasts(self, now, W):
        """Плашки в правом нижнем углу, стопкой снизу вверх."""
        H = self.size[1] / self.S
        keep, y = [], 0.0   # y — накопленная высота стопки снизу
        for t in self.toasts:
            if t["out"] is None and t["until"] is not None and now > t["until"]:
                t["out"] = now
            age = ease_out((now - t["t0"]) / 0.32)
            leave = ease_out((now - t["out"]) / 0.30) if t["out"] is not None else 0.0
            if leave >= 1.0:
                continue
            keep.append(t)
            a = q(age * (1 - leave), 8)
            off = (1 - age) * 18 + leave * 10          # выезжает снизу, уходит вниз
            c = self.TOAST_COL.get(t["kind"], ACCENT)
            w = max(270, min(430, max(self.measure(t["msg"], 10, True), self.measure(t["sub"], 9)) + 84))
            h = 60 if t["sub"] else 46
            x2 = W - 32
            x1 = x2 - w
            yy = H - 30 - h - y + off
            if a > 0.01:
                self.rr(x1 - 2, yy + 3, x2 + 2, yy + h + 7, 16, ("#000000", 0.30 * a))
                self.rr(x1, yy, x2, yy + h, 14, (mix(C_CARD, "#2a2050", 0.45), 0.97 * a), (mix(C_CARD, c, 0.55), a))
                self.rr(x1 + 1, yy + 12, x1 + 4, yy + h - 12, 2, (c, a))
                if t["sticky"]:   # «загрузчик» из трёх точек
                    for k in range(3):
                        ph = (math.sin(now * 6 - k * 0.8) + 1) / 2
                        self.ell(x1 + 24 + k * 11, yy + h / 2, 2.5 + ph * 1.5, (mix(C_CARD, c, 0.35 + 0.65 * ph), a))
                    tx = x1 + 66
                else:
                    self.glow(x1 + 26, yy + h / 2, 15, c, 0.30 * a, 1.8)
                    self.ell(x1 + 26, yy + h / 2, 5, (c, a))
                    tx = x1 + 46
                cy = yy + (18 if t["sub"] else h / 2)
                self.txt(tx, cy, self.fit(t["msg"], 10, x2 - tx - 14, True), 10, (INK, a), True, "w")
                if t["sub"]:
                    self.txt(tx, yy + 40, self.fit(t["sub"], 9, x2 - tx - 14), 9, (INK3, a), False, "w")
                self.reg(x1, yy, x2, yy + h, lambda tt=t: self._dismiss_toast(tt), "toast_" + str(id(t)))
            y += h + 10
        self.toasts = keep

    # ═══════════════════════════ взаимодействие ═══════════════════════════
    def _goto(self, k):
        if k == self.page:
            return
        self._snap("page")
        self.page = k
        if k == "set":
            self._refresh_set_info()
        self.dirty = True

    def _hit(self, e):
        x, y = e.x / self.S, e.y / self.S
        for x1, y1, x2, y2, cb, key in reversed(self.regions):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key, cb
        return None, None

    def _motion(self, e):
        key, _ = self._hit(e)
        self.cv.config(cursor="hand2" if key else "")
        self._set_hover(key)

    def _set_hover(self, key):
        if key != self.hover:
            self.hover = key
            self.dirty = True

    def _press(self, e):
        self.pressed, _ = self._hit(e)
        self.dirty = True

    def _release(self, e):
        key, cb = self._hit(e)
        was, self.pressed = self.pressed, None
        self.dirty = True
        if key and key == was:
            cb()

    def log(self, msg: str):
        self.q.put(("log", msg))

    def _pump(self):
        try:
            while True:
                kind, *a = self.q.get_nowait()
                if kind == "log":
                    self.logs.append(a[0])
                    self.logbox.config(state="normal")
                    self.logbox.insert("end", a[0] + "\n")
                    self.logbox.see("end")
                    self.logbox.config(state="disabled")
                elif kind == "state":
                    self._snap()
                    self.on, self.status, self.busy = a[0], a[1], (a[2] if len(a) > 2 else False)
                elif kind == "prog":
                    self.prog = a[0]
                    if a[0][2]:
                        self.status = f"Проверяю: {a[0][2]} (лучший результат {a[0][3]})"
                elif kind == "best":
                    self.best, self.score = a[0], (a[1], a[2])
                elif kind == "toast":
                    self._add_toast(*a)
                elif kind == "toast_end":
                    self._end_toast(*a)
                elif kind == "dlbusy":
                    self.dl_busy = a[0]
                elif kind == "diag_item":
                    self.diag_items.append(a[0])
                    self.diag_off = max(0, len(self.diag_items) - 7)   # автопрокрутка к последним
                elif kind == "diag_stage":
                    self.diag_stage = a[0]
                elif kind == "diag_done":
                    self.diag_running, self.diag_verdict, self.diag_off = False, a[0], 0
                    v = a[0]
                    self.toast_end("diag", v.title, {"ok": "ok", "warn": "warn", "fail": "error"}.get(v.status, "info"),
                                   (v.detail[:95] + "…") if len(v.detail) > 95 else v.detail, 8.0)
                    try:
                        Path(engine.DATA_DIR).mkdir(parents=True, exist_ok=True)
                        (engine.DATA_DIR / "diagnostics.txt").write_text(
                            diag.report_text(self.diag_items, v), encoding="utf-8")
                    except OSError:
                        pass
                    self.log("Диагностика: " + v.title)
                elif kind == "diag_auto":
                    self._goto("diag")
                    self._run_diag()
                self.dirty = True
        except queue.Empty:
            pass
        self.after(40, self._pump)

    def _clear_log(self):
        self.logs.clear()
        self.logbox.config(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.config(state="disabled")
        self.dirty = True

    def _watch(self):
        if self.on and not self.busy and not self.winws.alive:
            self.log("winws.exe завершился. Нажмите «Включить» снова.")
            self._snap()
            self.on, self.status = False, "Отключено"
        self.dirty = True
        self.after(3000, self._watch)

    # ── главная кнопка ──────────────────────────────────────────────────
    def _toggle(self):
        if self.busy:
            self.cancel.set()
            return
        self._snap()
        if self.on:
            self.winws.stop()
            if self.tg_auto:
                self.proxy.stop()
            self.on, self.status = False, "Отключено"
            self.log("Обход отключён.")
            self.dirty = True
            return
        self.cancel.clear()
        self.on, self.busy, self.status, self.prog = True, True, "Подготовка…", (0, 1, "", 0)
        self._tw["prog"] = [0.0, 0.0, 5, 0.002]
        self.dirty = True
        threading.Thread(target=self._enable, daemon=True).start()

    def _enable(self):
        S = self.q.put
        try:
            if engine.zapret_service_running():
                self.log("Служба zapret уже запущена. Удалите её (service.bat → Remove Services) и повторите.")
                S(("state", False, "Конфликт со службой zapret"))
                return
            if not engine.bundle_ready():
                S(("state", True, "Скачиваю zapret…", True))
                self.toast("Началась загрузка zapret…", "info", key="dl", sticky=True, sub="GitHub: Flowseal/zapret-discord-youtube")
                d = engine.download_bundle(self.log, lambda p: self.toast(f"Скачиваю zapret… {p}%", "info", key="dl", sticky=True))
                engine.set_zapret_dir(d)
                self.cfg["zapret_dir"] = str(d)
                engine.save_config(self.cfg)
                self.toast_end("dl", "zapret загружен", "ok", str(d)[-60:])
            miss = engine.missing_files()
            if miss:
                self.log("Не хватает файлов zapret: " + ", ".join(miss) + " (удалены антивирусом?)")
                self.toast("Не хватает файлов zapret", "error", sub=", ".join(m.split("/")[-1] for m in miss)[:80], dur=7)
                S(("state", False, "Файлы zapret повреждены"))
                S(("diag_auto",))
                return
            engine.ensure_user_lists()
            engine.enable_tcp_timestamps()
            strategies = engine.load_strategies()
            if not strategies:
                raise RuntimeError("Стратегии не найдены в папке zapret")
            best, sc, total = None, 0, 0
            if self.choice != "auto":
                s = next((x for x in strategies if x.name == self.choice), None)
                if s and self.winws.start(s):
                    best, total = s, len(engine.load_targets())
                    sc = engine.check_targets(engine.load_targets())
                    self.log(f"Запущена стратегия: {s.name} ({sc}/{total})")
                elif s:
                    self.log(f"{s.name}: не запустилась — {self.winws.last_error}")
                    self.winws.fatal = self.winws.last_error
            else:
                best, sc, total = engine.auto_select(
                    self.winws, strategies, self.cfg.get("best"), self.log,
                    lambda i, n, name, s: S(("prog", (i, n, name, s))), self.cancel)
            if best is None:
                if self.cancel.is_set():
                    self.log("Отменено.")
                    S(("state", False, "Отключено"))
                elif self.winws.fatal:
                    self.log("Причина: " + self.winws.fatal)
                    self.toast("winws.exe не запускается", "error", sub=self.winws.fatal[:95], dur=8)
                    S(("state", False, "winws.exe не запускается"))
                    S(("diag_auto",))
                else:
                    self.log("Ни одна стратегия не помогла. Включите Secure DNS в браузере, "
                             "отключите VPN/другие обходчики, проверьте антивирус.")
                    S(("state", False, "Не удалось подобрать стратегию"))
                return
            self.cfg["best"] = best.name
            engine.save_config(self.cfg)
            S(("best", best.name, sc, total))
            if self.tg_auto:
                if self.proxy.start():
                    self.log(f"Прокси Telegram запущен на {self.proxy.host}:{self.proxy.port}")
                else:
                    self.log(self.proxy.error or "Не удалось запустить прокси")
            S(("state", True, f"Работает · {best.name}"))
        except Exception as e:  # noqa: BLE001
            self.log(f"Ошибка: {e}")
            self.winws.stop()
            S(("state", False, "Ошибка"))

    # ── Telegram ────────────────────────────────────────────────────────
    def _tg_toggle(self):
        self._snap()
        if self.proxy.running:
            self.proxy.stop()
            self.log("Прокси Telegram остановлен.")
        else:
            if self.proxy.start():
                self.log(f"Прокси Telegram запущен на {self.proxy.host}:{self.proxy.port}")
            else:
                self.log(self.proxy.error or "Не удалось запустить прокси")
        self.dirty = True

    def _open_tg(self):
        if not self.proxy.running:
            self._tg_toggle()
        try:
            os.startfile(self.proxy.link)
        except OSError:
            self._copy_link()

    def _copy_link(self):
        self.clipboard_clear()
        self.clipboard_append(self.proxy.link)
        self.log("Ссылка скопирована: " + self.proxy.link)

    def _toggle_tg_auto(self):
        self.tg_auto = not self.tg_auto
        self.cfg["tg_auto"] = self.tg_auto
        engine.save_config(self.cfg)
        self.dirty = True

    # ── настройки ───────────────────────────────────────────────────────
    def _pick_dir(self):
        d = filedialog.askdirectory(title="Папка zapret-discord-youtube (где лежит bin\\winws.exe)")
        if not d:
            return
        p = Path(d)
        if not engine.is_zapret_dir(p):
            self.log(f"В папке {p} не найдены bin\\winws.exe и general*.bat")
        else:
            engine.set_zapret_dir(p)
            self.cfg["zapret_dir"] = str(p)
            engine.save_config(self.cfg)
            self.log(f"Папка zapret: {p}")
            self._refresh_set_info()
        self.dirty = True

    def _download(self):
        if self.busy or self.dl_busy:
            return
        self.dl_busy = True
        self.toast("Началась загрузка свежего zapret…", "info", key="dl", sticky=True,
                   sub="GitHub: Flowseal/zapret-discord-youtube")
        self.dirty = True

        def work():
            try:
                self.winws.stop()
                d = engine.download_bundle(self.log, lambda p: self.toast(
                    f"Скачиваю zapret… {p}%", "info", key="dl", sticky=True, sub="GitHub: Flowseal/zapret-discord-youtube"))
                engine.set_zapret_dir(d)
                self.cfg["zapret_dir"] = str(d)
                engine.save_config(self.cfg)
                engine.ensure_user_lists()
                self._refresh_set_info()
                self.toast_end("dl", "zapret обновлён", "ok", f"Стратегий: {self.set_info['n']}", 5)
            except Exception as e:  # noqa: BLE001
                self.log(f"Ошибка загрузки: {e}")
                self.toast_end("dl", "Не удалось скачать zapret", "error", str(e)[:90], 8)
            self.q.put(("dlbusy", False))
            self.q.put(("state", False, "Отключено"))
        threading.Thread(target=work, daemon=True).start()

    def _strategy_menu(self):
        m = tk.Menu(self, tearoff=0, bg=C_CARD, fg=INK2, activebackground=ACCENT_FILL,
                    activeforeground="#fff", bd=0, font=(FONT, 10))

        def pick(v):
            self.choice = v
            self.cfg["choice"] = v
            engine.save_config(self.cfg)
            self.dirty = True
        m.add_command(label="Авто (лучшая)", command=lambda: pick("auto"))
        m.add_separator()
        if engine.bundle_ready():
            for s in engine.load_strategies():
                m.add_command(label=s.name, command=lambda n=s.name: pick(n))
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _close(self):
        self.cancel.set()
        self.winws.stop()
        self.proxy.stop()
        self.destroy()


def main():
    if os.name != "nt":
        sys.exit("Приложение работает только в Windows.")
    if not is_admin() and not os.environ.get("ZOC_NOADMIN"):
        relaunch_as_admin()
        return
    App().mainloop()


if __name__ == "__main__":
    main()
