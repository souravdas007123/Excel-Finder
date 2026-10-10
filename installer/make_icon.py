"""Excel Finder ka icon (installer/icon.ico) banata hai. Chalao: python installer/make_icon.py (Pillow chahiye)."""
from PIL import Image, ImageDraw
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# gradient tile (same blue to violet as the app logo)
grad = Image.new("RGBA", (S, S))
px = grad.load()
c1, c2 = (59, 130, 246), (139, 92, 246)
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S - 2)
        px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)) + (255,)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((32, 32, S - 32, S - 32), radius=220, fill=255)
img.paste(grad, (0, 0), mask)
d = ImageDraw.Draw(img)
# spreadsheet grid (white card)
gx0, gy0, gx1, gy1 = 190, 210, 700, 720
d.rounded_rectangle((gx0, gy0, gx1, gy1), radius=48, fill=(255, 255, 255, 245))
cw, ch = (gx1 - gx0) / 3, (gy1 - gy0) / 3
line = (120, 140, 190, 255)
for i in range(1, 3):
    d.line((gx0 + cw * i, gy0 + 24, gx0 + cw * i, gy1 - 24), fill=line, width=14)
    d.line((gx0 + 24, gy0 + ch * i, gx1 - 24, gy0 + ch * i), fill=line, width=14)
# header row tint + one highlighted (found) cell in green
d.rounded_rectangle((gx0 + 24, gy0 + 24, gx1 - 24, gy0 + ch - 7), radius=20, fill=(222, 232, 255, 255))
d.rounded_rectangle((gx0 + cw + 12, gy0 + ch * 1 + 12, gx0 + cw * 2 - 12, gy0 + ch * 2 - 12), radius=22, fill=(52, 211, 153, 255))
# magnifier
cx, cy, r = 640, 640, 175
d.line((cx + r * 0.72, cy + r * 0.72, 880, 880), fill=(255, 255, 255, 255), width=84)
d.ellipse((cx - r - 30, cy - r - 30, cx + r + 30, cy + r + 30), fill=(255, 255, 255, 255))
d.ellipse((cx - r + 18, cy - r + 18, cx + r - 18, cy + r - 18), fill=(99, 102, 241, 70), outline=(79, 70, 229, 255), width=0)
img.save("installer/icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])
print("ok")
