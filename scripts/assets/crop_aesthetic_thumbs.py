"""从审美封面裁出添置行用的小缩略图（鞋 + 裤脚）。

为什么不用 CSS 裁：源图 900×1299 装进 74×52 的框，`object-fit:cover` 只会
取中间那条横带（正好是"敞开外套里一件白 T"），要取到画面下方的鞋，必须
先缩放再平移，纯 CSS 做不干净。所以在构建期裁好，运行时只负责显示。

取景框为什么是这一块：六张封面的衣物排布几乎一致（上装 y150–610、
裤装 y580–1200、鞋 y1000–1245），而鞋与裤脚在水平方向是重叠的（鞋在
左侧、裤脚在右侧），所以"整条裤子不带鞋"根本框不出来——左上角总会切到
鞋头。退而求其次，把鞋和裤脚一起框进来，这是唯一能干净收口的取景。

用法：`.venv/Scripts/python.exe scripts/assets/crop_aesthetic_thumbs.py`
改了取景框或换了封面图之后必须重跑，然后重算 web_assets 清单。
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "web" / "reference" / "aesthetics"

# (左, 上, 右, 下) —— 440×309，比例 1.424，对齐显示尺寸 74×52（1.423）
BOX = (180, 990, 620, 1299)
QUALITY = 82


def main() -> int:
    if not DIR.is_dir():
        raise SystemExit("找不到封面目录：" + str(DIR))
    made = []
    for src in sorted(DIR.glob("cover-*.webp")):
        name = src.name.replace("cover-", "thumb-", 1)
        out = DIR / name
        with Image.open(src) as im:
            im = im.convert("RGB")
            width, height = im.size
            box = (BOX[0], BOX[1], min(BOX[2], width), min(BOX[3], height))
            crop = im.crop(box)
        crop.save(out, "WEBP", quality=QUALITY, method=6)
        made.append((name, crop.size, out.stat().st_size))
    total = sum(size for _, _, size in made)
    for name, size, nbytes in made:
        print(f"{name:22s} {size[0]}x{size[1]}  {nbytes / 1024:.1f} KB")
    print(f"共 {len(made)} 张，合计 {total / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
