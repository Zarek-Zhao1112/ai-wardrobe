"""§19：中转边界与无回退；所有CLI与网络由测试替身接管。"""
import base64
import asyncio
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from app import config
from app.services.llm import client, desktop
from scripts import desktop_bridge as bridge

TOKEN = "test_pairing_" + "x" * 32
MESSAGES = [{"role": "system", "content": "衣服识别"}, {"role": "user", "content": [
    {"type": "text", "text": "目标裁剪图，再看原图的选区 x=.1 y=.2 w=.3 h=.4"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"synthetic-crop").decode()}},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"synthetic-original").decode()}},
]}]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def api(tmp_path, runner=lambda *a, **k: '{"type":"tshirt"}'):
    exe = tmp_path / "fake-cli.exe"
    exe.touch()
    return TestClient(bridge.create_app(exe, TOKEN, runner)), {"Authorization": "Bearer " + TOKEN}


def test_auth_health_and_no_secret(isolated):
    c, headers = api(isolated)
    assert c.get("/health").status_code == 403
    assert c.get("/health", headers={**headers, "Origin": "https://outside.example"}).status_code == 403
    response = c.get("/health", headers=headers)
    assert response.status_code == 200 and response.json()["model"] == bridge.MODEL
    assert TOKEN not in response.text


def test_dual_images_and_schema_are_preserved(isolated):
    seen = []
    c, headers = api(isolated, lambda *args, **kwargs: seen.append((args, kwargs)) or '{"type":"tshirt"}')
    response = c.post("/chat", headers=headers, json={"model": bridge.MODEL, "messages": MESSAGES, "json_output": True, "schema": {"type": "object"}})
    assert response.status_code == 200
    system, content = seen[0][0][1:]
    assert "衣服识别" in system and '"type": "object"' in system
    assert [base64.b64decode(p["source"]["data"]) for p in content if p["type"] == "image"] == [b"synthetic-crop", b"synthetic-original"]
    assert "x=.1" in content[0]["text"] and seen[0][1]["json_output"] is True


@pytest.mark.parametrize("messages", [[], [{"role": "assistant", "content": "x"}], [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}], [MESSAGES[1], MESSAGES[1]]])
def test_rejects_untrusted_input(messages):
    with pytest.raises(HTTPException):
        bridge.convert_messages(messages)


def test_model_fixed_and_malformed_body(isolated):
    c, headers = api(isolated)
    assert c.post("/chat", headers=headers, json={"model": "glm", "messages": MESSAGES}).status_code == 400
    assert c.post("/chat", headers={**headers, "Content-Type": "application/json"}, content="[").status_code == 400


def test_busy_returns_without_second_cli(isolated):
    started, release = threading.Event(), threading.Event()
    calls = []
    def runner(*args, **kwargs):
        calls.append(1); started.set(); release.wait(5)
        return "{}"
    c, headers = api(isolated, runner)
    body = {"model": bridge.MODEL, "messages": MESSAGES}
    thread = threading.Thread(target=lambda: c.post("/chat", headers=headers, json=body))
    thread.start()
    assert started.wait(3)
    try:
        assert c.post("/chat", headers=headers, json=body).status_code == 429
        assert calls == [1]
    finally:
        release.set(); thread.join(5)


def test_stalled_upload_times_out_and_unlocks(monkeypatch, isolated):
    monkeypatch.setattr(bridge, "UPLOAD_TIMEOUT", .01)
    exe = isolated / "cli.exe"
    app = bridge.create_app(exe, TOKEN, lambda *a, **k: "{}")
    endpoint = next(route.endpoint for route in app.routes if route.path == "/chat")
    class Stalled:
        headers = {"content-type": "application/json"}
        async def stream(self):
            yield b"{"
            await asyncio.sleep(10)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(endpoint(Stalled()))
    assert caught.value.status_code == 504
    c = TestClient(app)
    response = c.post("/chat", headers={"Authorization": "Bearer " + TOKEN}, json={"model": bridge.MODEL, "messages": MESSAGES})
    assert response.status_code == 200


def test_cli_flags_environment_and_result(monkeypatch, isolated):
    monkeypatch.setenv("CODEBUDDY_API_KEY", "should-not-inherit")
    monkeypatch.setenv("LLM_API_KEY", "should-not-inherit")
    events = [{"type": "system", "subtype": "init", "model": bridge.MODEL, "apiKeySource": "copilot.tencent.com"}, {"type": "result", "subtype": "success", "is_error": False, "result": '{"type":"tshirt"}'}]
    def run(args, **kwargs):
        assert args[args.index("--tools") + 1] == ""
        assert args[args.index("--model") + 1] == bridge.MODEL
        assert args[args.index("--setting-sources") + 1] == ""
        assert "--no-session-persistence" in args and "--strict-mcp-config" in args
        assert "CODEBUDDY_API_KEY" not in kwargs["env"] and "LLM_API_KEY" not in kwargs["env"]
        assert not list(Path(kwargs["cwd"]).iterdir())
        assert json.loads(kwargs["input"])["message"]["content"] == [{"type": "text", "text": "x"}]
        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(e) for e in events).encode(), stderr=b"")
    monkeypatch.setattr(subprocess, "run", run)
    assert bridge.execute_cli(isolated / "cli.exe", "system", [{"type": "text", "text": "x"}], json_output=True) == '{"type":"tshirt"}'


@pytest.mark.parametrize("diagnostic,status", [(b"Authentication required", 401), (b"Insufficient credits", 402), (b"internal error secret", 502)])
def test_cli_failure_safe(monkeypatch, isolated, diagnostic, status):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=b"", stderr=diagnostic))
    with pytest.raises(HTTPException) as caught:
        bridge.execute_cli(isolated / "cli.exe", "", [], json_output=True)
    assert caught.value.status_code == status and "secret" not in caught.value.detail


def test_cli_timeout(monkeypatch, isolated):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("cli", 75)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(HTTPException) as caught:
        bridge.execute_cli(isolated / "cli.exe", "", [], json_output=True)
    assert caught.value.status_code == 504


@pytest.mark.parametrize("change", [None, "no_init", "wrong_model", "wrong_source", "cancelled", "aborted_streaming", "aborted_tools", "is_error", "empty", "truncated", "later_error"])
def test_cli_complete_result_can_survive_exit_timeout(monkeypatch, isolated, capsys, change):
    init={"type":"system","subtype":"init","model":bridge.MODEL,"apiKeySource":"copilot.tencent.com"}
    final={"type":"result","subtype":"success","is_error":False,"result":'{"type":"tshirt"}'}
    if change=="wrong_model":init['model']='other'
    if change=="wrong_source":init['apiKeySource']='other'
    if change=="cancelled":final['cancelled']=True
    if change in ('aborted_streaming','aborted_tools'):final['terminal_reason']=change
    if change=="is_error":final['is_error']=True
    if change=="empty":final['result']=' '
    events=([init] if change!='no_init' else [])+[final]
    if change=="later_error":events.append({"type":"result","subtype":"error","is_error":True})
    output='\n'.join(json.dumps(e) for e in events).encode()
    if change=="truncated":output=output[:-7]
    calls=[]
    def run(*args,**kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired('cli',75,output=output,stderr=b'private secret')
    monkeypatch.setattr(subprocess,'run',run)
    if change is None:
        assert bridge.execute_cli(isolated/'cli','private prompt',[],json_output=True)=='{"type":"tshirt"}'
    else:
        with pytest.raises(HTTPException) as error:bridge.execute_cli(isolated/'cli','private prompt',[],json_output=True)
        assert error.value.status_code==504
    assert calls==[1]
    record=capsys.readouterr().out
    assert 'private' not in record and 'tshirt' not in record and 'copilot.tencent' not in record
    assert json.loads(record)['result_recovered']==(change is None)


@pytest.mark.skipif(bridge.os.name != "nt", reason="Windows命令行限制")
def test_long_system_rejected_before_spawn(monkeypatch, isolated):
    def unexpected(*a, **k):
        raise AssertionError("不应启动进程")
    monkeypatch.setattr(subprocess, "run", unexpected)
    with pytest.raises(HTTPException) as caught:
        bridge.execute_cli(isolated / "cli.exe", "文" * 40000, [{"type": "text", "text": "x"}], json_output=True)
    assert caught.value.status_code == 413


@pytest.mark.parametrize("url", ["http://8.8.8.8:8891", "http://localhost:8891", "http://192.168.1.2:8891/secret", "http://user@192.168.1.2:8891", "https://192.168.1.2:8891", "http://169.254.169.254:80"])
def test_url_restrictions(url):
    with pytest.raises(ValueError):
        desktop.validate_settings(url, TOKEN)


def test_config_atomic_no_fallback_and_no_secret(monkeypatch, isolated):
    monkeypatch.setattr(desktop, "bridge_request", lambda *a, **k: {"status": "ready", "protocol": 1, "model": bridge.MODEL})
    result = desktop.save_settings("http://192.168.1.2:8891", TOKEN)
    assert result["configured"] and TOKEN not in json.dumps(result)
    assert desktop.load_settings()["token"] == TOKEN
    assert client.model_ready(vision=True)
    assert isinstance(client.get_client(vision=True), desktop.DesktopClient)
    desktop.settings_path().write_text("invalid")
    with pytest.raises(client.LLMError) as caught:
        client.get_client(vision=True)
    assert caught.value.code == "bridge_config"
    assert desktop.bridge_status()["invalid"]


def test_failed_connect_keeps_old_config(monkeypatch, isolated):
    previous = {"url": "http://127.0.0.1:8891", "token": TOKEN}
    desktop.settings_path().write_text(json.dumps(previous))
    def offline(*args, **kwargs):
        raise client.LLMError("offline", code="bridge_offline")
    monkeypatch.setattr(desktop, "bridge_request", offline)
    with pytest.raises(client.LLMError):
        desktop.save_settings("http://192.168.1.2:8891", TOKEN)
    assert desktop.load_settings() == previous


@pytest.mark.parametrize("status,code", [(401, "bridge_login"), (403, "bridge_pairing"), (402, "insufficient_balance"), (429, "bridge_busy"), (504, "bridge_timeout"), (302, "bridge_unavailable")])
def test_http_error_mapping(monkeypatch, status, code):
    class FakeHTTP:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def request(self, *a, **k): return httpx.Response(status, text="never expose private upstream")
    monkeypatch.setattr(httpx, "Client", FakeHTTP)
    with pytest.raises(client.LLMError) as caught:
        desktop.bridge_request({"url": "http://127.0.0.1:8891", "token": TOKEN}, "/chat", payload={})
    assert caught.value.code == code and "private" not in str(caught.value)


@pytest.mark.parametrize("path,payload,read", [("/health", None, 6), ("/chat", {}, 90), ("/flatlay/jobs/a", None, 20)])
@pytest.mark.parametrize("failure,code", [(httpx.ConnectTimeout, "bridge_offline"), (httpx.ReadTimeout, "bridge_timeout")])
def test_connection_timeout_is_separate_and_never_retries(monkeypatch, path, payload, read, failure, code):
    calls = []
    class FakeHTTP:
        def __init__(self, **kwargs):
            limit = kwargs["timeout"]
            assert limit.connect == limit.pool == 4
            assert limit.read == read
            assert limit.write == (20 if payload is not None else 6)
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, *args, **kwargs):
            calls.append(1)
            raise failure("private upstream diagnostic")
    monkeypatch.setattr(httpx, "Client", FakeHTTP)
    with pytest.raises(client.LLMError) as caught:
        desktop.bridge_request({"url": "http://192.168.1.2:8891", "token": TOKEN}, path, payload=payload)
    assert caught.value.code == code
    assert len(calls) == 1
    assert TOKEN not in str(caught.value) and "private upstream" not in str(caught.value)
    if failure is httpx.ConnectTimeout:
        assert "地址是否已变化" in str(caught.value)


def test_settings_rejects_rebinding_and_cross_site(monkeypatch, isolated):
    from app.main import app
    from app.routes import meta
    calls = []
    monkeypatch.setattr(meta, "save_settings", lambda *args: calls.append(args) or {"configured": True})
    body = {"url": "http://192.168.1.2:8891", "token": TOKEN}
    with TestClient(app, base_url="http://127.0.0.1:8819") as c:
        assert c.put("/settings/desktop-bridge", json=body, headers={"Host": "rebind.example:8819", "Origin": "http://rebind.example:8819"}).status_code == 403
        assert c.put("/settings/desktop-bridge", json=body, headers={"Origin": "https://outside.example"}).status_code == 403
        assert not calls
        assert c.put("/settings/desktop-bridge", json=body, headers={"Origin": "http://127.0.0.1:8819"}).status_code == 200
        assert len(calls) == 1
