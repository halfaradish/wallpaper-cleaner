"""生成程序图标（纯标准库，不依赖 Pillow）

    python packaging/make_icon.py                 # 生成 packaging/icon.ico
    python packaging/make_icon.py --preview       # 额外输出预览图，方便肉眼检查
    python packaging/make_icon.py --design c      # 换造型

图标是画出来的，不是往仓库里塞一个二进制文件：调色或改造型只改这个脚本，构建时
由 spec 调用生成，和 version_info.txt 是同一套做法。

Windows 任务栏、资源管理器、窗口标题栏都取 exe 里嵌的这份图标。
"""

import argparse
import os
import struct
import zlib

# 与面板同一套配色，保证图标和界面是一套视觉
ACCENT = (106, 169, 224)      # --accent
ACCENT_DEEP = (28, 56, 84)    # 比 --accent-dim 更深，小尺寸下对比更够
INK = (13, 16, 20)            # --bg

ICO_SIZES = (16, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4


# ------------------------------------------------------------------ 画布

class Canvas:
    """带超采样的 RGBA 画布

    先在高倍率下按硬边绘制，最后降采样得到抗锯齿边缘 —— 比逐像素算覆盖率简单，
    效果也够好。
    """

    def __init__(self, size):
        self.size = size
        self.px = bytearray(size * size * 4)

    # --- 像素写入 ---

    def _put(self, x, y, rgba):
        sa = rgba[3]
        if sa == 0:
            return
        i = (y * self.size + x) * 4
        if sa == 255:
            self.px[i:i + 4] = bytes(rgba)
            return
        a = sa / 255.0
        inv = 1.0 - a
        self.px[i] = int(rgba[0] * a + self.px[i] * inv)
        self.px[i + 1] = int(rgba[1] * a + self.px[i + 1] * inv)
        self.px[i + 2] = int(rgba[2] * a + self.px[i + 2] * inv)
        self.px[i + 3] = min(255, int(sa + self.px[i + 3] * inv))

    def _fill(self, x0, y0, x1, y1, test, color):
        """在包围盒里逐像素判断，命中就上色

        test 收原始像素坐标；color 收归一化坐标（0..1），这样渐变不用关心画布多大。
        """
        lo_x, hi_x = max(0, int(x0)), min(self.size - 1, int(x1) + 1)
        lo_y, hi_y = max(0, int(y0)), min(self.size - 1, int(y1) + 1)
        scale = 1.0 / self.size
        for y in range(lo_y, hi_y + 1):
            py = y + 0.5
            for x in range(lo_x, hi_x + 1):
                px = x + 0.5
                if test(px, py):
                    self._put(x, y, color(px * scale, py * scale))

    # --- 绘图原语 ---

    def round_rect(self, x0, y0, x1, y1, radius, color):
        """圆角矩形；x1/y1 是外边界"""
        color = _color_fn(color)

        def test(px, py):
            if px < x0 or px > x1 or py < y0 or py > y1:
                return False
            # 把点夹到「圆角圆心」构成的矩形上，距离超过半径就在圆角外
            cx = min(max(px, x0 + radius), x1 - radius)
            cy = min(max(py, y0 + radius), y1 - radius)
            return (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius

        self._fill(x0, y0, x1, y1, test, color)

    def disc(self, cx, cy, r, color):
        color = _color_fn(color)
        self._fill(cx - r, cy - r, cx + r, cy + r,
                   lambda px, py: (px - cx) ** 2 + (py - cy) ** 2 <= r * r, color)

    def poly(self, points, color):
        """任意多边形，射线法判定内外"""
        color = _color_fn(color)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]

        def test(px, py):
            inside = False
            j = len(points) - 1
            for i in range(len(points)):
                xi, yi = points[i]
                xj, yj = points[j]
                if (yi > py) != (yj > py):
                    if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                        inside = not inside
                j = i
            return inside

        self._fill(min(xs), min(ys), max(xs), max(ys), test, color)

    def segment(self, x0, y0, x1, y1, width, color):
        """带宽度、圆头的线段"""
        color = _color_fn(color)
        half = width / 2.0
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy or 1e-9

        def test(px, py):
            t = ((px - x0) * dx + (py - y0) * dy) / length_sq
            t = min(1.0, max(0.0, t))
            nx, ny = x0 + t * dx, y0 + t * dy
            return (px - nx) ** 2 + (py - ny) ** 2 <= half * half

        self._fill(min(x0, x1) - width, min(y0, y1) - width,
                   max(x0, x1) + width, max(y0, y1) + width, test, color)

    def star4(self, cx, cy, r, color, sharpness=0.5):
        """四角星（闪光）

        边界半径按角度取 r / (|cos|^n + |sin|^n)^(1/n)：n<1 时 45° 方向收得很紧，
        四个轴向形成尖角。
        """
        color = _color_fn(color)
        n = sharpness

        def test(px, py):
            dx, dy = px - cx, py - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist == 0:
                return True
            ax, ay = abs(dx) / dist, abs(dy) / dist
            return dist <= r / ((ax ** n + ay ** n) ** (1.0 / n))

        self._fill(cx - r, cy - r, cx + r, cy + r, test, color)

    # --- 输出 ---

    def downsample(self, factor):
        out_size = self.size // factor
        out = bytearray(out_size * out_size * 4)
        area = factor * factor
        for y in range(out_size):
            for x in range(out_size):
                r = g = b = a = 0
                for sy in range(factor):
                    row = (y * factor + sy) * self.size
                    for sx in range(factor):
                        i = (row + x * factor + sx) * 4
                        pa = self.px[i + 3]
                        # 在预乘空间里累加，否则半透明边缘会把底色混成黑边
                        r += self.px[i] * pa
                        g += self.px[i + 1] * pa
                        b += self.px[i + 2] * pa
                        a += pa
                o = (y * out_size + x) * 4
                if a:
                    out[o] = min(255, r // a)
                    out[o + 1] = min(255, g // a)
                    out[o + 2] = min(255, b // a)
                out[o + 3] = a // area
        return out


def _color_fn(color):
    if callable(color):
        return color
    c = tuple(color) + (255,) * (4 - len(color))
    return lambda _x, _y: c


def vertical_gradient(top, bottom):
    """按 y（0..1）线性插值的竖向渐变"""
    def fn(_x, y):
        return tuple(int(top[i] + (bottom[i] - top[i]) * y) for i in range(3)) + (255,)
    return fn


# ------------------------------------------------------------------ 造型

def draw_brand(size):
    """A. 延续面板左上角那个标记：圆角方块 + 渐变，右上角一点闪光表示「已清理」"""
    c = Canvas(size)
    s = float(size)
    c.round_rect(s * 0.03, s * 0.03, s * 0.97, s * 0.97, s * 0.22,
                 vertical_gradient(ACCENT, ACCENT_DEEP))
    c.round_rect(s * 0.31, s * 0.35, s * 0.65, s * 0.69, s * 0.06, INK)
    c.star4(s * 0.71, s * 0.29, s * 0.19, (255, 255, 255), sharpness=0.45)
    return c


def draw_frame(size):
    """B. 相框轮廓 + 闪光，点出「壁纸」"""
    c = Canvas(size)
    s = float(size)
    c.round_rect(s * 0.03, s * 0.03, s * 0.97, s * 0.97, s * 0.22,
                 vertical_gradient(ACCENT, ACCENT_DEEP))
    # 先挖出深色外框，再填白，再挖空内部，形成一个描边相框
    c.round_rect(s * 0.19, s * 0.23, s * 0.69, s * 0.69, s * 0.07, INK)
    c.round_rect(s * 0.25, s * 0.29, s * 0.63, s * 0.63, s * 0.04, (255, 255, 255))
    c.round_rect(s * 0.30, s * 0.34, s * 0.58, s * 0.58, s * 0.02, INK)
    c.poly([(s * 0.30, s * 0.58), (s * 0.42, s * 0.43), (s * 0.50, s * 0.58)],
           (255, 255, 255))
    c.star4(s * 0.75, s * 0.25, s * 0.18, (255, 255, 255), sharpness=0.45)
    return c


def draw_folder(size):
    """C. 文件夹 + 勾，点出「清理残留的文件夹」"""
    c = Canvas(size)
    s = float(size)
    c.round_rect(s * 0.03, s * 0.03, s * 0.97, s * 0.97, s * 0.22,
                 vertical_gradient(ACCENT, ACCENT_DEEP))
    c.round_rect(s * 0.17, s * 0.21, s * 0.52, s * 0.35, s * 0.05, INK)
    c.round_rect(s * 0.17, s * 0.27, s * 0.83, s * 0.75, s * 0.06, (255, 255, 255))
    c.segment(s * 0.33, s * 0.52, s * 0.44, s * 0.63, s * 0.075, ACCENT_DEEP)
    c.segment(s * 0.44, s * 0.63, s * 0.67, s * 0.39, s * 0.075, ACCENT_DEEP)
    return c


def draw_brand_simple(size):
    """D. 面板标记的简化版：只保留渐变圆角方块和大闪光

    A 那个深色内方块在 16px 下只剩 5 个像素，看不出是什么，反而把闪光挤没了。
    小尺寸图标要靠一个高对比的粗形状撑住，所以这里去掉内方块、把闪光放大。
    """
    c = Canvas(size)
    s = float(size)
    c.round_rect(s * 0.03, s * 0.03, s * 0.97, s * 0.97, s * 0.22,
                 vertical_gradient(ACCENT, ACCENT_DEEP))
    c.star4(s * 0.50, s * 0.49, s * 0.31, (255, 255, 255), sharpness=0.45)
    return c


def draw_broom(size):
    """E. 扫帚（实测 16px 下刷毛和手柄糊成一道斜杠，认不出来，已不推荐）"""
    c = Canvas(size)
    s = float(size)
    c.round_rect(s * 0.03, s * 0.03, s * 0.97, s * 0.97, s * 0.22,
                 vertical_gradient(ACCENT, ACCENT_DEEP))
    c.segment(s * 0.75, s * 0.20, s * 0.52, s * 0.47, s * 0.085, (255, 255, 255))
    c.poly([(s * 0.55, s * 0.42), (s * 0.66, s * 0.54),
            (s * 0.38, s * 0.80), (s * 0.26, s * 0.68)], (255, 255, 255))
    c.segment(s * 0.44, s * 0.55, s * 0.36, s * 0.63, s * 0.045, ACCENT_DEEP)
    c.segment(s * 0.54, s * 0.65, s * 0.46, s * 0.73, s * 0.045, ACCENT_DEEP)
    return c


# 标签里标了 16px 下的实测可辨认度：任务栏和资源管理器小图标都用 16px，
# 那个尺寸下认不出来就等于没有图标。用 --preview 可以自己看放大图复核。
DESIGNS = {
    'd': ('面板标记简化版 · 大闪光（16px 可辨认，默认）', draw_brand_simple),
    'c': ('文件夹 + 勾（16px 可辨认）', draw_folder),
    'a': ('面板标记 · 内方块 + 小闪光（16px 下两者都糊掉）', draw_brand),
    'b': ('相框 + 闪光（16px 下不可辨认）', draw_frame),
    'e': ('扫帚（16px 下糊成一道斜杠）', draw_broom),
}
DEFAULT_DESIGN = 'd'


# ------------------------------------------------------------------ PNG / ICO

def png_bytes(width, height, rgba):
    """最小 PNG 编码器：8 位 RGBA、无隔行、filter 全 0"""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + chunk(b'IEND', b''))


def render_rgba(design, size):
    return design(size * SUPERSAMPLE).downsample(SUPERSAMPLE)


def render_png(design, size):
    return png_bytes(size, size, render_rgba(design, size))


def write_ico(path, design, sizes=ICO_SIZES):
    """写出 ICO；每个尺寸用一张 PNG 作为图像条目（Windows Vista+ 支持）"""
    images = [(size, render_png(design, size)) for size in sizes]
    header = struct.pack('<HHH', 0, 1, len(images))
    entries, blob, offset = b'', b'', 6 + 16 * len(images)
    for size, data in images:
        dim = 0 if size >= 256 else size      # 256 在 ICO 目录里用 0 表示
        entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blob += data
    with open(path, 'wb') as f:
        f.write(header + entries + blob)
    return sum(len(d) for _s, d in images)


def preview_sheet(path, designs, sizes=(16, 32, 48, 128), cell=150):
    """把多个造型、多个尺寸拼成一张图，方便一眼比较小尺寸下的可辨认度"""
    keys = sorted(designs)
    width, height = cell * len(sizes), cell * len(keys)
    sheet = bytearray(width * height * 4)
    for row, key in enumerate(keys):
        design = designs[key][1]
        for col, size in enumerate(sizes):
            rgba = render_rgba(design, size)
            ox = col * cell + (cell - size) // 2
            oy = row * cell + (cell - size) // 2
            for y in range(size):
                src = y * size * 4
                dst = ((oy + y) * width + ox) * 4
                sheet[dst:dst + size * 4] = rgba[src:src + size * 4]
    with open(path, 'wb') as f:
        f.write(png_bytes(width, height, sheet))


def magnified_sheet(path, designs, size=16, zoom=9, pad=14):
    """把指定尺寸放大后并排铺开

    16px 是任务栏和资源管理器的小图标尺寸，也是图标最容易翻车的地方，但在这个尺寸下
    直接看几乎看不出差别。放大到能数清像素，才好判断轮廓还认不认得出来。
    """
    keys = sorted(designs)
    cell = size * zoom + pad * 2
    width, height = cell * len(keys), cell
    sheet = bytearray(width * height * 4)
    for col, key in enumerate(keys):
        rgba = render_rgba(designs[key][1], size)
        ox, oy = col * cell + pad, pad
        for y in range(size):
            for x in range(size):
                src = (y * size + x) * 4
                # 最近邻放大，保留硬像素边界
                for dy in range(zoom):
                    dst_row = ((oy + y * zoom + dy) * width + ox + x * zoom) * 4
                    for dx in range(zoom):
                        sheet[dst_row + dx * 4:dst_row + dx * 4 + 4] = rgba[src:src + 4]
    with open(path, 'wb') as f:
        f.write(png_bytes(width, height, sheet))
    return keys


# ------------------------------------------------------------------ 入口

def build_icon(project_root, design_key=DEFAULT_DESIGN, preview=False):
    """供 build.py / spec 调用：生成 icon.ico 并返回路径"""
    out_dir = os.path.join(project_root, 'packaging')
    if not os.path.isdir(out_dir):
        out_dir = project_root
    ico_path = os.path.join(out_dir, 'icon.ico')
    _design = DESIGNS[design_key][1]
    total = write_ico(ico_path, _design)
    print(f'[icon] 已生成 {ico_path}'
          f'（{", ".join(str(s) for s in ICO_SIZES)}，{total / 1024:.1f} KB）')
    if preview:
        preview_sheet(os.path.join(out_dir, 'icon-preview.png'), DESIGNS)
    return ico_path


def main():
    parser = argparse.ArgumentParser(description='生成 wallpaper-cleaner 程序图标')
    parser.add_argument('--design', default=DEFAULT_DESIGN, choices=sorted(DESIGNS),
                        help='造型：' + '，'.join(f'{k}={v[0]}' for k, v in sorted(DESIGNS.items())))
    parser.add_argument('--preview', action='store_true', help='输出各尺寸与三造型对比预览图')
    parser.add_argument('--out', default=None, help='输出路径（默认 packaging/icon.ico）')
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    label, design = DESIGNS[args.design]
    out = args.out or os.path.join(here, 'icon.ico')

    total = write_ico(out, design)
    print(f'图标已生成: {out}')
    print(f'  造型: {label}')
    print(f'  尺寸: {", ".join(str(s) for s in ICO_SIZES)}'
          f'（共 {len(ICO_SIZES)} 个，{total / 1024:.1f} KB）')

    if args.preview:
        for size in ICO_SIZES:
            path = os.path.join(here, f'preview-{args.design}-{size}.png')
            with open(path, 'wb') as f:
                f.write(render_png(design, size))
        sheet = os.path.join(here, 'preview-sheet.png')
        preview_sheet(sheet, DESIGNS)
        zoom = os.path.join(here, 'preview-16px.png')
        keys = magnified_sheet(zoom, DESIGNS, size=16)
        print(f'  预览: {here}\\preview-{args.design}-*.png')
        print(f'  对比图: {sheet}')
        print(f'  16px 放大图: {zoom}（从左到右依次是 {" / ".join(keys)}）')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
