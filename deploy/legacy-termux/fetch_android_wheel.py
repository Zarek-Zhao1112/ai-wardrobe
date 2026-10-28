#!/usr/bin/env python3
"""取 pydantic-core 的 **Android 预编译 wheel**（Termux 专用）。

## 为什么需要这个脚本

`pydantic-core` 是 Rust 扩展。PyPI 上的 aarch64 wheel 是 **manylinux(glibc)** 的，
而 Termux 用的是 **bionic libc** → 装不上，只能现编 Rust
（要额外装 100+MB 的 rust 工具链，手机上十几分钟，还很吃内存）。

社区有预编译：[TUR](https://github.com/termux-user-repository/tur-pypi-dists)。
但它的**索引**在 GitHub Pages（能连），**包本体**在 GitHub Releases ——
**国内手机上直连拉不动**（实测：90 秒还没下完 2MB 的包）。

所以这里自己下：读 TUR 索引找到匹配当前解释器的 wheel → 走 GitHub 代理下载。
实测各通道取 256KB 的耗时（2026-10 某安卓机）：

| 通道 | 吞吐 |
|---|---|
| GitHub 直连 | 超时 |
| ghproxy.net | 9 KB/s |
| **gh-proxy.com** | **94 KB/s** ✓ |

## 用法

    python deploy/legacy-termux/fetch_android_wheel.py [--dest DIR]

成功时 stdout **只打印 wheel 的完整路径**（方便 shell 里 `WHEEL=$(...)`），
其余说明都走 stderr。失败时退出码非 0，并给出可照做的替代方案。
"""

from __future__ import annotations

import argparse
import html
import os
import platform
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TUR_INDEX = "https://termux-user-repository.github.io/pypi/pydantic-core/"

# 依次尝试。空串代表直连（放最后 —— 它基本不通，但万一以后通了呢）。
PROXIES = [
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "",
]

CHUNK = 64 * 1024
MIN_BYTES = 300 * 1024          # 小于这个数说明下到的是错误页，不是 wheel
MIN_SPEED_KBPS = 15             # 低于这个速度就换下一个通道，别干等
SLOW_AFTER_SEC = 20             # 多久之后开始判速


def log(*a) -> None:
    print(*a, file=sys.stderr)


def wheel_arch() -> str:
    """把 `platform.machine()` 映射成 wheel 标签里的架构名。"""
    m = platform.machine().lower()
    return {
        "aarch64": "arm64_v8a",
        "armv8l": "arm64_v8a",
        "armv7l": "arm",
        "arm": "arm",
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "i686": "i686",
        "i386": "i686",
    }.get(m, m)


def fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def list_candidates() -> list[tuple[tuple[int, ...], str, str]]:
    """返回 [(版本元组, 版本串, 完整 URL)]，按版本升序。"""
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    arch = wheel_arch()
    log(f"解释器 {platform.python_version()}（tag={tag}）· 架构 {platform.machine()} → {arch}")
    log(f"读取 TUR 索引：{TUR_INDEX}")

    html_text = fetch(TUR_INDEX)
    hrefs = re.findall(r'href="([^"#?]+\.whl)', html_text)
    log(f"索引里有 {len(hrefs)} 个 wheel")

    out = []
    for href in hrefs:
        name = href.rsplit("/", 1)[-1]
        # 形如 pydantic_core-2.50.1-cp314-cp314-android_24_arm64_v8a.whl
        if f"-{tag}-{tag}-android_" not in name or arch not in name:
            continue
        parts = name.split("-")
        if len(parts) < 2:
            continue
        ver = parts[1]
        try:
            key = tuple(int(x) for x in re.findall(r"\d+", ver)[:3])
        except ValueError:
            continue
        out.append((key, ver, urllib.parse.urljoin(TUR_INDEX, html.unescape(href))))

    out.sort(key=lambda t: t[0])
    return out


def download(url: str, dest: str) -> bool:
    """带代理回退 + 速度判据的下载。"""
    rel = url.split("github.com/", 1)[-1]
    for prefix in PROXIES:
        full = f"{prefix}https://github.com/{rel}"
        label = prefix or "（直连）"
        t0 = time.time()
        got = 0
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                while True:
                    buf = r.read(CHUNK)
                    if not buf:
                        break
                    f.write(buf)
                    got += len(buf)
                    dt = time.time() - t0
                    if dt > SLOW_AFTER_SEC and got / 1024 / dt < MIN_SPEED_KBPS:
                        raise TimeoutError(
                            f"太慢（{got/1024:.0f}KB / {dt:.0f}s = {got/1024/dt:.0f} KB/s）"
                        )
            dt = time.time() - t0
            if got < MIN_BYTES:
                raise ValueError(f"只下到 {got} 字节，像是错误页")
            log(f"  ✓ {label} {got/1024/1024:.2f} MB / {dt:.1f}s = {got/1024/dt:.0f} KB/s")
            return True
        except Exception as e:  # noqa: BLE001 - 换通道继续，失败原因都打出来
            log(f"  ✗ {label} {type(e).__name__}: {str(e)[:70]}")
            if os.path.exists(dest):
                os.remove(dest)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="取 pydantic-core 的 Android 预编译 wheel")
    ap.add_argument("--dest", default=os.path.expanduser("~/.cache/wzbbti"),
                    help="下载到哪个目录（默认 ~/.cache/wzbbti）")
    args = ap.parse_args()

    socket.setdefaulttimeout(20)

    try:
        cands = list_candidates()
    except Exception as e:  # noqa: BLE001
        log(f"✗ 读 TUR 索引失败：{type(e).__name__}: {e}")
        cands = []

    if not cands:
        log("")
        log("✗ TUR 上没有匹配当前 Python 版本/架构的预编译包。两条出路：")
        log("  ① 换一个 Termux 的 Python 版本（TUR 通常覆盖 3.11/3.12/3.13/3.14）")
        log("  ② 现场编译 Rust（慢但不挑版本）：")
        log("       pkg install -y rust clang cmake make binutils")
        log("       CARGO_BUILD_JOBS=2 pip install pydantic-core")
        return 1

    ver, url = cands[-1][1], cands[-1][2]
    log(f"选中最新的匹配版本：pydantic_core {ver}")
    log(f"  {url}")

    os.makedirs(args.dest, exist_ok=True)
    # 文件名必须是规范名（名称-版本-平台.whl），否则 pip 不认。
    # Termux 的家目录是 ext4，长文件名没问题。
    dest = os.path.join(args.dest, url.rsplit("/", 1)[-1])

    if os.path.exists(dest) and os.path.getsize(dest) >= MIN_BYTES:
        log(f"已经下过了，直接用：{dest}")
    elif not download(url, dest):
        log("")
        log("✗ 所有代理都没拉下来。可以手动把 wheel 传到手机上再 pip install 它。")
        return 2

    # 只往 stdout 打路径 —— shell 里要 `WHEEL=$(...)`
    print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
