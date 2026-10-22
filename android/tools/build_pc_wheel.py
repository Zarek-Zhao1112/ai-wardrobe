"""把手机上编好的 pydantic_core 重新打包成 Chaquopy 能装的 Android wheel。

## 为什么需要这一步

Chaquopy 17.0 起 pip 强制 `--only-binary`，装不了 sdist；而它的预编译仓库
（chaquo.com/pypi）**没有 pydantic**。PyPI 上的 aarch64 wheel 又是 glibc 的
（Android 用 bionic），装不上。所以只能自己造。

好在 Termux 里已经用 rust 编出了一份 `.so`：
    _pydantic_core.cpython-314-aarch64-linux-android.so
它的 EXT_SUFFIX 与 Chaquopy 的 CPython 3.14 完全一致，理论上可直接加载
（DT_NEEDED 是 libpython3.14.so，Chaquopy 运行时同名）。

## wheel 结构

    pydantic_core-<ver>.dist-info/{METADATA,WHEEL,RECORD}
    pydantic_core/{__init__.py, _pydantic_core.*.so, core_schema.py, ...}

platform tag 用 Chaquopy 的 `android_<minApi>_<abi>` 形式（连字符换成下划线）。

用法：
    python build_pc_wheel.py <源目录> <输出目录> [tag]
"""

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

DEFAULT_TAG = "cp314-cp314-android_24_arm64_v8a"


def sha256_b64(data: bytes) -> str:
    h = hashlib.sha256(data).digest()
    return base64.urlsafe_b64encode(h).rstrip(b"=").decode()


def elf_palign(path: Path) -> list[int]:
    """读 ELF64 的 PT_LOAD 段对齐值（判断 16 KB page 兼容性）。

    Android 15 起有设备用 16 KB 内存页，`.so` 的段对齐若是 4 KB 会在
    dlopen 时直接崩（`dlopen failed`）。Chaquopy 17.0 要求 Python 3.13+ 正是为此。
    """
    import struct

    d = path.read_bytes()
    e_phoff = struct.unpack_from("<Q", d, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", d, 0x36)[0]
    e_phnum = struct.unpack_from("<H", d, 0x38)[0]
    aligns = []
    for i in range(e_phnum):
        base = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", d, base)[0] == 1:  # PT_LOAD
            aligns.append(struct.unpack_from("<Q", d, base + 48)[0])
    return aligns


def build(src: Path, out_dir: Path, tag: str) -> Path:
    meta_path = src / "METADATA"
    if not meta_path.is_file():
        raise SystemExit(f"缺少 {meta_path}")
    meta_text = meta_path.read_text(encoding="utf-8")

    name = version = None
    for line in meta_text.splitlines():
        if line.startswith("Name: "):
            name = line[6:].strip()
        elif line.startswith("Version: "):
            version = line[9:].strip()
    if not name or not version:
        raise SystemExit("METADATA 里没有 Name/Version")

    dist_info = f"{name}-{version}.dist-info".replace("-", "_")
    dist_info = f"{name}-{version}.dist-info"
    whl_name = f"{name}-{version}-{tag}.whl"
    out_path = out_dir / whl_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # 收集要打进 wheel 的文件（跳过 __pycache__，它无意义且会带绝对路径）
    files: list[tuple[str, bytes]] = []
    for p in sorted(src.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.name in ("METADATA", "WHEEL", "RECORD"):
            continue
        arc = f"{name}/{p.relative_to(src).as_posix()}"
        files.append((arc, p.read_bytes()))

    so_files = [a for a, _ in files if a.endswith(".so")]
    if not so_files:
        raise SystemExit("源目录里没有 .so —— 那这个 wheel 就没意义了")

    files.append((f"{dist_info}/METADATA", meta_text.encode("utf-8")))
    wheel_text = (
        "Wheel-Version: 1.0\n"
        "Generator: ai-wardrobe manual android repack\n"
        "Root-Is-Purelib: false\n"
        f"Tag: {tag}\n"
    )
    files.append((f"{dist_info}/WHEEL", wheel_text.encode("utf-8")))

    # RECORD：每个文件的 sha256+size，RECORD 自身留空
    lines = []
    for arc, data in files:
        lines.append(f"{arc},sha256={sha256_b64(data)},{len(data)}")
    lines.append(f"{dist_info}/RECORD,,")
    files.append((f"{dist_info}/RECORD", ("\n".join(lines) + "\n").encode("utf-8")))

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for arc, data in files:
            info = zipfile.ZipInfo(arc, date_time=(2026, 10, 22, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)

    print(f"wheel: {out_path}")
    print(f"  条目 {len(files)} 个，其中 .so {len(so_files)} 个：{so_files}")
    for a, d in files:
        print(f"    {len(d):>9}  {a}")

    so_path = next(src / Path(a).name for a in so_files if (src / Path(a).name).is_file())
    aligns = elf_palign(so_path)
    print(f"  ELF PT_LOAD 对齐: {[hex(x) for x in aligns]}")
    if all(a >= 0x4000 for a in aligns):
        print("  ✓ 16 KB page 兼容（Android 15+ 要求）")
    else:
        print("  ⚠️ 存在 4 KB 对齐的段 —— 在 16 KB 页设备上可能 dlopen 失败")
    return out_path


if __name__ == "__main__":
    src = Path(sys.argv[1])
    out = Path(sys.argv[2])
    tag = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_TAG
    build(src, out, tag)
