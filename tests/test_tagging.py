"""`tagging` 的单元测试。

重点是**归一**：它错了会静默 —— 库里多一条"黑白条纹"这种没法统计的值，
当时不会报错，等到缺口分析和偏好统计出问题时已经晚了。
所以这里用大量"脏输入"去撞它。
"""

from __future__ import annotations

import json

import pytest

from app.models import ItemCreate
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
        assert row["user_id"] == uid
