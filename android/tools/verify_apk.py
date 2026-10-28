"""解包核对APK内全部web和Python模块，需与包内相同的Python3.14运行。"""
from pathlib import Path
import argparse
import hashlib
import io
import json
import marshal
import sys
import types
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def same_code(left, right):
    if not isinstance(left, types.CodeType) or not isinstance(right, types.CodeType):
        return left == right
    fields = ("co_code", "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_argcount",
              "co_posonlyargcount", "co_kwonlyargcount", "co_flags", "co_exceptiontable", "co_linetable")
    return all(getattr(left, key) == getattr(right, key) for key in fields) and len(left.co_consts) == len(right.co_consts) and all(same_code(a, b) for a, b in zip(left.co_consts, right.co_consts))


def verify(apk: Path, repo: Path = ROOT) -> dict:
    if sys.version_info[:2] != (3, 14):
        raise ValueError("请用与包内一致的Python3.14运行核对工具")
    with zipfile.ZipFile(apk) as outer:
        with zipfile.ZipFile(io.BytesIO(outer.read("assets/chaquopy/app.imy"))) as inner:
            packed = {name: inner.read(name) for name in inner.namelist() if not name.endswith("/")}
    web = {p.relative_to(repo).as_posix(): p.read_bytes() for p in (repo / "web").rglob("*") if p.is_file()}
    if web != {key: value for key, value in packed.items() if key.startswith("web/")}:
        raise ValueError("APK的web文件与源码不一致，含缺失/过期/字节差异")
    checked = 0
    for path in (repo / "app").rglob("*.py"):
        name = path.relative_to(repo).as_posix()
        if name in packed:
            equal = path.read_bytes() == packed[name]
        else:
            actual = marshal.loads(packed[name + "c"][16:])
            expected = compile(path.read_text(encoding="utf-8"), actual.co_filename, "exec", dont_inherit=True)
            equal = same_code(actual, expected)
        if not equal:
            raise ValueError("APK后端不一致：" + name)
        checked += 1
    return {"pass": True, "bytes": apk.stat().st_size, "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(), "web_files": len(web), "backend_modules": checked}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.apk)))
