"""手机在「账号额度 / API 直连」之间切换：中转分流 + 手机侧保存。

两条路都经电脑出网，区别只在电脑用哪个上游：
    account → 个人 CLI → 账号额度
    api     → 电脑直调已配置的模型
"""
import base64
import json

from fastapi.testclient import TestClient
import pytest

from app import config
from app.services.llm import desktop
from scripts.bridge import desktop_bridge as bridge

TOKEN = "test_pairing_" + "x" * 32
AUTH = {"Authorization": "Bearer " + TOKEN}
MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "这件是什么"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"fake").decode()}},
]}]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def bridge_api(tmp_path, runner):
    exe = tmp_path / "fake-cli.exe"
    exe.touch()
    return TestClient(bridge.create_app(exe, TOKEN, runner))


def test_api_engine_does_not_touch_the_cli(isolated, monkeypatch):
    seen = []

    def fake_api(profile, messages, schema, json_output):
        seen.append({"profile": profile, "schema": schema, "json": json_output})
        return '{"type":"大衣"}'

    monkeypatch.setattr(bridge, "api_profile", lambda: {"base_url": "http://x/v1", "key": "k", "model": "gpt-6-luna", "proxy": None})
    monkeypatch.setattr(bridge, "call_api", fake_api)
    c = bridge_api(isolated, lambda *a, **k: pytest.fail("选了 API 直连就不该再起 CLI"))
    r = c.post("/chat", headers=AUTH, json={
        "model": bridge.MODEL, "engine": "api", "messages": MESSAGES,
        "json_output": True, "schema": {"type": "object", "properties": {"type": {"type": "string"}}},
    })
    assert r.status_code == 200
    body = r.json()
    # 回来的模型名必须是电脑侧真实用的那个，前端要靠它显示。
    assert body["model"] == "gpt-6-luna" and body["engine"] == "api"
    assert body["text"] == '{"type":"大衣"}'
    assert seen and seen[0]["json"] is True


def test_missing_engine_keeps_old_apk_working(isolated):
    seen = []
    c = bridge_api(isolated, lambda *a, **k: seen.append(1) or '{"type":"tshirt"}')
    r = c.post("/chat", headers=AUTH, json={"model": bridge.MODEL, "messages": MESSAGES, "json_output": True})
    assert r.status_code == 200
    # 老 APK 的请求体里没有 engine 字段，必须仍然走账号额度那条。
    assert r.json()["engine"] == "account" and seen


def test_api_engine_without_profile_is_a_readable_error(isolated, monkeypatch):
    monkeypatch.setattr(bridge, "api_profile", lambda: None)
    c = bridge_api(isolated, lambda *a, **k: '{"type":"tshirt"}')
    r = c.post("/chat", headers=AUTH, json={
        "model": bridge.MODEL, "engine": "api", "messages": MESSAGES, "json_output": False,
    })
    assert r.status_code == 503 and "密钥" in r.json()["detail"]


def test_health_lists_both_engines(isolated, monkeypatch):
    monkeypatch.setattr(bridge, "api_profile", lambda: {"base_url": "http://x/v1", "key": "k", "model": "gpt-6-luna", "proxy": None})
    c = bridge_api(isolated, lambda *a, **k: "ok")
    engines = c.get("/health", headers=AUTH).json()["engines"]
    assert engines == {"account": bridge.MODEL, "api": "gpt-6-luna"}


def test_legacy_config_defaults_to_account(isolated):
    desktop.settings_path().write_text(json.dumps({"url": "http://127.0.0.1:8891", "token": TOKEN}))
    # 手机上升级前写下的配置没有 engine 字段，不能因此变成"配置损坏"。
    assert desktop.load_settings()["engine"] == desktop.ACCOUNT


def test_set_engine_persists_and_reports_new_model(isolated, monkeypatch):
    def health(settings, path, **kwargs):
        return {"protocol": 1, "status": "ready", "engines": {"account": desktop.MODEL, "api": "gpt-6-luna"}}

    monkeypatch.setattr(desktop, "bridge_request", health)
    desktop.settings_path().write_text(json.dumps({"url": "http://127.0.0.1:8891", "token": TOKEN}))
    status = desktop.set_engine(desktop.API)
    assert status["engine"] == "api" and status["model"] == "gpt-6-luna"
    assert json.loads(desktop.settings_path().read_text())["engine"] == "api"
    # 切回去也要能切。
    assert desktop.set_engine(desktop.ACCOUNT)["model"] == desktop.MODEL


def test_legacy_config_backfills_api_engine(isolated, monkeypatch):
    """升级 APK 前保存的配置没有 api_model —— 必须自己补探一次。

    不补探的话「API 直连」永远不会出现，而用户没有任何理由重新配对一次，
    等于升级后功能凭空消失。
    """
    def health(settings, path, **kwargs):
        return {"protocol": 1, "status": "ready", "engines": {"account": desktop.MODEL, "api": "gpt-6-luna"}}

    monkeypatch.setattr(desktop, "bridge_request", health)
    monkeypatch.setattr(desktop, "_probe_gate", {"at": 0.0})
    desktop.settings_path().write_text(json.dumps({"url": "http://127.0.0.1:8891", "token": TOKEN}))
    status = desktop.bridge_status()
    assert status["engines"][desktop.API] == "gpt-6-luna"
    # 结果要落盘，否则每次 /health 都得连一次电脑。
    assert json.loads(desktop.settings_path().read_text())["api_model"] == "gpt-6-luna"


def test_bridge_status_survives_bridge_offline(isolated, monkeypatch):
    """电脑关着时 /health 不能因为补探而报错或抛异常。"""
    def offline(*args, **kwargs):
        raise desktop.LLMError("offline", code="bridge_offline")

    monkeypatch.setattr(desktop, "bridge_request", offline)
    monkeypatch.setattr(desktop, "_probe_gate", {"at": 0.0})
    desktop.settings_path().write_text(json.dumps({"url": "http://127.0.0.1:8891", "token": TOKEN}))
    status = desktop.bridge_status()
    assert status["configured"] is True
    assert status["engines"][desktop.ACCOUNT] == desktop.MODEL
    assert status["engines"][desktop.API] is None


def test_set_engine_refuses_when_bridge_cannot_do_api(isolated, monkeypatch):
    monkeypatch.setattr(desktop, "bridge_request", lambda *a, **k: {"protocol": 1, "status": "ready"})
    desktop.settings_path().write_text(json.dumps({"url": "http://127.0.0.1:8891", "token": TOKEN}))
    with pytest.raises(ValueError):
        desktop.set_engine(desktop.API)
    # 拒绝之后不能把配置改成一半 —— 仍然是原来那条。
    assert json.loads(desktop.settings_path().read_text()).get("engine", desktop.ACCOUNT) == desktop.ACCOUNT
