"""解压 JDK 与 Android cmdline-tools，并整理成 SDK 期望的目录结构。

为什么要自己解压：Windows 上 `unzip` 处理长路径/中文路径不稳，
而 sdkmanager 要求 cmdline-tools 必须落在 `<sdk>/cmdline-tools/latest/`。
"""

import shutil
import sys
import zipfile
from pathlib import Path

BUILD = Path(__file__).parent


def unzip(src: Path, dst: Path, strip_prefix: str | None = None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        prefix = strip_prefix or ""
        if prefix and not all(n.startswith(prefix) for n in names):
            prefix = ""
        for n in names:
            rel = n[len(prefix):] if prefix else n
            if not rel or rel.endswith("/"):
                continue
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst, 1 << 20)
    print(f"  解压 {src.name} → {dst}  ({len(list(dst.rglob('*')))} 项)")


def main() -> int:
    jdk_zip = BUILD / "jdk17.zip"
    sdk_zip = BUILD / "cmdline-tools.zip"

    if jdk_zip.exists() and not (BUILD / "jdk" / "bin" / "java.exe").exists():
        print("解压 JDK …")
        # Adoptium 的 zip 顶层是 jdk-17.x.x+x/
        with zipfile.ZipFile(jdk_zip) as z:
            top = {n.split("/")[0] for n in z.namelist() if "/" in n}
        prefix = f"{sorted(top)[0]}/" if len(top) == 1 else ""
        unzip(jdk_zip, BUILD / "jdk", prefix)
    else:
        print("JDK 已就绪")

    sdk_root = BUILD / "android-sdk"
    latest = sdk_root / "cmdline-tools" / "latest"
    if sdk_zip.exists() and not (latest / "bin" / "sdkmanager.bat").exists():
        print("解压 cmdline-tools …")
        unzip(sdk_zip, latest, "cmdline-tools/")
    else:
        print("cmdline-tools 已就绪")

    java = BUILD / "jdk" / "bin" / "java.exe"
    print("java:", java, "存在:", java.exists())
    print("sdkmanager:", latest / "bin" / "sdkmanager.bat",
          "存在:", (latest / "bin" / "sdkmanager.bat").exists())
    return 0


if __name__ == "__main__":
    sys.exit(main())
