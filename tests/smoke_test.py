"""P0 烟雾测试：验证服务真能起来、核心链路真能跑通。

用法（在项目根目录执行）：
    .venv/Scripts/python.exe tests/smoke_test.py

设计要点：
- **用临时库**（WARDROBE_DB 环境变量指向临时文件），绝不碰 data/wardrobe.db
- 进程内用 TestClient 跑真链路，不是只 import 一下
- 覆盖 P0 修的几条关键缺陷，防止它们悄悄回退

必须在导入 app 之前设置 WARDROBE_DB —— db.py 是在 import 期读这个变量的。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 本文件在 tests/ 下，项目根是上一级 —— sys.path 要指向根才能 import app
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="wardrobe_smoke_"))
os.environ["WARDROBE_DB"] = str(_TMP / "smoke.db")

# 微信通道的后台 worker 关掉：本文件会起 `TestClient`（触发 lifespan），
# 不关就会拉一个后台线程每 5 秒重试一次"未绑定"，把 61 项的输出刷脏。
# （未绑定时它**不发真实请求**，但日志噪音没必要。）
# 真扫码验证是 P3-W1 的门，单独做。
os.environ["CLAWBOT_WORKER_ENABLED"] = "false"

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"   -> {detail}" if detail and not cond else ""))
    return cond


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    section("0. 纯函数：品类归一化（taxonomy）")
    from app import taxonomy

    check("'t-shirt' 能命中 tshirt（连字符处理）", taxonomy.resolve_type("t-shirt") == "tshirt",
          taxonomy.resolve_type("t-shirt"))
    check("'T恤' 能命中 tshirt", taxonomy.resolve_type("T恤") == "tshirt", taxonomy.resolve_type("T恤"))
    check("'面包' 不应命中 bag（短词误判）", taxonomy.resolve_type("面包") != "bag",
          taxonomy.resolve_type("面包"))
    check("'黑裙子' 能命中 skirt（两字词召回）", taxonomy.resolve_type("黑裙子") == "skirt",
          taxonomy.resolve_type("黑裙子"))
    check("'连衣裙' 命中 dress", taxonomy.resolve_type("连衣裙") == "dress", taxonomy.resolve_type("连衣裙"))
    check("'包' 精确命中 bag", taxonomy.resolve_type("包") == "bag", taxonomy.resolve_type("包"))
    check("'运动鞋' 命中 sneakers", taxonomy.resolve_type("运动鞋") == "sneakers",
          taxonomy.resolve_type("运动鞋"))
    check("'shoes' 命中 other_shoes（词表里没有 shoes 这个 key 的陷阱）",
          taxonomy.resolve_type("shoes") == "other_shoes", taxonomy.resolve_type("shoes"))
    check("'shoes' 的槽位是 shoes 而不是 accessory",
          taxonomy.slot_of(taxonomy.resolve_type("shoes")) == "shoes",
          taxonomy.slot_of(taxonomy.resolve_type("shoes")))
    check("dict 里品类数 >= 50", len(taxonomy.CLOTHING_TYPES) >= 50, str(len(taxonomy.CLOTHING_TYPES)))

    section("0b. 纯函数：季节化组合数")
    items = (
        [{"type": "tshirt", "slot": "top", "season": ["summer"], "primary_color": "white",
          "is_archived": False} for _ in range(3)]
        + [{"type": "down", "slot": "outer", "season": ["winter"], "primary_color": "black",
            "is_archived": False} for _ in range(2)]
        + [{"type": "jeans", "slot": "bottom", "season": ["summer"], "primary_color": "blue",
            "is_archived": False} for _ in range(2)]
        + [{"type": "sneakers", "slot": "shoes", "season": ["summer"], "primary_color": "white",
            "is_archived": False} for _ in range(2)]
    )
    summer = taxonomy.count_combinations(items, "summer")
    winter = taxonomy.count_combinations(items, "winter")
    check("夏季组合数 = (3*2+0)*2 = 12", summer["total_combinations"] == 12,
          str(summer["total_combinations"]))
    check("冬季组合数 = 0（只有羽绒服，没有冬季上下装）", winter["total_combinations"] == 0,
          str(winter["total_combinations"]))
    check("冬季能指出断链槽位", len(winter["blocking_slots"]) > 0, str(winter["blocking_slots"]))

    home = [{"type": "pajamas", "slot": "full", "season": [], "primary_color": "gray",
             "is_archived": False} for _ in range(3)]
    check("家居套装不参与可出门组合", taxonomy.count_combinations(home, "summer")["total_combinations"] == 0)
    suit = [{"type": "suit", "slot": "full", "season": [], "primary_color": "navy",
             "is_archived": False}]
    check("suit 保留在 full（不该被排除）", taxonomy.count_combinations(suit, "summer")["counts"]["full"] == 1)

    section("1. 应用装配与健康检查")
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/health")
        check("GET /health 返回 200", r.status_code == 200, f"status={r.status_code}")
        h = r.json() if r.status_code == 200 else {}
        check("schema_version == 1", h.get("schema_version") == 1, str(h.get("schema_version")))

        r = client.get("/taxonomy")
        check("GET /taxonomy 返回 200", r.status_code == 200, f"status={r.status_code}")
        tx = r.json() if r.status_code == 200 else {}
        check("taxonomy.types 数量 >= 50", len(tx.get("types", {})) >= 50, str(len(tx.get("types", {}))))

        r = client.get("/")
        check("GET / 返回 200", r.status_code == 200, f"status={r.status_code}")

        section("2. 衣橱 CRUD 与录入即时反馈（设计文档 §9.1/§9.2）")
        created_ids = []
        # 刻意不标 season：不标即视为四季通用，测试就不会因为跑的月份不同而飘
        plan = [
            ("tshirt", "上衣T恤", {"colors": ["white"], "primary_color": "white"}),
            ("shirt", "衬衫", {"colors": ["blue"], "primary_color": "blue"}),
            ("jeans", "牛仔裤", {"colors": ["blue"], "primary_color": "blue"}),
            ("casual_pants", "休闲裤", {"colors": ["khaki"], "primary_color": "khaki"}),
            ("sneakers", "运动鞋", {"colors": ["white"], "primary_color": "white"}),
            ("leather_shoes", "皮鞋", {"colors": ["black"], "primary_color": "black"}),
        ]
        last = None
        for t, name, attrs in plan:
            r = client.post("/items", json={"type": t, "name": name, **attrs})
            if r.status_code != 200:
                check(f"POST /items ({name})", False, f"{r.status_code} {r.text[:200]}")
                break
            last = r.json()
            created_ids.append(last["item"]["id"])
        else:
            check("POST /items 建 6 件全部成功", len(created_ids) == 6, str(len(created_ids)))
            check("响应含 combination_delta", "combination_delta" in (last or {}),
                  str(list((last or {}).keys())))
            d = last["combination_delta"]
            check("combination_delta 有 before/after", "before" in d and "after" in d, str(d))
            check("最后一件（鞋）让组合数增加", d["after"] > d["before"], str(d))
            check("响应含 message", bool(last.get("message")), str(last.get("message")))
            check("响应含 sample_outfits", "sample_outfits" in last, str(list(last.keys())))
            if last.get("sample_outfits"):
                so = last["sample_outfits"][0]
                check("样例搭配含真实 item 展开", len(so.get("items", [])) > 0, str(so))
                check("样例 item_id 都在库里", all(i["id"] in created_ids for i in so["items"]),
                      str([i["id"] for i in so["items"]]))

        section("3. 列表 / 详情 / 搜索")
        r = client.get("/items")
        check("GET /items 返回 200", r.status_code == 200, f"status={r.status_code}")
        lst = r.json() if r.status_code == 200 else {}
        check("items 数量 == 6", lst.get("total") == 6, str(lst.get("total")))

        r = client.get("/items", params={"slot": "shoes"})
        check("按槽位过滤 slot=shoes 得 2 件",
              r.status_code == 200 and r.json().get("total") == 2,
              str(r.json().get("total") if r.status_code == 200 else r.status_code))

        r = client.get("/items", params={"search": "%"})
        check("搜索 '%' 不会命中全部（LIKE 转义）",
              r.status_code == 200 and r.json().get("total") == 0,
              str(r.json().get("total") if r.status_code == 200 else r.status_code))

        r = client.get("/items", params={"search": "牛仔"})
        check("搜索 '牛仔' 命中 1 件",
              r.status_code == 200 and r.json().get("total") == 1,
              str(r.json().get("total") if r.status_code == 200 else r.status_code))

        section("4. 缺口分析与组合数")
        r = client.get("/wardrobe/gaps")
        check("GET /wardrobe/gaps 返回 200", r.status_code == 200, f"status={r.status_code}")
        gaps = r.json() if r.status_code == 200 else {}
        check("缺口分析有季节字段", bool(gaps.get("season")), str(gaps.get("season")))
        check("组合数 = 2*2*2 = 8", gaps.get("total_combinations") == 8,
              str(gaps.get("total_combinations")))
        check("有补货建议（slot_suggestions）", len(gaps.get("slot_suggestions", [])) > 0)
        check("有旧规则对照项", bool(gaps.get("legacy_rule_verdict")), str(gaps.get("legacy_rule_verdict")))
        check("旧规则对照能看出漏判",
              "白名单" in (gaps.get("legacy_rule_verdict") or "")
              or "不上报" in (gaps.get("legacy_rule_verdict") or ""),
              str(gaps.get("legacy_rule_verdict")))
        print(f"        旧规则怎么说：{gaps.get('legacy_rule_verdict')}")
        for s in gaps.get("slot_suggestions", [])[:3]:
            print(f"        建议 {s['slot_label']}: ΔC={s['delta']} 折扣={s['diversity_discount']} 分={s['score']}")

        r = client.get("/wardrobe/combinations", params={"limit": 5})
        check("GET /wardrobe/combinations 返回 200", r.status_code == 200, f"status={r.status_code}")
        comb = r.json() if r.status_code == 200 else {}
        check("样例被正确截断", comb.get("truncated") is True, str(comb.get("truncated")))
        check("样例数量 == limit", len(comb.get("sample", [])) == 5, str(len(comb.get("sample", []))))

        r = client.get("/wardrobe/gaps", params={"season": "bogus"})
        check("非法 season 返回 422", r.status_code == 422, f"status={r.status_code}")

        section("5. 修改单品（可清空字段 / 保留打标来源）")
        if created_ids:
            iid = created_ids[0]
            r = client.patch(f"/items/{iid}", json={"notes": "夏天常穿"})
            check("PATCH 设置 notes", r.status_code == 200 and r.json().get("notes") == "夏天常穿",
                  str(r.json().get("notes") if r.status_code == 200 else r.status_code))
            r = client.patch(f"/items/{iid}", json={"notes": None})
            check("PATCH 传 null 能清空 notes（缺陷 #2）",
                  r.status_code == 200 and r.json().get("notes") is None,
                  str(r.json().get("notes") if r.status_code == 200 else r.status_code))
            before_by = r.json().get("tagged_by") if r.status_code == 200 else None
            r = client.patch(f"/items/{iid}", json={"primary_color": "off_white"})
            check("PATCH 改属性后 tagged_by 保留、不被盖成 manual（缺陷 #12）",
                  r.status_code == 200 and r.json().get("tagged_by") == before_by,
                  f"{before_by} -> {r.json().get('tagged_by') if r.status_code == 200 else r.status_code}")
            r = client.patch(f"/items/{iid}", json={"type": ""})
            check("PATCH type='' 被拒（缺陷 #11）", r.status_code == 422, f"status={r.status_code}")
            r = client.patch("/items/not-exist-id", json={"notes": "x"})
            check("PATCH 不存在的 id 返回 404", r.status_code == 404, f"status={r.status_code}")

        section("6. 穿着记录（缺陷 #10：非法 outfit_id 必须 400 而不是 500）")
        if len(created_ids) >= 3:
            r = client.post("/items/wear", json={"item_ids": created_ids[:3]})
            check("记录一次穿着成功", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
            if r.status_code == 200:
                check("source 默认 self_recorded", r.json().get("source") == "self_recorded",
                      str(r.json().get("source")))

            r = client.post("/items/wear", json={
                "item_ids": created_ids[:3], "outfit_id": "outfit_does_not_exist"
            })
            check("非法 outfit_id 返回 400（不是 500）", r.status_code == 400, f"status={r.status_code}")

            r = client.post("/items/wear", json={
                "item_ids": created_ids[:3], "outfit_id": "outfit_does_not_exist",
                "source": "ai_suggested"
            })
            check("ai_suggested + 非法 outfit_id 仍返回 400", r.status_code == 400, f"status={r.status_code}")

            r = client.post("/items/wear", json={"item_ids": ["no_such_item"]})
            check("不存在的 item_id 返回 400", r.status_code == 400, f"status={r.status_code}")

            r = client.post("/items/wear", json={"item_ids": created_ids[:2], "worn_at": "2026/09/30"})
            check("非法日期格式返回 422", r.status_code == 422, f"status={r.status_code}")

            r = client.get("/items/wear/events")
            check("能查到穿着记录", r.status_code == 200 and len(r.json()) >= 1,
                  str(len(r.json()) if r.status_code == 200 else r.status_code))

            r = client.get(f"/items/{created_ids[0]}")
            check("穿着后 wear_count 增加",
                  r.status_code == 200 and r.json().get("wear_count", 0) >= 1,
                  str(r.json().get("wear_count") if r.status_code == 200 else r.status_code))

        section("7. 换季降级（本季 0 套时必须给出解释，不能干巴巴显示 0）")
        if created_ids:
            for iid in created_ids:
                client.patch(f"/items/{iid}", json={"season": ["summer"]})

            r = client.get("/wardrobe/gaps", params={"season": "winter"})
            g = r.json() if r.status_code == 200 else {}
            check("全部标成夏季后，冬季组合数 = 0",
                  g.get("total_combinations") == 0, str(g.get("total_combinations")))
            joined = " ".join(g.get("suggestions", []))
            check("给出了跨季节解释（而不是只报 0）", "跨季节" in joined, joined[:200])
            check("指出可以不填 season", "不填" in joined or "不标" in joined, joined[:200])

            r = client.get("/wardrobe/gaps", params={"season": "summer"})
            check("切回夏季又能算出 8 套",
                  r.status_code == 200 and r.json().get("total_combinations") == 8,
                  str(r.json().get("total_combinations") if r.status_code == 200 else r.status_code))

            r = client.get("/wardrobe/gaps")
            check("不传 season 时用当前季节（今天是秋季，应为 0）",
                  r.status_code == 200 and r.json().get("total_combinations") == 0,
                  str(r.json().get("total_combinations") if r.status_code == 200 else r.status_code))

    section("8. 知识库（P4）：frontmatter / 检索 / 写盘安全（全程只碰临时 vault）")
    import tempfile
    from pathlib import Path as _Path

    from app.services.knowledge import catalog as _kbcat
    from app.services.knowledge import frontmatter as _kbfm
    from app.services.knowledge import retrieval as _kbret
    from app.services.knowledge import write as _kbwrite
    from vault_fixture import make_vault as _mkvault, snapshot_fingerprint as _kbfp

    # 临时 vault 落在**系统临时目录** —— 跑完不清理也不会在仓库留残留
    _kv = _mkvault(_Path(tempfile.mkdtemp(prefix="wardrobe_kb_")) / "vault")
    _kbcat.reset_cache()

    try:
        _m1, _ = _kbfm.parse("---\ntype: permanent\ntags: [a, b]\n---\n\n# 标题\n\n正文\n")
        check("frontmatter 能解出标量与列表", _m1.get("tags") == ["a", "b"], str(_m1.get("tags")))

        _bad = "---\ntype: permanent\n\n# 标题\n\n没闭合\n"
        _m2, _b2 = _kbfm.parse(_bad)
        check("未闭合的 frontmatter 不抛异常且原样返回", _m2 == {} and _b2 == _bad, str(_m2))

        _m3, _b3 = _kbfm.parse(_kbfm.render({"type": "permanent", "tags": ["x", "领域/甲"]}, "# T · S\n\n正文\n"))
        check("render → parse 可往返", _m3.get("tags") == ["x", "领域/甲"] and "正文" in _b3, str(_m3))

        _cat = _kbcat.load(_kv)
        check("扫描出合成库的笔记", len(_cat.notes) >= 8, str(len(_cat.notes)))
        check("坏笔记不拖垮整次扫描", "坏掉的" in {n.name for n in _cat.notes}, "")
        check("领域标签按同目录多数投票（而非目录名）",
              _cat.vote_domain_tag("领域/甲领域") == "领域/甲",
              _cat.vote_domain_tag("领域/甲领域"))
        check("旧式 schema 目录被识别为 old", _cat.header_schema("领域/乙领域") == "old", "")

        _hits = _kbret.find_related(_cat, "我想学缓存", limit=3)
        check("检索能命中（缓存 → Alpha·基础）",
              bool(_hits) and _hits[0].note.name == "Alpha·基础",
              str([(h.note.name, h.score) for h in _hits]))
        check("无关主题返回空 = 库里没有（应触发建议开新主题）",
              _kbret.find_related(_cat, "量子力学入门", limit=3) == [], "")

        _before = _kbfp(_kv)
        _kbwrite.write_note(
            _kv, "领域/甲领域", "冒烟·新笔记",
            {"type": "permanent", "tags": ["领域/甲"], "note": "冒烟测试"}, "# 冒烟 · 新笔记\n\n正文\n",
        )
        _after = _kbfp(_kv)
        _changed = {k for k in _before if _before.get(k) != _after.get(k)}
        check("写盘只新增：原有文件一个字节没动、只多出新笔记",
              _changed == set() and (set(_after) - set(_before)) == {"领域/甲领域/冒烟·新笔记.md"},
              f"changed={_changed}")

        _p1 = _kbwrite.write_note(_kv, "领域/甲领域", "重名", {"type": "permanent"}, "A\n")
        _p2 = _kbwrite.write_note(_kv, "领域/甲领域", "重名", {"type": "permanent"}, "B\n")
        check("同名绝不覆盖（自动加 -2，且旧内容仍在）",
              _p1 != _p2 and _p2.name == "重名-2.md" and "A" in _p1.read_text(encoding="utf-8"),
              f"{_p1.name} / {_p2.name}")

        def _rejects(fn) -> bool:
            try:
                fn()
                return False
            except _kbwrite.WriteError:
                return True

        check("目录穿越（../）被拒",
              _rejects(lambda: _kbwrite.write_note(_kv, "领域/../../../逃逸", "越界", {"type": "permanent"}, "x")))
        check("落点白名单外被拒（不许写到 inbox）",
              _rejects(lambda: _kbwrite.write_note(_kv, "inbox", "偷渡", {"type": "permanent"}, "x")))
        check("未获授权的新目录被拒",
              _rejects(lambda: _kbwrite.write_note(_kv, "领域/全新", "x", {"type": "permanent"}, "x")))
        check("原子写不留 .tmp-* 残留", list(_kv.rglob(".tmp-*")) == [])
    finally:
        _kbcat.reset_cache()

    section("9. 知识库（P4）：生成与拼装（纯函数，不调模型）")
    from app.services.knowledge import generate as _kbgen

    _p = _kbgen.NotePlan(
        title="缓存一致性", subtitle="陈旧读",
        tags=["缓存"], aliases=["缓存一致性"],
        sections=[_kbgen.Section(heading="一、定义", body="正文一。"),
                  _kbgen.Section(heading="二、避免", body="正文二。")],
        intro="> 目标：搞清缓存。", intro_note="只讲缓存一致性。",
        outro=["- 相邻：Alpha·基础"],
    )
    _meta, _body, _ = _kbgen.assemble(_p, domain_tag="领域/甲", known_names={"Alpha·基础"})
    check("拼装：frontmatter 字段顺序固定",
          list(_meta.keys()) == ["type", "tags", "aliases", "created", "source", "note"],
          str(list(_meta.keys())))
    check("拼装：领域标签在 tags 最后一位", _meta["tags"][-1] == "领域/甲", str(_meta["tags"]))
    check("拼装：H1 是「主标题 · 副标题」",
          _body.startswith("# 缓存一致性 · 陈旧读\n"), _body.split("\n")[0])
    check("拼装：章节顺序跟着大纲",
          _body.index("## 一、定义") < _body.index("## 二、避免"))
    check("拼装：末尾有延伸学习线索", "## 附：延伸学习线索" in _body)

    _out, _dropped = _kbgen.strip_unknown_links("见 [[不存在的]]。", {"Alpha·基础"})
    check("死链被剥成普通文字（不留死链）",
          "[[" not in _out and _dropped == ["不存在的"], str(_dropped))

    _plans = _kbgen.parse_plans({"notes": [
        {"title": "A", "sections": ["一、甲"]},
        {"title": ""}, "垃圾", {"title": "B", "sections": [{"heading": "h"}]},
    ]})
    check("大纲解析容错（空标题/垃圾项丢掉，字符串章节收下）",
          len(_plans) == 2 and _plans[0].sections[0].heading == "一、甲",
          str([p.title for p in _plans]))

    return 0


if __name__ == "__main__":
    print(f"临时库：{os.environ['WARDROBE_DB']}")
    try:
        code = main()
    except Exception:
        import traceback

        traceback.print_exc()
        code = 1

    print(f"\n{'=' * 60}")
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 60)
    sys.exit(1 if FAILED else code)
