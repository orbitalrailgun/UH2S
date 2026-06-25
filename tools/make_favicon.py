#!/usr/bin/env python3
"""Генерация favicon.ico (и favicon.svg) для Universal Harvester — чистый stdlib.

Иконка: тёмный закруглённый квадрат с циан-рамкой, неоновая «воронка-харвестер»
и три точки-данные, падающие в неё. Цвета совпадают с тёмной темой приложения.
Запуск:  python tools/make_favicon.py
"""

import struct

# палитра (RGB)
BG = (17, 24, 39)        # #111827
BORDER = (6, 182, 212)   # #06B6D4
GLYPH_TOP = (34, 211, 238)   # #22D3EE
GLYPH_BOT = (6, 182, 212)    # #06B6D4

# координаты в пространстве 64x64
FUNNEL = [(12, 22), (52, 22), (37, 40), (37, 52), (27, 52), (27, 40)]
DOTS = [(22, 15, 3.2), (32, 12, 3.2), (42, 15, 3.2)]


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _in_round_rect(px, py, x0, y0, x1, y1, r):
    if px < x0 or px > x1 or py < y0 or py > y1:
        return False
    nx = _clamp(px, x0 + r, x1 - r)
    ny = _clamp(py, y0 + r, y1 - r)
    return (px - nx) ** 2 + (py - ny) ** 2 <= r * r


def _in_polygon(px, py, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _sample(fx, fy):
    """Цвет (r,g,b,a) точки в пространстве 64x64."""
    # вне внешнего скруглённого квадрата — прозрачно
    if not _in_round_rect(fx, fy, 2, 2, 62, 62, 14):
        return (0, 0, 0, 0)
    # глиф (воронка/точки) поверх фона
    in_glyph = _in_polygon(fx, fy, FUNNEL)
    if not in_glyph:
        for cx, cy, r in DOTS:
            if (fx - cx) ** 2 + (fy - cy) ** 2 <= r * r:
                in_glyph = True
                break
    if in_glyph:
        t = _clamp((fy - 12) / 40.0, 0.0, 1.0)  # вертикальный градиент
        r = round(GLYPH_TOP[0] + (GLYPH_BOT[0] - GLYPH_TOP[0]) * t)
        g = round(GLYPH_TOP[1] + (GLYPH_BOT[1] - GLYPH_TOP[1]) * t)
        b = round(GLYPH_TOP[2] + (GLYPH_BOT[2] - GLYPH_TOP[2]) * t)
        return (r, g, b, 255)
    # внутренний квадрат — фон; кольцо между внешним и внутренним — рамка
    if _in_round_rect(fx, fy, 4, 4, 60, 60, 12):
        return (*BG, 255)
    return (*BORDER, 255)


def _render(size, ss=4):
    """RGBA-пиксели size×size с суперсэмплингом ss×ss (сглаживание)."""
    pixels = []
    scale = 64.0 / size
    inv = 1.0 / (ss * ss)
    for y in range(size):
        row = []
        for x in range(size):
            ar = ag = ab = aa = 0
            for sy in range(ss):
                for sx in range(ss):
                    fx = (x + (sx + 0.5) / ss) * scale
                    fy = (y + (sy + 0.5) / ss) * scale
                    r, g, b, a = _sample(fx, fy)
                    ar += r * a; ag += g * a; ab += b * a; aa += a
            if aa > 0:
                row.append((round(ar / aa), round(ag / aa), round(ab / aa), round(aa * inv)))
            else:
                row.append((0, 0, 0, 0))
        pixels.append(row)
    return pixels


def _bmp_for_ico(pixels):
    """BITMAPINFOHEADER + XOR(BGRA, снизу вверх) + AND-маска (нули) для одной картинки ICO."""
    h = len(pixels)
    w = len(pixels[0])
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for (r, g, b, a) in pixels[y]:
            xor += bytes((b, g, r, a))
    and_row = ((w + 31) // 32) * 4
    mask = bytes(and_row * h)
    return header + bytes(xor) + mask


def write_ico(path, sizes=(16, 32, 48)):
    images = [_bmp_for_ico(_render(s)) for s in sizes]
    count = len(images)
    out = bytearray(struct.pack("<HHH", 0, 1, count))
    offset = 6 + 16 * count
    for s, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s & 0xFF, s & 0xFF, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for data in images:
        out += data
    with open(path, "wb") as f:
        f.write(out)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#22D3EE"/>
      <stop offset="1" stop-color="#06B6D4"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#111827" stroke="#06B6D4" stroke-width="2"/>
  <circle cx="22" cy="15" r="3.2" fill="#22D3EE"/>
  <circle cx="32" cy="12" r="3.2" fill="#22D3EE"/>
  <circle cx="42" cy="15" r="3.2" fill="#22D3EE"/>
  <path d="M12 22 H52 L37 40 V52 H27 V40 Z" fill="url(#g)"/>
</svg>
"""


def write_svg(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(SVG)


if __name__ == "__main__":
    write_ico("favicon.ico")
    write_svg("favicon.svg")
    print("written favicon.ico and favicon.svg")
