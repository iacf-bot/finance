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

TT_LINES = ["SAMPLE BISTRO", "Date: 10/02/2026  19:40", "", "Pasta              18.00", "Salad              12.00",
            "Subtotal           30.00", "Sales Tax           2.66", "Tip                 5.40", "TOTAL             $38.06",
            "THANK YOU"]
CN_TT_LINES = ["示例餐厅", "日期: 2026-09-30 19:10", "", "牛肉面            40.00", "饺子              60.00",
               "小计             100.00", "服务费            10.00", "税额               6.00", "合计            ¥116.00",
               "谢谢惠顾"]


def degrade(data: bytes, angle=0.0, noise=0, contrast=1.0, scale=1.0) -> bytes:
    """Make a synthetic receipt look like a phone photo: tilt, speckle, faded thermal print, low resolution."""
    import random
    from PIL import ImageEnhance
    img = Image.open(io.BytesIO(data)).convert("L")
    if scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
        img = img.point(lambda v: min(255, v + int((1 - contrast) * 120)))
    if noise:
        rnd = random.Random(7); px = img.load()
        for _ in range(noise):
            x, y = rnd.randrange(img.width), rnd.randrange(img.height)
            px[x, y] = rnd.choice((0, 90, 200))
    if angle:
        img = img.rotate(angle, expand=True, fillcolor=255)
    buf = io.BytesIO(); img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()
