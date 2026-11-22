"""Генерация иконки (красный знак «СТОП» с рукой, тёмная версия). Запуск: python make_icon.py"""
import os
from PIL import Image, ImageDraw, ImageFont

N, SS = 1024, 2          # итоговый размер и суперсэмплинг
W = N * SS
RED_DARK, RED_MID, RED_LIGHT = (96, 14, 18), (138, 22, 26), (168, 32, 34)
WHITE = (250, 244, 242)


def circle(draw, r, fill=None, outline=None, width=0, c=W / 2):
    draw.ellipse([c - r, c - r, c + r, c + r], fill=fill, outline=outline, width=width)


def make() -> Image.Image:
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = SS

    # диск с мягким радиальным градиентом (тёмно-красный)
    steps = 60
    for i in range(steps):
        t = i / (steps - 1)
        r = (W / 2 - 6 * s) * (1 - t * 0.85)
        col = tuple(int(RED_DARK[k] + (RED_LIGHT[k] - RED_DARK[k]) * (t ** 1.5)) for k in range(3))
        circle(d, r, fill=col + (255,))
    # внутренняя тонкая тень и белое кольцо
    circle(d, 470 * s, outline=WHITE + (255,), width=26 * s)

    # рука (белая)
    cx = W / 2
    fw = 74 * s
    gap = 9 * s
    left = cx - (4 * fw + 3 * gap) / 2 - 10 * s
    tops = [352, 262, 222, 276]                      # мизинец, безымянный, средний, указательный
    for i, top in enumerate(tops):
        x = left + i * (fw + gap)
        d.rounded_rectangle([x, top * s, x + fw, 600 * s], radius=fw // 2, fill=WHITE)
    # ладонь
    px1, px2 = left - 40 * s, left + 4 * fw + 3 * gap + 40 * s
    d.rounded_rectangle([px1, 470 * s, px2, 800 * s], radius=96 * s, fill=WHITE)
    # большой палец: толстая линия с круглыми концами + плавное соединение с ладонью
    a, b = (px2 - 70 * s, 650 * s), (px2 + 82 * s, 478 * s)
    d.line([a, b], fill=WHITE, width=92 * s)
    for p in (a, b):
        d.ellipse([p[0] - 46 * s, p[1] - 46 * s, p[0] + 46 * s, p[1] + 46 * s], fill=WHITE)
    d.polygon([(px2 - 110 * s, 520 * s), (px2 - 20 * s, 560 * s), (px2 - 20 * s, 700 * s)], fill=WHITE)

    # надпись STOP
    font = None
    for name in ("ariblk.ttf", "impact.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(f"C:\\Windows\\Fonts\\{name}", 92 * s)
            break
        except OSError:
            continue
    d.text(((px1 + px2) / 2 - 18 * s, 665 * s), "STOP", font=font, fill=RED_MID + (255,), anchor="mm")

    return im.resize((N, N), Image.LANCZOS)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    img = make()
    img.save(os.path.join(here, "icon.png"))
    img.save(os.path.join(here, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon.png / icon.ico готовы")
