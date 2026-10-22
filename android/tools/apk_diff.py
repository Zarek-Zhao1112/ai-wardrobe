"""对比两个 APK：条目差异 + **条目之间的死空间**。

## 为什么需要看死空间

"包突然变大 5 MB，但解包一看条目一模一样" 这种事故，靠 `unzip -l` 是看不出来的
——它只列条目，不列偏移。真凶通常是 **AGP 增量打包留下的僵尸数据**：

    Gradle 先写了一个版本的某个资产（比如 Chaquopy 的 requirements-common.imy），
    这个文件内容一变（哪怕只多 157 字节），新版本会被追加到文件别处，
    **旧版本那 5.1 MB 原地不动**，既不是条目、也不被任何目录引用。

实测 2026-10-22：`.env` 变了 157 字节 → 包从 19.0 MB 涨到 24.1 MB，
条目逐字节相同，只有偏移能看出来。`gradle clean` 一次就干净。

## 用法

    python android/tools/apk_diff.py <旧.apk> <新.apk>

两个包内容应当完全一致时（同一份源码打两次），输出应该是「条目无差异、无 >1KB 间隙」。
"""

import os
import sys
import zipfile


def summarize(path):
    """返回 {条目名: (未压缩大小, 压缩后大小, 压缩方式)} + 死空间列表。"""
    size = os.path.getsize(path)
    z = zipfile.ZipFile(path)
    infos = sorted(z.infolist(), key=lambda i: i.header_offset)

    entries = {i.filename: (i.file_size, i.compress_size, i.compress_type) for i in infos}

    gaps = []
    prev_end = 0
    with open(path, "rb") as f:
        for i in infos:
            f.seek(i.header_offset + 26)
            nlen = int.from_bytes(f.read(2), "little")
            elen = int.from_bytes(f.read(2), "little")
            data_start = i.header_offset + 30 + nlen + elen
            if i.header_offset > prev_end:
                gaps.append((i.header_offset - prev_end, prev_end, i.filename))
            prev_end = data_start + i.compress_size

    with open(path, "rb") as f:
        data = f.read()
    eocd = data.rfind(b"PK\x05\x06")
    cd_off = int.from_bytes(data[eocd + 16:eocd + 20], "little")
    # 最后一个条目到中央目录之间还有对齐填充，正常是 4 KB 以内
    tail_gap = cd_off - prev_end

    return {
        "path": path,
        "size": size,
        "count": len(infos),
        "entries": entries,
        "gaps": gaps,
        "tail_gap": tail_gap,
    }


def report(a, b):
    print(f"旧: {a['path']}")
    print(f"    {a['size']:,} 字节，{a['count']} 个条目，末条到中央目录 {a['tail_gap']:,}")
    print(f"新: {b['path']}")
    print(f"    {b['size']:,} 字节，{b['count']} 个条目，末条到中央目录 {b['tail_gap']:,}")
    delta = b["size"] - a["size"]
    if delta:
        print(f"\n体积差: {delta:+,} 字节 ({delta / 1048576:+.2f} MB)")

    only_a = sorted(set(a["entries"]) - set(b["entries"]))
    only_b = sorted(set(b["entries"]) - set(a["entries"]))
    changed = [
        k
        for k in sorted(set(a["entries"]) & set(b["entries"]))
        if a["entries"][k] != b["entries"][k]
    ]

    if only_a:
        print("\n只在旧包里:")
        for k in only_a:
            print(f"  {a['entries'][k][0]:>10,}  {k}")
    if only_b:
        print("\n只在新包里:")
        for k in only_b:
            print(f"  {b['entries'][k][0]:>10,}  {k}")
    if changed:
        print("\n大小/压缩方式变了:")
        for k in changed:
            o, n = a["entries"][k], b["entries"][k]
            print(f"  {k}\n      {o}  →  {n}")
    if not (only_a or only_b or changed):
        print("\n条目完全一致（大小、压缩后大小、压缩方式全相同）")

    def gaps_over(s, limit):
        return [g for g in s["gaps"] if g[0] > limit]

    ga, gb = gaps_over(a, 1024), gaps_over(b, 1024)
    print("\n死空间（条目之间 > 1KB 的空隙）:")
    for tag, s, gs in (("旧", a, ga), ("新", b, gb)):
        total = sum(g[0] for g in s["gaps"])
        print(f"  {tag}: 合计 {total:,} 字节")
        for size, at, name in gs:
            print(f"      {size:>10,} @ {at:>12,}  →  {name}")

    # 判定：条目一致但体积变大 → 几乎肯定是死空间
    if not (only_a or only_b or changed) and delta > 100 * 1024:
        fb = sum(g[0] for g in gb) - sum(g[0] for g in ga)
        if fb > 100 * 1024:
            print(
                f"\n⚠️ 条目一致但体积大了 {delta / 1048576:.2f} MB，"
                f"死空间多了 {fb / 1048576:.2f} MB"
                f"\n   → 这是 AGP 增量打包的僵尸数据，跑 `sh android/build_apk.sh clean` 重打"
            )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    report(summarize(sys.argv[1]), summarize(sys.argv[2]))
