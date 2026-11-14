# -*- coding: utf-8 -*-
"""
GenPaper 图标生成器
设计：圆角方形徽标 + 青→紫对角渐变底（呼应 UI 主题色 #38bdf8 → #8b5cf6）
     + 白色文档纸张（右上折角）+ 数据网格（Excel 数据）+ 柱状图（报告生成）
输出：icon/icon_1024.png 主图，web-app/frontend/dist/{favicon.ico,favicon-32.png,logo.png}
运行：python make_icon.py
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.normpath(os.path.join(HERE, '..', 'web-app', 'frontend', 'dist'))

SS = 4          # 超采样倍数
BASE = 1024
W = BASE * SS   # 4096 实际绘制尺寸

# ---- 主题色（与前端一致）----
C_CYAN = (56, 189, 248)     # #38bdf8
C_VIOLET = (139, 92, 246)   # #8b5cf6
C_INDIGO = (99, 102, 241)   # #6366f1
C_SHEET = (255, 255, 255)
C_FOLD = (199, 215, 240)    # 折角浅蓝灰
C_CELL = (223, 232, 245)    # 表格单元格
C_GRIDLINE = (148, 163, 184)

# ---- 背景：对角线性渐变（青 → 紫）----
xx, yy = np.meshgrid(np.linspace(0, 1, W), np.linspace(0, 1, W))
t = (xx + yy) / 2.0
grad = np.zeros((W, W, 3), dtype=np.uint8)
for i in range(3):
    grad[..., i] = np.clip(C_CYAN[i] + (C_VIOLET[i] - C_CYAN[i]) * t, 0, 255).astype(np.uint8)
bg = Image.fromarray(grad, 'RGB').convert('RGBA')

# 圆角蒙版（iOS 风格圆角，半径约 22%）
mask = Image.new('L', (W, W), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, W - 1, W - 1], radius=int(0.22 * W), fill=255)
img = Image.new('RGBA', (W, W), (0, 0, 0, 0))
img.paste(bg, (0, 0), mask)

# 顶部柔光（增加质感）
highlight = Image.new('RGBA', (W, W), (0, 0, 0, 0))
hd = ImageDraw.Draw(highlight)
hd.ellipse([-W * 0.3, -W * 0.45, W * 1.05, W * 0.55], fill=(255, 255, 255, 26))
highlight = highlight.filter(ImageFilter.GaussianBlur(W * 0.06))
img.alpha_composite(highlight)

# ---- 文档纸张（右上折角）----
sheet_layer = Image.new('RGBA', (W, W), (0, 0, 0, 0))
sd = ImageDraw.Draw(sheet_layer)

margin_x = 0.235 * W
sx0, sy0 = margin_x, 0.155 * W
sx1, sy1 = W - margin_x, 0.845 * W
fold = 0.14 * W

# 投影（纸张下方偏移 + 模糊）
shadow = Image.new('RGBA', (W, W), (0, 0, 0, 0))
shd = ImageDraw.Draw(shadow)
shd.polygon([(sx0, sy0 + 0.018 * W), (sx1 - fold, sy0 + 0.018 * W), (sx1, sy0 + fold + 0.018 * W),
             (sx1, sy1 + 0.018 * W), (sx0, sy1 + 0.018 * W)], fill=(10, 18, 46, 90))
shadow = shadow.filter(ImageFilter.GaussianBlur(W * 0.02))
img.alpha_composite(shadow)

# 纸张主体（右上角切角的多边形）
sd.polygon([
    (sx0, sy0),
    (sx1 - fold, sy0),
    (sx1, sy0 + fold),
    (sx1, sy1),
    (sx0, sy1),
], fill=C_SHEET + (255,))

# 折角翻页
sd.polygon([(sx1 - fold, sy0), (sx1, sy0 + fold), (sx1 - fold, sy0 + fold)], fill=C_FOLD + (255,))

img.alpha_composite(sheet_layer)

# ---- 纸张上的内容 ----
d = ImageDraw.Draw(img)
sw = sx1 - sx0
sh = sy1 - sy0
pad = 0.075 * sw

# 数据网格：3 列 × 2 行 的圆角单元格（代表 Excel 数据）
grid_top = sy0 + 0.115 * sh
grid_h = 0.34 * sh
cols, rows = 3, 2
gap = 0.035 * sw
cell_w = (sw - 2 * pad - (cols - 1) * gap) / cols
cell_h = (grid_h - (rows - 1) * gap) / rows
for r in range(rows):
    for c in range(cols):
        x0 = sx0 + pad + c * (cell_w + gap)
        y0 = grid_top + r * (cell_h + gap)
        d.rounded_rectangle([x0, y0, x0 + cell_w, y0 + cell_h],
                            radius=int(0.02 * W), fill=C_CELL + (255,))

# 柱状图：3 根渐升圆角柱（代表报告生成输出）
bar_top = sy0 + 0.545 * sh
baseline = sy1 - 0.105 * sh
bar_w = 0.145 * sw
bar_gap = 0.075 * sw
heights = [0.16 * sh, 0.27 * sh, 0.38 * sh]
colors = [C_CYAN + (255,), C_INDIGO + (255,), C_VIOLET + (255,)]
total_w = 3 * bar_w + 2 * bar_gap
bx0 = sx0 + (sw - total_w) / 2
for i, hgt in enumerate(heights):
    x0 = bx0 + i * (bar_w + bar_gap)
    y0 = baseline - hgt
    d.rounded_rectangle([x0, y0, x0 + bar_w, baseline + 0.02 * sh],
                        radius=int(0.025 * W), fill=colors[i])

# ---- 缩放输出 ----
master = img.resize((BASE, BASE), Image.LANCZOS)
master_path = os.path.join(HERE, 'icon_1024.png')
master.save(master_path)

os.makedirs(DIST, exist_ok=True)
for size in (256, 128, 64, 48, 32, 16):
    master.resize((size, size), Image.LANCZOS).save(
        os.path.join(DIST, f'favicon-{size}.png'))
master.resize((128, 128), Image.LANCZOS).save(os.path.join(DIST, 'logo.png'))

# ICO（含多尺寸）
ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
master.save(os.path.join(DIST, 'favicon.ico'), sizes=ico_sizes)

print('生成完成:')
print(' ', master_path)
for f in ('favicon.ico', 'favicon-32.png', 'logo.png'):
    p = os.path.join(DIST, f)
    print(' ', p, os.path.getsize(p), 'bytes')
