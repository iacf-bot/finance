"""Synthetic receipt images (FAKE data) for OCR tests, drawn with PIL."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = {"en": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf"],
         "cn": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "C:/Windows/Fonts/msyh.ttc"]}

EN_LINES = ["TEST MART GROCERY", "123 Sample St, Springfield", "Date: 10/03/2026  14:25", "",
            "Bananas            2.49", "Milk 1 gal         4.29", "Bread              3.50",
            "SUBTOTAL          10.28", "TAX                0.82", "TOTAL            $11.10",
            "VISA ****0000     11.10", "THANK YOU"]
CN_LINES = ["测试便利店", "地址: 示例路1号", "日期: 2026-09-28 12:05", "",
            "矿泉水             3.00", "面包               8.50", "小计              11.50", "优惠               1.50",
            "合计             ¥10.00", "实付             ¥10.00", "谢谢惠顾"]


def font(kind: str, size: int):
    for f in FONTS[kind]:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return None


def receipt(lines, kind="en", size=34, fmt="JPEG", width=760) -> bytes:
    f = font(kind, size)
    if f is None:
        raise RuntimeError(f"no {kind} font")
    h = 60 + len(lines) * int(size * 1.6)
    img = Image.new("RGB", (width, h), "white")
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        d.text((40, 30 + i * int(size * 1.6)), l, fill="black", font=f)
    buf = io.BytesIO()
    img.save(buf, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return buf.getvalue()


def has_font(kind):
    return font(kind, 20) is not None
