"""W4 审查发现的缺陷 —— 逐条回归测试。

每一条都对应一次真实的审查发现（Bug 猎手 6 条 + 2 条疑似 + 规格核对官 1 条）。
**这些缺陷原本全都没有测试覆盖**，所以它们才能一路活到 W4 —— 这也说明
"单元测试全绿"离"链路是通的"还有距离。

⚠️ 图片相关用例各自用**不同的字节**：`image_hash` 有唯一索引（防重复录入），
共用一副字节会互相撞。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.models import ItemCreate, WearLogIn
from app.services import images
from app.services import items as item_svc
from app.services import preference, recommend, tagging, wardrobe
from app.services import wear as wear_svc


def png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00" * 60


def _item(uid: str, name: str, **fields) -> str:
    payload = {"type": fields.pop("type", "tshirt"), "name": name}
    payload.update(fields)
    return item_svc.create(uid, ItemCreate(**payload))["id"]


def _new_user() -> str:
    user_id = db.new_id("u")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (user_id, "临时用户", db.now_iso()),
        )
    return user_id


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. 图片：重传失败不能弄丢原图（数据丢失级）
# ---------------------------------------------------------------------------

class TestImageReuploadFailure:
    def test_original_photo_survives_a_failed_reupload(self, uid):
        """★ 换照片失败时，**原图必须原封不动**。

        原实现先 `target.write_bytes()` 覆盖原图，DB 失败后又 `unlink(target)`
        —— 原图凭空消失，而库里 `image_path` 还指着它。
        （Bug 猎手 W4 发现）
        """
        target_item = _item(uid, "有原图")
        other_item = _item(uid, "占位")

        first = images.save_image(uid, target_item, png("w5-original"))
        original_file = db.IMAGES_DIR / first["image_path"]
        assert original_file.is_file()

        # 让另一个单品先占住一张图的指纹
        images.save_image(uid, other_item, png("w5-conflict"))

        # 现在给 target_item 传同一张图 → 唯一索引冲突
        with pytest.raises(sqlite3.IntegrityError):
            images.save_image(uid, target_item, png("w5-conflict"))

        assert original_file.is_file(), "原图被删掉了 —— 这是数据丢失"
        assert item_svc.get(target_item, uid)["image_path"] == first["image_path"]

    def test_no_staging_file_left_behind(self, uid):
        """失败时不能留下 .staging 半成品。"""
        target_item = _item(uid, "暂存测试")
        other_item = _item(uid, "占位2")
        images.save_image(uid, other_item, png("w5-staging"))

        with pytest.raises(sqlite3.IntegrityError):
            images.save_image(uid, target_item, png("w5-staging"))

        leftovers = list(db.IMAGES_DIR.glob("*staging*"))
        assert leftovers == [], f"留下了暂存文件：{leftovers}"


# ---------------------------------------------------------------------------
# 2. season="all" 不该筛掉所有带季节标注的衣服
# ---------------------------------------------------------------------------

class TestSeasonAll:
    def test_season_all_means_no_season_filter(self):
        fresh = _new_user()
        for t in ("tshirt", "jeans", "sneakers"):
            _item(fresh, f"夏装-{t}", type=t, season=["summer"])

        assert wardrobe.total_combinations(fresh, "summer")["total_combinations"] == 1
        # 这一条原本是 0 —— 而界面上还写着「全部季节」
        assert wardrobe.total_combinations(fresh, "all")["total_combinations"] == 1


# ---------------------------------------------------------------------------
# 3. 打标失败不能覆盖已有分类
# ---------------------------------------------------------------------------

class TestTaggingDoesNotDowngrade:
    def test_failed_type_does_not_overwrite_existing(self, uid):
        """★ 打标认不出品类时，**不许**把已有分类改成 other/accessory。

        否则一件「上装」会被降级成「配饰」，直接掉出组合计算。
        （Bug 猎手 W4 发现）
        """
        item_id = _item(uid, "已有分类", type="T恤")
        assert item_svc.get(item_id, uid)["type"] == "tshirt"

        # 模拟一次"只认出颜色、没认出品类"的打标
        tagging.apply_tags(
            uid, item_id, {"tags": {"colors": ["black"], "primary_color": "black"},
                           "ai_confidence": 0.5}
        )

        after = item_svc.get(item_id, uid)
        assert after["type"] == "tshirt", "打标失败把已有分类改掉了"
        assert after["slot"] == "top"
        # `item_svc.get` 返回的是 to_out 处理过的 dict，多值字段已经是 list
        assert after["colors"] == ["black"], "认得出的字段照常写上"


# ---------------------------------------------------------------------------
# 4. 无时区时间不得崩溃
# ---------------------------------------------------------------------------

class TestNaiveDatetime:
    def test_record_accepts_naive_datetime_string(self, uid):
        """`record(when='2026-10-01')` 解析出来是无时区 datetime，
        而库里存的是带时区的 —— 相减会抛 TypeError。
        （Bug 猎手 W4 发现）"""
        preference.record(uid, "color", "w5-naive", 1.0)
        again = preference.record(uid, "color", "w5-naive", 1.0, when="2026-10-01")
        assert again["evidence_count"] > 0

    def test_record_accepts_naive_datetime_object(self, uid):
        from datetime import datetime

        preference.record(uid, "fit", "w5-naive-obj", 1.0)
        again = preference.record(
            uid, "fit", "w5-naive-obj", 1.0, when=datetime(2026, 10, 1, 12, 0, 0)
        )
        assert again["evidence_count"] > 0


# ---------------------------------------------------------------------------
# 5. 穿着记录去重
# ---------------------------------------------------------------------------

class TestWearDedup:
    def test_duplicate_item_ids_count_once(self, uid):
        """同一条记录里重复填同一件衣服，穿着次数只该加 1。
        （Bug 猎手 W4 发现）"""
        item_id = _item(uid, "重复穿着")
        wear_svc.log_wear(uid, WearLogIn(item_ids=[item_id, item_id]))

        assert item_svc.get(item_id, uid)["wear_count"] == 1

    def test_wear_event_stores_deduped_ids(self, uid):
        item_id = _item(uid, "重复穿着2")
        event = wear_svc.log_wear(uid, WearLogIn(item_ids=[item_id, item_id]))
        assert event["item_ids"] == [item_id]


# ---------------------------------------------------------------------------
# 6. 推荐 prompt 必须带上单品属性
# ---------------------------------------------------------------------------

class TestReasonPromptCarriesItems:
    def test_item_lines_reach_the_model(self, uid, fake_llm):
        """★ 提示词里写了「单品行格式：代号|槽位|...」，就必须**真的发过去**。

        原实现把 `AliasMap.build()` 的返回值丢掉了，模型只看到 `O1: A1 + A2 + A3`
        一堆代号却要写「为什么这套合适」—— 它唯一能做的就是编。
        （Bug 猎手 W4 发现）
        """
        for t in ("tshirt", "tshirt", "jeans", "jeans", "sneakers"):
            _item(uid, f"prompt-{t}", type=t)

        client = fake_llm([{"ranked": [{"outfit": "O1", "reason": "因为配色干净"}]}])
        recommend.recommend(uid, limit=1, client=client)

        prompt = client.calls[0]["messages"][1]["content"]
        import json
        rows = [json.loads(line) for line in prompt.splitlines() if line.startswith('{"code":')]
        assert rows and rows[0]["code"] == "A1", "单品属性行必须进入模型，不只发送组合代号"
        assert any(row["slot"] in {"top", "bottom", "shoes"} and row["type"] for row in rows)


# ---------------------------------------------------------------------------
# 7. 中文单字颜色/材质标签要能命中
# ---------------------------------------------------------------------------

class TestChineseSingleCharLabels:
    def test_color_with_suffix_resolves(self):
        """用户说「黑色」，词表里的标签是「黑」。单字不许做包含匹配，
        但作为**开头**是安全的。（Bug 猎手 W4 疑似项，已确认为真问题）"""
        assert tagging.resolve_enum("黑色", "color") == "black"
        assert tagging.resolve_enum("蓝色", "color") == "blue"
        assert tagging.resolve_enum("红色", "color") == "red"

    def test_longer_label_still_wins(self):
        """「酒红色」命中的是 2 字的「酒红」，不是 1 字的「红」。"""
        assert tagging.resolve_enum("酒红色", "color") == "burgundy"
        assert tagging.resolve_enum("米白色", "color") == "beige"

    def test_prefix_rule_does_not_misfire(self):
        """「深红色」以「深」开头，不是任何标签 —— 不许瞎猜成 red。"""
        assert tagging.resolve_enum("深红色", "color") is None


# ---------------------------------------------------------------------------
# 8. MMR 之后的硬约束修复
# ---------------------------------------------------------------------------

class TestDiversityEnforcement:
    def _candidate(self, ids: list[str], color: str, formality: str, rel: float) -> dict:
        return {
            "item_ids": ids,
            "items": [{"primary_color": color, "formality": formality}],
            "_relevance": rel,
        }

    def test_mmr_alone_cannot_hold_the_constraint(self):
        """★ 先说清楚 MMR 为什么不够：它只是**软**约束。

        3 套同色但相关性略高、1 套异色但相关性 0 —— λ=0.7 下同色的全胜。
        这是规格核对官 W4 给的反例，也是"多样性并入相似度"这个设计的边界。
        """
        pool = sorted(
            [
                self._candidate(["i1"], "black", "casual", 1.00),
                self._candidate(["i2"], "black", "casual", 0.99),
                self._candidate(["i3"], "black", "casual", 0.98),
                self._candidate(["i4"], "white", "work", 0.00),
            ],
            key=lambda c: c["_relevance"],
            reverse=True,
        )
        picked = recommend.mmr_select(pool, 3)
        colors = {c for o in picked for c in recommend._attr_set(o, "primary_color")}
        assert len(colors) == 1, "MMR 本身会选出 3 套同色的（软约束的固有限制）"

    def test_enforcement_fixes_it(self):
        pool = sorted(
            [
                self._candidate(["i1"], "black", "casual", 1.00),
                self._candidate(["i2"], "black", "casual", 0.99),
                self._candidate(["i3"], "black", "casual", 0.98),
                self._candidate(["i4"], "white", "work", 0.00),
            ],
            key=lambda c: c["_relevance"],
            reverse=True,
        )
        picked = recommend.mmr_select(pool, 3)
        fixed = recommend.ensure_diversity(picked, pool)

        colors = {c for o in fixed for c in recommend._attr_set(o, "primary_color")}
        formals = {f for o in fixed for f in recommend._attr_set(o, "formality")}
        assert len(colors) >= 2, "修复后应当至少 2 种主色"
        assert len(formals) >= 2, "修复后应当至少 2 种正式度"
        assert len(fixed) == 3

    def test_enforcement_gives_up_gracefully(self):
        """衣橱里全是黑衣服时，约束根本不可能满足 —— 不许报错、不许少给。"""
        pool = [
            self._candidate(["i1"], "black", "casual", 1.0),
            self._candidate(["i2"], "black", "casual", 0.9),
            self._candidate(["i3"], "black", "casual", 0.8),
        ]
        picked = recommend.mmr_select(pool, 3)
        fixed = recommend.ensure_diversity(picked, pool)
        assert len(fixed) == 3, "尽力而为，不因为满足不了就少给"


# ---------------------------------------------------------------------------
# 9. 路由层的两处（越界路径 + season 校验）
# ---------------------------------------------------------------------------

class TestRouteGuards:
    def test_recommend_rejects_bogus_season(self, client):
        """传非法 season 要 400，**不要静默返回 0 套** ——
        那样排查时完全看不出是参数写错。（规格核对官 W4 发现）"""
        r = client.post("/outfits/recommend", params={"season": "夏", "use_llm": "false"})
        assert r.status_code == 400
        assert "未知的季节" in r.json()["detail"]

    def test_recommend_accepts_all(self, client):
        assert client.post(
            "/outfits/recommend", params={"season": "all", "use_llm": "false"}
        ).status_code == 200

    def test_tagging_route_ignores_traversal_path(self, client, uid):
        """`image_path` 是用户可控字段，拼路径前必须挡掉越界。
        （Bug 猎手 W4 疑似项，低成本硬化）"""
        item_id = _item(uid, "越界路径")
        with db.conn() as c:
            c.execute(
                "UPDATE clothing_items SET image_path=? WHERE id=?",
                ("../../etc/passwd", item_id),
            )

        r = client.post(f"/tagging/{item_id}")
        # 要么 400（找不到文件），要么 503（模型不可用）——
        # **绝不能是 200**（那意味着它真读到了项目外的文件）
        assert r.status_code in (400, 503)
