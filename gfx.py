"""Сглаженная 2D-графика на Pillow: спрайты с суперсэмплингом, мягкие свечения, твины.

Все координаты в «логических» пикселях; множитель масштаба Windows (DPI) self.S применяется внутри.
"""
from __future__ import annotations

import math
from typing import Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

FONT_DIR = r"C:\Windows\Fonts"
FONT_FILES = {"reg": "segoeui.ttf", "bold": "seguisb.ttf", "mono": "consola.ttf"}
LANCZOS = Image.LANCZOS


def hex2rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    A, B = hex2rgb(a), hex2rgb(b)
    return "#%02x%02x%02x" % tuple(round(A[i] + (B[i] - A[i]) * t) for i in range(3))


def col(c):
    """'#rrggbb' | ('#rrggbb', alpha 0..1) | None -> RGBA-кортеж | None"""
    if c is None or c == "":
        return None
    if isinstance(c, tuple):
        r, g, b = hex2rgb(c[0])
        return r, g, b, int(max(0, min(1, c[1])) * 255)
    r, g, b = hex2rgb(c)
    return r, g, b, 255


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class GfxMixin:
    # ── инициализация ────────────────────────────────────────────────────
    def gfx_init(self, scale: float):
        self.S = scale
        self._cache: dict = {}
        self._fonts: dict = {}
        self._tw: dict = {}
        self.oy = 0.0
        self.fr: Image.Image = None
        self._radial = ImageOps.invert(Image.radial_gradient("L"))  # центр 255 → край 0

    def sc(self, v: float) -> int:
        return int(round(v * self.S))

    # ── твины (плавные значения) ─────────────────────────────────────────
    def tw_get(self, key, target: float, speed: float = 14.0, tol: float = 0.002) -> float:
        v = self._tw.get(key)
        if v is None:
            self._tw[key] = [target, target, speed, tol]
            return target
        v[1], v[2], v[3] = target, speed, tol
        return v[0]

    def tw_color(self, key, target_hex: str, speed: float = 8.0) -> str:
        rgb = hex2rgb(target_hex)
        out = [self.tw_get((key, i), float(rgb[i]), speed, 0.4) for i in range(3)]
        return "#%02x%02x%02x" % tuple(int(round(max(0, min(255, c)))) for c in out)

    def tw_step(self, dt: float) -> bool:
        moving = False
        for v in self._tw.values():
            d = v[1] - v[0]
            if abs(d) < v[3]:
                v[0] = v[1]
            else:
                v[0] += d * (1 - math.exp(-dt * v[2]))
                moving = True
        return moving

    # ── кэш спрайтов ─────────────────────────────────────────────────────
    def _cached(self, key, fn):
        v = self._cache.get(key)
        if v is None:
            if len(self._cache) > 2500:
                self._cache.clear()
            v = self._cache[key] = fn()
        return v

    @staticmethod
    def _ss(w: int, h: int) -> int:
        a = w * h
        return 4 if a < 60000 else 3 if a < 300000 else 2

    def blit(self, sp: Image.Image, px: int, py: int):
        self.fr.paste(sp, (px, py), sp)

    # ── примитивы ────────────────────────────────────────────────────────
    def rr(self, x1, y1, x2, y2, r, fill=None, outline=None, width=1):
        px, py = self.sc(x1), self.sc(y1 + self.oy)
        w, h = self.sc(x2) - px, self.sc(y2 + self.oy) - py
        if w < 2 or h < 2:
            return
        rp, ow = self.sc(r), (max(1, round(width * self.S)) if outline else 0)
        f, o = col(fill), col(outline)

        def make():
            ss = self._ss(w, h)
            im = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
            ImageDraw.Draw(im).rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=rp * ss,
                                                 fill=f, outline=o, width=ow * ss)
            return im.resize((w, h), LANCZOS)
        self.blit(self._cached(("rr", w, h, rp, f, o, ow), make), px, py)

    def ell(self, cx, cy, r, fill, outline=None, width=1):
        d = max(2, self.sc(2 * r))
        px, py = self.sc(cx) - d // 2, self.sc(cy + self.oy) - d // 2
        f, o = col(fill), col(outline)
        ow = max(1, round(width * self.S)) if outline else 0

        def make():
            ss = 4 if d < 200 else 3
            im = Image.new("RGBA", (d * ss, d * ss), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse([0, 0, d * ss - 1, d * ss - 1], fill=f, outline=o, width=ow * ss)
            return im.resize((d, d), LANCZOS)
        self.blit(self._cached(("el", d, f, o, ow), make), px, py)

    def hline(self, x1, x2, y, color):
        px, py = self.sc(x1), self.sc(y + self.oy)
        w = self.sc(x2) - px
        if w < 1:
            return
        c = col(color)
        sp = self._cached(("hl", w, c), lambda: Image.new("RGBA", (w, 1), c))
        self.blit(sp, px, py)

    # ── текст ────────────────────────────────────────────────────────────
    def _font(self, size_pt: float, bold=False, mono=False) -> ImageFont.FreeTypeFont:
        px = max(6, round(size_pt * 96 / 72 * self.S))
        key = (px, bold, mono)
        f = self._fonts.get(key)
        if f is None:
            name = FONT_FILES["mono" if mono else ("bold" if bold else "reg")]
            try:
                f = ImageFont.truetype(f"{FONT_DIR}\\{name}", px)
            except OSError:
                f = ImageFont.load_default()
            self._fonts[key] = f
        return f

    def measure(self, s: str, size=10, bold=False, mono=False) -> float:
        return self._cached(("ms", s, size, bold, mono, self.S),
                            lambda: self._font(size, bold, mono).getlength(s) / self.S)

    def fit(self, s, size, maxw, bold=False, mono=False):
        if self.measure(s, size, bold, mono) <= maxw:
            return s
        while len(s) > 4 and self.measure(s + "…", size, bold, mono) > maxw:
            s = s[:-2]
        return s + "…"

    def txt(self, x, y, s, size=10, color="#ffffff", bold=False, anchor="nw", mono=False):
        c = col(color)
        lines = s.split("\n")

        def sprite(ln):
            def make():
                f = self._font(size, bold, mono)
                asc, desc = f.getmetrics()
                w = int(math.ceil(f.getlength(ln))) + 2
                im = Image.new("RGBA", (w, asc + desc + 2), (c[0], c[1], c[2], 0))
                ImageDraw.Draw(im).text((0, 0), ln, font=f, fill=c, anchor="la")
                return im
            return self._cached(("tx", ln, size, bold, mono, c, self.S), make)

        sps = [sprite(ln) for ln in lines]
        lh = sps[0].height - 2
        tot_h = lh * 1.25 * len(lines)
        wmax = 0
        for i, sp in enumerate(sps):
            w = sp.width
            wmax = max(wmax, w)
            px, py = self.sc(x), self.sc(y + self.oy)
            if anchor == "center":
                px, py = px - w // 2, py - int(tot_h // 2)
            elif anchor == "w":
                py -= int(tot_h // 2)
            elif anchor == "e":
                px -= w
            self.blit(sp, px, py + int(i * lh * 1.25))
        return wmax / self.S

    # ── мягкие свечения / фон ────────────────────────────────────────────
    def _radial_mask(self, d: int, power: float, strength: float) -> Image.Image:
        q = round(strength * 40) / 40
        key = ("rm", d, power, q)

        def make():
            base = self._radial.resize((d, d), Image.BICUBIC)
            lut = [min(255, int(((v / 255) ** power) * 255 * q)) for v in range(256)]
            return base.point(lut)
        return self._cached(key, make)

    def glow(self, cx, cy, radius, color_hex, strength, power=2.1):
        """Мягкое радиальное свечение вокруг точки (логические координаты)."""
        d = self.sc(2 * radius)
        mask = self._radial_mask(d, power, strength)
        px, py = self.sc(cx) - d // 2, self.sc(cy + self.oy) - d // 2
        self.fr.paste(hex2rgb(color_hex), (px, py, px + d, py + d), mask)

    def make_bg(self, pw: int, ph: int, base_hex: str, blobs) -> Image.Image:
        """Aurora-фон с дизерингом (убирает полосы градиента)."""
        bg = Image.new("RGB", (pw, ph), hex2rgb(base_hex))
        for (fx, fy, fr, color, alpha) in blobs:
            R = int(max(pw, ph) * fr)
            mask = self._radial.resize((2 * R, 2 * R), Image.BICUBIC).point(
                [min(255, int(((v / 255) ** 1.7) * 255 * alpha)) for v in range(256)])
            cx, cy = int(pw * fx), int(ph * fy)
            bg.paste(hex2rgb(color), (cx - R, cy - R, cx + R, cy + R), mask)
        noise = Image.effect_noise((pw, ph), 2.2).convert("RGB")
        return ImageChops.add(bg, noise, 1, -128)
