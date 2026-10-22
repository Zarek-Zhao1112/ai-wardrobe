"""从 PyPI 镜像直接抓 wheel（绕开 pip 的索引解析问题）。

为什么不用 `pip download`：本机 pip 26.1.2 对该镜像始终报
"from versions: none"，连一个 HTTP 请求日志都不打（-v 也一样），
而同一 URL 用 curl 能拿到 188 KB 的索引页 —— 所以直接在索引页里
找 href 更可靠，也不受 pip 与代理交互的影响。

用法：python grab_wheels.py <输出目录>
"""

import re
import ssl
import sys
import urllib.request
from pathlib import Path

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

# 与手机上实际装的一致（`ls site-packages` 实测），避免版本漂移
WANT = [
    ("fastapi", "0.142.0"),
    ("starlette", "1.7.0"),
    ("uvicorn", "0.54.0"),
    ("pydantic", "2.13.4"),
    ("opentelemetry-api", "1.45.0"),
    ("httpx", "0.28.1"),
    ("python-multipart", "0.0.32"),
    ("segno", "1.6.6"),
    ("anyio", "4.15.1"),
    ("certifi", "2026.10.19"),
    ("click", "8.5.0"),
    ("h11", "0.16.0"),
    ("httpcore", "1.0.9"),
    ("idna", "3.20"),
    ("typing-extensions", "4.16.0"),
    ("typing-inspection", "0.4.4"),
    ("annotated-types", "0.8.0"),
    ("annotated-doc", "0.0.5"),
    ("sniffio", "1.3.1"),
]

# 明确拒绝的：这些是平台相关的，Android 上用不了
REJECT = re.compile(r"\.(tar\.gz|zip)$|-win_|-macosx_|-manylinux_|-musllinux_")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "wheels-grabber/1.0"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        return r.read()


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    ok, fail = [], []
    for name, ver in WANT:
        try:
            html = fetch(f"{MIRROR}/{name}/").decode("utf-8", "replace")
        except Exception as e:
            fail.append((name, ver, f"索引页失败: {type(e).__name__} {e}"))
            continue

        hrefs = re.findall(r'href="([^"]+)"', html)
        want_prefix = f"{norm(name)}-{ver}-"
        cands = []
        for h in hrefs:
            base = h.split("#")[0].rsplit("/", 1)[-1]
            if not base.startswith(want_prefix):
                continue
            if not base.endswith(".whl"):
                continue
            if REJECT.search(base):
                continue
            if "py3-none-any" not in base:
                continue
            cands.append((base, h))
        if not cands:
            # 退一步：允许 py2.py3-none-any
            for h in hrefs:
                base = h.split("#")[0].rsplit("/", 1)[-1]
                if base.startswith(want_prefix) and base.endswith(".whl") and "none-any" in base:
                    cands.append((base, h))
        if not cands:
            fail.append((name, ver, "索引页里没找到对应的 pure-python wheel"))
            continue

        fname, href = cands[0]
        url = urllib.request.urljoin(f"{MIRROR}/{name}/", href)
        try:
            data = fetch(url)
        except Exception as e:
            fail.append((name, ver, f"下载失败: {type(e).__name__} {e}"))
            continue
        target = out_dir / fname
        target.write_bytes(data)
        ok.append((fname, len(data)))

    print(f"成功 {len(ok)} 个：")
    for fname, size in ok:
        print(f"  {size:>9}  {fname}")
    if fail:
        print(f"\n失败 {len(fail)} 个：")
        for name, ver, why in fail:
            print(f"  {name}=={ver}: {why}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
