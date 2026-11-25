"""Генерация иконки Zapret OneClick: кнопка питания («один клик») с буквой Z («запрет»).
Цвета — как в приложении (фиолетовый → фуксия на тёмном фоне). Запуск: python make_icon.py"""
import math
import os
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

N, SS = 1024, 2
W = N * SS
VIOLET, FUCHSIA, SOFT = (139, 92, 246), (232, 121, 249), (196, 181, 253)
BG_OUT, BG_IN = (9, 7, 15), (38, 24, 70)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def radial(size, c_in, c_out, power=1.0):
    g = Image.radial_gradient("L").resize((size, size), Image.BICUBIC)   # 0 в центре → 255 по краю
    g = g.point([int(((v / 255) ** power) * 255) for v in range(256)])
    return Image.composite(Image.new("RGB", (size, size), c_out), Image.new("RGB", (size, size), c_in), g)


def diag_gradient(size, c1, c2):
    """Диагональный градиент c1 (сверху-слева) → c2 (снизу-справа)."""
    g = Image.linear_gradient("L").resize((size, size), Image.BILINEAR)           # сверху вниз
    h = g.rotate(90).resize((size, size), Image.BILINEAR)                          # слева направо
    mask = ImageChops.add(g, h, 2)
    return Image.composite(Image.new("RGB", (size, size), c2), Image.new("RGB", (size, size), c1), mask)


def make() -> Image.Image:
    s = SS
    # фон: скруглённый квадрат с радиальным градиентом
    bg = radial(W, BG_IN, BG_OUT, 0.9).convert("RGBA")
    m = Image.new("L", (W, W), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, W - 1, W - 1], radius=int(W * 0.225), fill=255)
    bg.putalpha(m)
    im = bg

    # неоновое свечение за кольцом
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    c = W / 2
    R = 318 * s
    wd = 86 * s
    gd.arc([c - R, c - R + 20 * s, c + R, c + R + 20 * s], start=-60, end=240, fill=VIOLET + (255,), width=wd + 30 * s)
    glow = glow.filter(ImageFilter.GaussianBlur(46 * s))
    glow.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.75)))
    im = Image.alpha_composite(im, glow)

    # кольцо кнопки питания с градиентом (маска → градиентная заливка)
    ring = Image.new("L", (W, W), 0)
    rd = ImageDraw.Draw(ring)
    cy = c + 20 * s
    rb = R + wd / 2
    rd.arc([c - rb, cy - rb, c + rb, cy + rb], start=-60, end=240, fill=255, width=wd)
    for a in (-60, 240):                                                       # круглые концы
        ex, ey = c + R * math.cos(math.radians(a)), cy + R * math.sin(math.radians(a))
        rd.ellipse([ex - wd / 2, ey - wd / 2, ex + wd / 2, ey + wd / 2], fill=255)
    top = cy - R - 70 * s                                                      # вертикальная черта «питания»
    rd.rounded_rectangle([c - wd / 2, top, c + wd / 2, cy - R * 0.50], radius=int(wd / 2), fill=255)
    grad = diag_gradient(W, SOFT, FUCHSIA)
    grad2 = diag_gradient(W, VIOLET, FUCHSIA)
    col = Image.composite(grad, grad2, ring.point(lambda v: 0))                # базовая заливка
    col = diag_gradient(W, (167, 139, 250), (232, 121, 249))
    layer = col.convert("RGBA")
    layer.putalpha(ring)
    im = Image.alpha_composite(im, layer)

    # буква Z внутри — белая, с лёгким градиентом
    font = None
    for name in ("seguibl.ttf", "ariblk.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(f"C:\\Windows\\Fonts\\{name}", 380 * s)
            break
        except OSError:
            continue
    zmask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(zmask).text((c, cy + 34 * s), "Z", font=font, fill=255, anchor="mm")
    zcol = Image.composite(Image.new("RGB", (W, W), (255, 255, 255)),
                           Image.new("RGB", (W, W), SOFT), diag_gradient(W, (0, 0, 0), (255, 255, 255)).convert("L").point(lambda v: 255 - v // 3))
    zl = zcol.convert("RGBA")
    zl.putalpha(zmask)
    im = Image.alpha_composite(im, zl)

    # тонкая светлая кромка сверху (стекло)
    edge = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([3 * s, 3 * s, W - 3 * s, W - 3 * s], radius=int(W * 0.225),
                                           outline=(255, 255, 255, 34), width=4 * s)
    im = Image.alpha_composite(im, edge)
    return im.resize((N, N), Image.LANCZOS)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    img = make()
    img.save(os.path.join(here, "icon.png"))
    img.save(os.path.join(here, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon.png / icon.ico готовы")
