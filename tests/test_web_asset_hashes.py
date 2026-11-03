"""发布资源摘要保留二进制字节，只对文本统一换行。"""
from scripts.checks import web_assets


def test_binary_newlines_are_part_of_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(web_assets, "WEB", tmp_path)
    sample = tmp_path / "sample.webp"
    sample.write_bytes(b"RIFF\r\nWEBP")
    original = web_assets.generated(["/sample.webp"])
    sample.write_bytes(b"RIFF\nWEBP")
    assert web_assets.generated(["/sample.webp"]) != original


def test_text_newlines_are_normalized(tmp_path, monkeypatch):
    monkeypatch.setattr(web_assets, "WEB", tmp_path)
    sample = tmp_path / "sample.js"
    sample.write_bytes(b"const a = 1;\r\n")
    original = web_assets.generated(["/sample.js"])
    sample.write_bytes(b"const a = 1;\n")
    assert web_assets.generated(["/sample.js"]) == original

