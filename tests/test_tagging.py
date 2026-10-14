"""`tagging` 的单元测试。

重点是**归一**：它错了会静默 —— 库里多一条"黑白条纹"这种没法统计的值，
当时不会报错，等到缺口分析和偏好统计出问题时已经晚了。
所以这里用大量"脏输入"去撞它。
"""

from __future__ import annotations

import json

import pytest

from app.models import ItemCreate, ItemUpdate
from app.services import items as item_svc
from app.services import tagging
from app.services.llm.client import LLMError


class TestResolveEnum:
    def test_exact_key(self):
        assert tagging.resolve_enum("black", "color") == "black"

    def test_chinese_label(self):
        assert tagging.resolve_enum("黑", "color") == "black"

    def test_normalized_key(self):
        assert tagging.resolve_enum("Light-Blue", "color") == "light_blue"
        assert tagging.resolve_enum("light_blue", "color") == "light_blue"

    def test_longest_contain_match_wins(self):
        """「藏蓝色」要命中 navy 而不是 blue —— 取最长命中。"""
        assert tagging.resolve_enum("藏蓝色", "color") == "navy"

    def test_single_char_label_does_not_misfire(self):
        """单字标签不参与包含匹配，防「包」命中「面包」那类误判。"""
        assert tagging.resolve_enum("外卖面包", "material") is None

    def test_unmatched_returns_none(self):
        assert tagging.resolve_enum("荧光玫红", "color") is None
        assert tagging.resolve_enum(None, "color") is None
        assert tagging.resolve_enum("", "color") is None
        assert tagging.resolve_enum("   ", "color") is None

    def test_unknown_vocab_returns_none(self):
        assert tagging.resolve_enum("anything", "no_such_vocab") is None


class TestNormalizeTags:
    def test_type_drives_slot(self):
        clean, failed = tagging.normalize_tags({"type": "T恤"})
        assert clean["type"] == "tshirt"
        assert clean["slot"] == "top"
        assert failed == {}

    def test_unrecognized_type_is_reported_and_does_not_overwrite(self):
        """认不出的 type **不写进 clean**。

        否则 `apply_tags` 会拿 `other` 覆盖这件衣服原有的分类 ——
        一件本来是「上装」的衣服会被降级成「配饰」，**直接掉出组合计算**。
        打标失败不该破坏已有数据。（W4 审查发现）
        """
        clean, failed = tagging.normalize_tags({"type": "飞行服"})
        assert "type" not in clean
        assert "slot" not in clean
        assert failed["type"] == "飞行服"

    def test_missing_type_is_simply_skipped(self):
        """模型压根没给 type —— 那不是错，既不写 clean 也不进 failed。"""
        clean, failed = tagging.normalize_tags({"colors": ["black"]})
        assert "type" not in clean
        assert "type" not in failed
        assert clean["primary_color"] == "black"

    def test_multivalue_given_as_string_is_split(self):
        """模型常把数组写成 "black, white" —— 那是常见行为，不是错误。"""
        clean, _ = tagging.normalize_tags({"type": "tshirt", "colors": "black, white"})
        assert clean["colors"] == ["black", "white"]

    def test_multivalue_deduped(self):
        clean, _ = tagging.normalize_tags({"type": "tshirt", "colors": ["black", "black"]})
        assert clean["colors"] == ["black"]

    def test_primary_color_falls_back_to_first_of_colors(self):
        clean, _ = tagging.normalize_tags({"type": "tshirt", "colors": ["navy", "black"]})
        assert clean["primary_color"] == "navy"

    def test_bad_value_never_reaches_clean(self):
        """这条是核心：归一失败的值**不能**出现在 clean 里。"""
        clean, failed = tagging.normalize_tags(
            {"type": "tshirt", "material": "外星材料", "fit": "loose"}
        )
        assert "material" not in clean
        assert failed["material"] == "外星材料"
        assert clean["fit"] == "loose", "一个字段坏掉不该拖累其它字段"

    def test_partial_failure_in_multivalue_keeps_the_good_half(self):
        clean, failed = tagging.normalize_tags(
            {"type": "tshirt", "season": ["summer", "梅雨季"], "style": ["casual"]}
        )
        assert clean["season"] == ["summer"]
        assert failed["season"] == ["梅雨季"]
        assert clean["style"] == ["casual"]

    def test_pattern_uses_local_vocab(self):
        clean, _ = tagging.normalize_tags({"type": "shirt", "pattern": "条纹"})
        assert clean["pattern"] == "striped"


class TestTagImage:
    def _img(self, tmp_path, name="a.jpg", payload=b"\xff\xd8\xff\xe0fake"):
        path = tmp_path / name
        path.write_bytes(payload)
        return path

    def test_happy_path_normalizes_everything(self, fake_llm, tmp_path):
        client = fake_llm([
            {
                "type": "T恤",
                "colors": ["black"],
                "material": "棉",
                "fit": "宽松",
                "season": ["夏"],
                "confidence": 0.9,
            }
        ])
        result = tagging.tag_image(self._img(tmp_path), client=client)

        assert result["tags"]["type"] == "tshirt"
        assert result["tags"]["slot"] == "top"
        assert result["tags"]["primary_color"] == "black"
        assert result["tags"]["material"] == "cotton"
        assert result["tags"]["fit"] == "loose"
        assert result["tags"]["season"] == ["summer"]
        assert result["ai_confidence"] == 0.9
        assert result["unresolved"] == {}

    def test_image_is_inlined_as_data_url(self, fake_llm, tmp_path):
        """模型接不了本地路径，必须内联成 data URL。"""
        client = fake_llm([{"type": "衬衫"}])
        tagging.tag_image(self._img(tmp_path, "b.png", b"89504e47"), client=client)

        content = client.calls[0]["messages"][1]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_hint_lands_in_the_prompt(self, fake_llm, tmp_path):
        client = fake_llm([{"type": "羽绒服"}])
        tagging.tag_image(self._img(tmp_path, "c.jpg"), client=client, hint="这是冬天的外套")
        text = client.calls[0]["messages"][1]["content"][0]["text"]
        assert "这是冬天的外套" in text

    def test_system_prompt_carries_the_vocabulary(self, fake_llm, tmp_path):
        """词表必须真的进 prompt —— 否则模型只能自由发挥。"""
        client = fake_llm([{"type": "T恤"}])
        tagging.tag_image(self._img(tmp_path, "d.jpg"), client=client)
        system = client.calls[0]["messages"][0]["content"]
        assert "tshirt(T恤)" in system
        assert "black(黑)" in system
        assert "loose(宽松)" in system

    def test_llm_error_propagates(self, fake_llm, tmp_path):
        """打标失败必须抛出。吞掉它会让"打标失败"和"打标结果为空"分不清。"""
        client = fake_llm(error=LLMError("连不上模型"))
        with pytest.raises(LLMError):
            tagging.tag_image(self._img(tmp_path, "e.jpg"), client=client)

    def test_non_dict_response_is_handled(self, fake_llm, tmp_path):
        """模型返回了非 dict（解析层已保证是 dict，这里防一手）。

        归一后什么都没有是对的 —— 关键是不能**编**一个 type 出来。
        """
        client = fake_llm(["我是字符串不是对象"])
        result = tagging.tag_image(self._img(tmp_path, "f.jpg"), client=client)
        assert result["tags"] == {}
        assert result["unresolved"] == {}


class TestApplyTags:
    def test_marks_source_as_ai_not_manual(self, uid):
        """**打标必须记 `tagged_by='ai'`。**
        否则 P2 的「改 AI 打错的标」就分不清原始来源了 ——
        这正是 wardrowbe 缺 `external` 枚举踩过的坑。"""
        item_id = item_svc.create(uid, ItemCreate(type="other", name="待打标单品"))["id"]

        row = tagging.apply_tags(
            uid,
            item_id,
            {
                "tags": {
                    "type": "shirt",
                    "slot": "top",
                    "colors": ["black"],
                    "primary_color": "black",
                    "fit": "loose",
                },
                "ai_confidence": 0.8,
            },
        )

        assert row is not None
        assert row["tagging_status"] == "tagged"
        assert row["tagged_by"] == "ai"
        assert row["type"] == "shirt"
        assert row["fit"] == "loose"
        assert json.loads(row["colors"]) == ["black"]
        assert row["ai_confidence"] == 0.8

    def test_empty_tags_is_a_noop(self, uid):
        assert tagging.apply_tags(uid, "it_not_exist", {"tags": {}}) is None

    def test_unknown_fields_are_filtered_out(self, uid):
        """只允许 `TAG_FIELDS` 白名单里的键落库。"""
        item_id = item_svc.create(uid, ItemCreate(type="other", name="白名单测试"))["id"]
        row = tagging.apply_tags(
            uid,
            item_id,
            {"tags": {"type": "shirt", "id": "HACK", "user_id": "HACK"}},
        )
        assert row["id"] == item_id
        # 白名单里的 `user_id` 即使被模型塞进来也不能改归属（越权防护）
        assert row["user_id"] == uid


# 真实 PNG 字节（1×1 灰点）。必须是**合法**的：`images.sniff()` 读文件头，
# 假的 `\x89PNG\r\n\x1a\n + 垃圾` 在 `save_image` 里能过 sniff，但落到磁盘上
# `read_image` 只看"文件在不在"，所以这里用能真存下来的最小 PNG。
_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3dfc0000000c4944415408d76360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def _png_bytes() -> bytes:
    """同一张图 + 不同尾巴。

    ⚠️ 尾巴必须**每个测试都不同**：`clothing_items.image_hash` 上有唯一索引
    （一张图不能录给两个单品），所以复用同一个尾巴会撞
    `sqlite3.IntegrityError: UNIQUE constraint failed`。
    用自增计数器而不是 `hash(item_id)` —— 后者取值范围有限，测试一多必撞。
    """
    _png_bytes.counter += 1
    n = _png_bytes.counter
    return _PNG_1PX + bytes([n % 251, (n * 7) % 251, (n * 13) % 251, (n * 29) % 251])


_png_bytes.counter = 0


@pytest.fixture
def vision_ready(monkeypatch):
    """把 `VISION_READY` 打开。

    ⚠️ `conftest.py` 把 `VISION_MODEL` 等四项**显式置空**，
    所以测试里的确定状态是"没配模型"（`test_without_key_is_503_not_500` 依赖它）。
    要测真正的打标链路必须在这里单独打开 —— 否则测试会静默退化成
    "只测到 skipped 分支"，看起来绿其实什么都没验。
    """
    from app import config

    monkeypatch.setattr(config, "VISION_READY", True, raising=False)
    return config


def _item_with_photo(uid, *, name="待打标", **fields):
    """建一个带照片的单品，返回 `(item_id, 原始 png 字节)`。"""
    from app.services import images as image_svc

    create = {"type": "other", "name": name}
    create.update(fields)
    item_id = item_svc.create(uid, ItemCreate(**create))["id"]
    data = _png_bytes()
    image_svc.save_image(uid, item_id, data)
    return item_id, data


class TestTagAndApplySkippedAndFailed:
    """`skipped` / `failed` 三态：**不抛异常，只回报状态**。

    这是自动路径（上传照片时顺手打标）与手动路径最本质的区别 ——
    照片已经存好了，打标炸了绝不能让上传变成 4xx/5xx。
    """

    def test_not_configured_is_skipped_not_failed(self, uid, fake_llm):
        """没配视觉模型 = `skipped`，且**一个请求都不该发出去**。"""
        client = fake_llm([{"type": "shirt"}])
        out = tagging.tag_and_apply("it_not_exist", "also_not_exist", client=client)

        assert out["status"] == "failed"  # 单品都不存在，先于 VISION_READY 判定
        assert client.calls == []

    def test_skipped_when_vision_not_ready(self, uid, fake_llm, vision_ready):
        """把 `VISION_READY` 关掉 → `skipped`，且模型**完全没被调用**。

        ⚠️ `skipped` 与 `failed` 必须分开：前者是"没配"（正常状态），
        后者是"配了但炸了"（故障）。混在一起会让排查方向跑偏。
        """
        from app import config

        config.VISION_READY = False
        item_id, _ = _item_with_photo(uid, name="没配模型")
        client = fake_llm([{"type": "shirt", "colors": ["black"]}])

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "skipped"
        assert "视觉模型" in out["message"]
        assert client.calls == [], "没配模型就不该发请求（否则每次上传都白花钱）"

    def test_model_error_is_failed_and_item_keeps_pending(self, uid, fake_llm, vision_ready):
        """模型炸了 → `failed`，但**单品必须还能手动填**（不被写坏）。"""
        item_id, _ = _item_with_photo(uid, name="模型炸了")
        client = fake_llm(error=LLMError("boom"))

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "failed"
        assert "boom" in out["message"]
        item = item_svc.get(item_id, uid)
        assert item["tagging_status"] == "pending", "打标失败不该把单品标成已打标"
        assert item["type"] == "other", "打标失败不该动用户的品类"

    def test_missing_photo_is_failed_without_calling_model(self, uid, fake_llm, vision_ready):
        """没有照片 → `failed`，**不调模型**（没有图可看，调了纯浪费钱）。"""
        item_id = item_svc.create(uid, ItemCreate(type="other", name="没照片"))["id"]
        client = fake_llm([{"type": "shirt"}])

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "failed"
        assert client.calls == []


class TestTagAndApplyApplied:
    def test_happy_path_writes_and_returns_converted_item(self, uid, fake_llm, vision_ready):
        """写回成功，且返回的 `item` 必须**是 `to_out()` 口径**。

        ⚠️ 这条是回归锁：`apply_tags` 返回的是**原始行**（`colors` 还是 JSON 文本）。
        如果 `tag_and_apply` 直接把它透出去，调用方就会拿到"字符串 `'[\"black\"]'`"
        这种逐字符消费的下游（前端 `.map` 直接炸）。`images`/`items` 都要过 `to_out()`。
        """
        item_id, _ = _item_with_photo(uid, name="白T")
        client = fake_llm([
            {"type": "T恤", "colors": ["black"], "fit": "宽松", "confidence": 0.86}
        ])

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "applied"
        assert out["tags"]["type"] == "tshirt"
        assert out["ai_confidence"] == 0.86
        # ★ 必须是**列表**，不能是 JSON 字符串
        assert out["item"]["colors"] == ["black"]
        assert isinstance(out["item"]["colors"], list)
        assert out["item"]["type"] == "tshirt"
        assert out["item"]["tagged_by"] == "ai"
        assert out["item"]["tagging_status"] == "tagged"

    def test_slot_is_derived_when_type_is_written(self, uid, fake_llm, vision_ready):
        """★ 回归锁：`slot` 必须跟 `type` 一起写，否则**这件衣服永远不进任何组合**。

        **这个 bug 真实发生过**（2026-10-10，`_only_blank` 的第一版）：
        `items.get()` 走 `to_out()`，会给缺失的 `slot` 补上列默认值 `accessory`，
        于是"只填空"逻辑判定 `slot` 已填 → 过滤掉 → 但 `type` 写了。
        结果 `type='tshirt'` / `slot='accessory'`：组合数按 slot 算，
        **一件 T 恤静默消失**，缺口分析跟着错，且全程不报错。

        P5 已在 `other` 品类上吃过同一类亏（口语品类词落 `other` → 槽位 accessory）。
        """
        item_id, _ = _item_with_photo(uid, name="槽位回归")
        client = fake_llm([{"type": "T恤", "colors": ["black"]}])

        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert out["tags"]["type"] == "tshirt"
        assert out["tags"]["slot"] == "top", "type 要写，slot 就必须重新派生"
        item = item_svc.get(item_id, uid)
        assert item["slot"] == "top", "★ 落库 slot 必须跟 type 一致"
        # ★ 这才是真正的判据：这件衣服必须真的能进组合
        assert item["slot"] != "accessory"

    def test_slot_not_written_when_type_is_not_written(self, uid, fake_llm, vision_ready):
        """`type` 不写 → `slot` 也不写（否则 type/slot 会不一致）。"""
        item_id, _ = _item_with_photo(uid, name="只填颜色", type="shirt")
        client = fake_llm([{"type": "polo", "colors": ["black"]}])

        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert "type" not in out["tags"], "`type` 用户填过了，不该被改"
        assert "slot" not in out["tags"], "`slot` 是 `type` 的派生值，type 不写就不写"
        assert out["tags"]["colors"] == ["black"]
        assert item_svc.get(item_id, uid)["type"] == "shirt", "用户填的品类不能变"


    def test_only_blank_does_not_touch_user_values(self, uid, fake_llm, vision_ready):
        """`overwrite=False`（自动路径）：**只填空，不覆盖用户已填的值**。

        这是最要紧的一条：用户辛辛苦苦填的品类不能被 AI 的猜测冲掉。
        """
        item_id, _ = _item_with_photo(uid, name="用户填过", type="shirt", fit="修身")
        client = fake_llm([
            {"type": "T恤", "fit": "宽松", "colors": ["black"], "material": "棉"}
        ])

        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert out["status"] == "applied"
        assert "fit" not in out["tags"], "`fit` 用户填过了，不该出现在待写列表里"
        item = item_svc.get(item_id, uid)
        assert item["type"] == "shirt", "用户填的品类被覆盖了"
        assert item["fit"] == "修身", "用户填的版型被覆盖了"
        assert item["material"] == "cotton", "空字段应该被补上"

    def test_overwrite_true_replaces_everything(self, uid, fake_llm, vision_ready):
        """`overwrite=True`（手动点按钮）：全量替换 —— 用户主动点的就是要新结果。"""
        item_id, _ = _item_with_photo(uid, name="手动重认", type="shirt", fit="修身")
        client = fake_llm([{"type": "polo", "fit": "宽松"}])

        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=True)

        assert out["status"] == "applied"
        item = item_svc.get(item_id, uid)
        assert item["type"] == "polo"
        assert item["fit"] == "loose"

    def test_type_other_counts_as_blank(self, uid, fake_llm, vision_ready):
        """`type='other'` 等于"还没分类"（`ItemCreate` 的默认值就是它）。

        ⚠️ 不特判的话，"录入时不选品类、靠 AI 认"这条链路会被自己堵死 ——
        用户明明什么都没填，却因为默认值是 `other` 而被判定成"已填"。
        """
        item_id, _ = _item_with_photo(uid, name="默认other", type="other")
        client = fake_llm([{"type": "T恤", "colors": ["black"]}])

        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert out["tags"].get("type") == "tshirt"
        assert item_svc.get(item_id, uid)["type"] == "tshirt"

    def test_nothing_to_fill_is_empty_not_applied(self, uid, fake_llm, vision_ready):
        """所有字段都已填 → `empty`（不是 `applied`）。

        区别很重要：`empty` 表示"模型白跑了一趟，什么都没补上"，
        前端可以据此提示"这件已经填满了"；`applied` 会让人以为改了东西。
        """
        item_id, _ = _item_with_photo(
            uid, name="已填满", type="shirt", fit="修身", material="棉"
        )
        client = fake_llm([{"type": "polo", "fit": "宽松", "material": "羊毛"}])

        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert out["status"] == "empty"
        item = item_svc.get(item_id, uid)
        assert item["type"] == "shirt", "`empty` 分支绝不能碰库"
        assert item["fit"] == "修身"

    def test_all_normalization_failed_is_empty_with_unresolved(self, uid, fake_llm, vision_ready):
        """模型给的东西**全都归一失败** → `empty`，但 `unresolved` 要带回原因。

        归一失败的值**绝不写进库**（宁缺勿脏）：库里多一条"黑白条纹"这种
        没法统计的值，当时不报错，等缺口分析出问题时已经晚了。
        """
        item_id, _ = _item_with_photo(uid, name="全不认识")
        client = fake_llm([{"type": "不存在的品类", "colors": ["荧光_chartreuse"]}])

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "empty"
        assert out["tags"] == {}
        assert out["unresolved"], "归一失败的原因要带给前端（设计文档 §10.2 承诺过）"
        item = item_svc.get(item_id, uid)
        assert item["type"] == "other", "归一失败的 type 不能落库（会掉出组合计算）"

    def test_hint_reaches_the_prompt(self, uid, fake_llm, vision_ready):
        """`hint` 必须真的进 prompt —— 否则「这是冬天的外套」这类补充信息白传。"""
        item_id, _ = _item_with_photo(uid, name="带提示")
        client = fake_llm([{"type": "coat"}])

        tagging.tag_and_apply(uid, item_id, hint="这是冬天的外套", client=client)

        assert client.calls, "模型没被调用"
        blob = json.dumps(client.calls[0]["messages"], ensure_ascii=False, default=str)
        assert "这是冬天的外套" in blob

    def test_reads_the_persisted_photo_not_a_stale_temp_file(self, uid, fake_llm, vision_ready):
        """用的是**库里那张图**（`images.read_image`），不是某个临时文件。

        回归锁：`save_image` 走 `.staging` → 更新 DB → `replace` 的顺序。
        如果打标用的是传入的临时文件，重传图时就会拿旧图/半张图去认。
        """
        from app.services import images as image_svc

        item_id, _ = _item_with_photo(uid, name="读库图")
        persisted = image_svc.read_image(item_svc.get(item_id, uid)["image_path"])
        assert persisted is not None and persisted.is_file()

        client = fake_llm([{"type": "shirt"}])
        tagging.tag_and_apply(uid, item_id, client=client)

        # prompt 里内联的是**落盘后那张图**的 base64
        blob = json.dumps(client.calls[0]["messages"], ensure_ascii=False, default=str)
        assert "data:image" in blob

    def test_db_error_does_not_escape_as_500(self, uid, fake_llm, vision_ready, monkeypatch):
        """★ 回归锁：写库炸了 → `failed`，**不许抛出去**。

        这个 bug 真实发生过（2026-10-10，W4 双审查实测复现）：
        `apply_tags` 碰数据库（`sqlite3.OperationalError` / `IntegrityError`），
        原本在 `try` 之外 → 自动路径下上传接口变 **500**。
        可**照片已经存好了、旧的也已经换掉了**，用户收到"上传失败"，
        重试同一张图还会再撞一次 `image_hash` 唯一索引。

        `tag_and_apply` 对自动路径的承诺是"**失败不抛，只回报状态**"。
        """
        item_id, _ = _item_with_photo(uid, name="写库炸了")

        def boom(*_a, **_k):
            import sqlite3

            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(tagging, "apply_tags", boom)
        client = fake_llm([{"type": "T恤", "colors": ["black"]}])

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "failed"
        assert "写库失败" in out["message"], "要如实告诉调用方是写库这一步坏了"

    def test_image_read_error_does_not_escape(self, uid, fake_llm, vision_ready, monkeypatch):
        """读图时磁盘层异常（Windows 上文件被占用 → `PermissionError`）也不能抛。"""
        from app.services import images as image_svc

        item_id, _ = _item_with_photo(uid, name="图被占用")

        def boom(_p):
            raise PermissionError(13, "文件被另一个程序占用")

        monkeypatch.setattr(image_svc, "read_image", boom)
        client = fake_llm([{"type": "T恤"}])

        out = tagging.tag_and_apply(uid, item_id, client=client)

        assert out["status"] == "failed"
        assert client.calls == [], "图都没读到，不该调模型"


class TestTagAndApplySourceIntegrity:
    """来源标记 `tagged_by` 的诚实性。

    ⚠️ 库里**没有字段级来源标记**，`tagged_by` 是整件单品的来源。
    这让它在"只填空"模式下很容易说谎。
    """

    def test_manual_item_keeps_manual_after_ai_fills_blanks(self, uid, fake_llm, vision_ready):
        """★ 回归锁：用户手填的单品，AI 补了空字段后 `tagged_by` **必须还是 `manual`**。

        这个 bug 真实发生过（2026-10-10，W4 双审查实测复现）：
        `apply_tags` 无条件写 `tagged_by='ai'`，所以一件从头手填的衣服
        （type/fit/colors 都是用户填的），AI 只补了个 `material`，
        **整件就被记成 "ai 填的"** —— 用户填的品类和版型来源被污染。
        `services/items.py` 手改时特意保留原值，方向正好相反。
        """
        item_id = item_svc.create(
            uid, ItemCreate(type="shirt", name="全手填", fit="修身", colors=["white"])
        )["id"]
        # 模拟"用户手填过"：`items.update()` 会把 tagged_by 记成 manual
        item_svc.update(item_id, ItemUpdate(name="全手填2"), uid)
        assert item_svc.get(item_id, uid)["tagged_by"] == "manual", "前置：来源应是 manual"
        from app.services import images as image_svc

        image_svc.save_image(uid, item_id, _png_bytes())

        client = fake_llm([{"material": "棉", "primary_color": "black"}])
        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert out["status"] == "applied"
        item = item_svc.get(item_id, uid)
        assert item["material"] == "cotton", "空字段应该被补上"
        assert item["tagged_by"] == "manual", "★★ 用户手填的单品，来源不能被翻成 ai"
        assert item["type"] == "shirt"
        assert item["fit"] == "修身"

    def test_overwrite_marks_ai_even_on_manual_item(self, uid, fake_llm, vision_ready):
        """手动点按钮（全量覆盖）→ 来源记 `ai`，因为这次结果就是 AI 给的。"""
        item_id = item_svc.create(
            uid, ItemCreate(type="shirt", name="手动重认", fit="修身")
        )["id"]
        item_svc.update(item_id, ItemUpdate(name="手动重认2"), uid)
        assert item_svc.get(item_id, uid)["tagged_by"] == "manual"
        from app.services import images as image_svc

        image_svc.save_image(uid, item_id, _png_bytes())

        client = fake_llm([{"type": "polo", "fit": "宽松"}])
        tagging.tag_and_apply(uid, item_id, client=client, overwrite=True)

        item = item_svc.get(item_id, uid)
        assert item["type"] == "polo"
        assert item["tagged_by"] == "ai", "全量覆盖的结果就是 AI 的，记 ai"


class TestOnlyBlankFieldConsistency:
    """字段**之间**的一致性（逐字段判空看不出来的那类问题）。"""

    def test_stale_slot_is_repaired_even_when_type_unchanged(self, uid, fake_llm, vision_ready):
        """★ 回归锁：库里 `type` 与 `slot` 对不上时，自动打标要**顺手修好**。

        这个 bug 真实发生过（2026-10-10，W4 双审查实测复现）：
        构造 `type=shirt` / `slot=accessory` 的脏数据，AI 认出了 `tshirt`，
        但 `type` 非空 → 不写；`slot` 被 pop → 也不写。**不一致永久留下**。

        代价不是理论上的：`wardrobe._by_slot()` 直接按 `it["slot"]` 分组算
        组合数（`services/wardrobe.py:89`），所以这件衣服**永远不进任何组合**，
        缺口分析和补货建议一起错，全程不报错。
        """
        from app import db
        from app.services import images as image_svc

        item_id = item_svc.create(uid, ItemCreate(type="shirt", name="脏slot"))["id"]
        with db.conn() as c:
            c.execute("UPDATE clothing_items SET slot='accessory' WHERE id=?", (item_id,))
        assert item_svc.get(item_id, uid)["slot"] == "accessory", "前置：脏数据构造成功"
        image_svc.save_image(uid, item_id, _png_bytes())

        client = fake_llm([{"colors": ["black"]}])
        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        item = item_svc.get(item_id, uid)
        assert out["tags"]["slot"] == "top"
        assert item["slot"] == "top", "★★ 脏 slot 必须被修好，否则这件衣服永不入组合"
        assert item["type"] == "shirt", "type 是用户填的，不该被改"

    def test_consistent_slot_produces_no_extra_write(self, uid, fake_llm, vision_ready):
        """`slot` 已经一致时**不产生**多余的 slot 写入（避免无意义的 UPDATE）。"""
        from app.services import images as image_svc

        item_id = item_svc.create(uid, ItemCreate(type="shirt", name="slot一致"))["id"]
        assert item_svc.get(item_id, uid)["slot"] == "top", "前置：shirt 的槽位就是 top"
        image_svc.save_image(uid, item_id, _png_bytes())

        client = fake_llm([{"colors": ["black"]}])
        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert "slot" not in out["tags"], "一致时不该重复写 slot"

    def test_pattern_other_counts_as_blank(self, uid, fake_llm, vision_ready):
        """★ 回归锁：`pattern='other'` 视作"还没填"。

        `taxonomy.PATTERNS` 里有 `"other": "其他图案"` —— 语义是"我没意见"，
        不是用户真的选了"其他图案"。原本只对 `type` 特判，导致 AI 认出了
        "条纹"却因为这个默认值而永远补不上（实测复现）。
        """
        from app import db
        from app.services import images as image_svc

        item_id = item_svc.create(uid, ItemCreate(type="tshirt", name="图案other"))["id"]
        with db.conn() as c:
            c.execute("UPDATE clothing_items SET pattern='other' WHERE id=?", (item_id,))
        image_svc.save_image(uid, item_id, _png_bytes())

        client = fake_llm([{"pattern": "条纹"}])
        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert out["tags"]["pattern"] == "striped", "AI 认出的图案应该写得进去"
        assert item_svc.get(item_id, uid)["pattern"] == "striped"

    def test_primary_color_contradicting_user_colors_is_dropped(self, uid, fake_llm, vision_ready):
        """★ 回归锁：`primary_color` 必须属于 `colors`。

        逐字段判空看不出这类矛盾：用户填了 `colors=['black']`（非空 → AI 的
        colors 被跳过），AI 认出 `primary_color='navy'`（空 → 写入），
        结果「主色不属于配色」。`taxonomy._stats()` 用 `primary_color`
        统计颜色多样性，分母会算错且不报错。

        裁决：**以用户的 `colors` 为准**，丢弃矛盾的 `primary_color`
        （它本来就只是 `colors[0]` 的派生值）。
        """
        from app.services import images as image_svc

        item_id = item_svc.create(
            uid, ItemCreate(type="tshirt", name="主色矛盾", colors=["black"])
        )["id"]
        image_svc.save_image(uid, item_id, _png_bytes())

        client = fake_llm([{"primary_color": "navy"}])
        out = tagging.tag_and_apply(uid, item_id, client=client, overwrite=False)

        assert "primary_color" not in out["tags"], "主色不在配色里，应该被丢弃"
        item = item_svc.get(item_id, uid)
        assert item["colors"] == ["black"], "用户的配色不能被改"
        assert item["primary_color"] in (None, "", "black"), "★ 不能留下矛盾的主色"


class TestMultiWordColorValues:
    """多词颜色名（`light blue`）在**多值入口**必须整词识别。

    ⚠️ 这个 bug 真实发生过（2026-10-10，W4 双审查实测复现）：
    `_as_list` 按空白切分（模型爱写 `"black, white"`），于是合法的枚举值
    `light_blue`（浅蓝）被切成 `["light", "blue"]` → `light` 认不出进
    `unresolved`，而 `blue` **静默写进库** → **浅蓝被记成蓝**，不报错。
    同一份词表走单值入口能正确认出 `light_blue`，只有多值入口会错。
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("light blue", ["light_blue"]),
            ("off white", None),      # 词表里没有 off_white，不该硬凑
            ("navy blue", ["navy", "blue"]),
            ("black, white", ["black", "white"]),
            ("black white", ["black", "white"]),
        ],
    )
    def test_whole_word_wins_over_whitespace_split(self, raw, expected):
        clean, _ = tagging.normalize_tags({"colors": raw})
        got = clean.get("colors")
        if expected is None:
            # 没有整词命中，才允许退回逐块匹配（这里是 'off'/'white'）
            assert got is None or "off_white" not in got, "不该凭空造出 off_white"
        else:
            assert got == expected, f"{raw!r} → {got!r}，期望 {expected!r}"

    def test_light_blue_is_not_silently_downgraded_to_blue(self):
        """★ 核心断言：浅蓝绝不能变成蓝。"""
        clean, failed = tagging.normalize_tags({"colors": "light blue"})

        assert clean["colors"] == ["light_blue"], "浅蓝必须被整词认出"
        assert "blue" not in clean["colors"], "★★ 浅蓝被静默降级成蓝了"
        assert "light" not in failed.get("colors", []), "不该把 'light' 当成认不出的噪声"
