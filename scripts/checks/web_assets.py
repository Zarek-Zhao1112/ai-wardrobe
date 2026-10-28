"""核对ES模块/HTML资源，生成Service Worker清单，移动目录后不手抄两份。"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
START = "// BEGIN GENERATED ASSETS"
END = "// END GENERATED ASSETS"
MODULE_RE = re.compile(r'''(?:from\s+|import\s*\(\s*|import\s+)["']([^"']+)["']''')


def inspect(web: Path = WEB) -> tuple[list[str], list[str]]:
    errors = []
    for path in sorted(web.rglob("*.js")):
        if path.name == "sw.js":
            continue
        source = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))
        for name in MODULE_RE.findall(source):
            if not name.startswith((".", "/")):
                errors.append(f"{path.relative_to(web)}: 不支持的模块 {name}")
                continue
            target = (web / name.lstrip("/")) if name.startswith("/") else path.parent / name
            target = target.resolve()
            if not target.is_relative_to(web.resolve()) or not target.is_file():
                errors.append(f"{path.relative_to(web)}: 缺少模块 {name}")
    for path in web.rglob("*.html"):
        for name in re.findall(r'''(?:src|href)=["'](/[^"'#?]*)["']''', path.read_text(encoding="utf-8")):
            if name != "/" and not (web / name.lstrip("/")).is_file():
                errors.append(f"{path.relative_to(web)}: 缺少资源 {name}")
    assets = ["/"] + ["/" + p.relative_to(web).as_posix() for p in sorted(web.rglob("*"))
        if p.is_file() and p.name not in {"sw.js", "index.html"}]
    return assets, errors


def generated(assets: list[str]) -> str:
    digest = hashlib.sha256()
    for url in assets:
        path = WEB / ("index.html" if url == "/" else url.lstrip("/"))
        content = path.read_bytes()
        if path.suffix != ".png":
            content = content.replace(b"\r\n", b"\n")
        digest.update(url.encode() + b"\0" + hashlib.sha256(content).digest())
    revision = digest.hexdigest()[:16]
    return START + '\nconst ASSET_REVISION = "' + revision + '";\nconst SHELL_ASSETS = ' + json.dumps(assets, ensure_ascii=False, indent=2) + ";\n" + END


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="更新生成区；默认只核查")
    args = parser.parse_args()
    assets, errors = inspect()
    sw = WEB / "sw.js"
    text = sw.read_text(encoding="utf-8")
    if START not in text or END not in text:
        errors.append("sw.js 缺少生成区标记")
    elif args.write:
        before, tail = text.split(START, 1)
        _, after = tail.split(END, 1)
        sw.write_text(before + generated(assets) + after, encoding="utf-8", newline="\n")
    elif generated(assets) not in text:
        errors.append("缓存清单与文件不一致，请运行 python scripts/checks/web_assets.py --write")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"资源检查通过：{len(assets)} 项；模块引用与离线清单一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
