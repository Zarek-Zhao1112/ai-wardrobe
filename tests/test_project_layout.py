"""目录迁移的有意义边界：越界构建、过期副本、中文索引与安全维护。"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
import time
import pytest
from fastapi.testclient import TestClient
from android.tools.sync_sources import sync_sources, validate_build
from scripts.checks import scan_leaks, web_assets, check_contrast
from scripts.bridge import desktop_bridge, manage


def test_offline_install_is_complete_and_preserves_active_cache():
    node = shutil.which("node")
    if not node:
        pytest.skip("需要Node22+执行SW内存回归")
    result = subprocess.run([node, str(Path(__file__).parent / "frontend/shell_cache_check.cjs")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["passed"] == 5


def test_source_sync_removes_stale_paths_but_preserves_server_and_settings(tmp_path):
    repo, build = tmp_path / "source", tmp_path / "build"
    (repo / "web/js/core").mkdir(parents=True)
    (repo / "app/__pycache__").mkdir(parents=True)
    (repo / "web/js/core/app.js").write_text("export const version=47")
    (repo / "app/main.py").write_text("VALUE=47")
    (repo / "app/__pycache__/main.pyc").write_bytes(b"host bytecode")
    base = build / "app/src/main/python"
    (base / "web/js").mkdir(parents=True)
    (base / "web/js/app.js").write_text("old path")
    (base / "server.py").write_text("native entry")
    (base / ".env").write_text("local settings")
    sync_sources(repo, build)
    assert not (base / "web/js/app.js").exists()
    assert (base / "web/js/core/app.js").read_text() == "export const version=47"
    assert not (base / "app/__pycache__").exists()
    assert (base / "server.py").read_text() == "native entry"
    assert (base / ".env").read_text() == "local settings"
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "android/tools/sync_sources.py"),
                    str(repo), str(build), "--clear-env"], check=True)
    assert not (base / ".env").exists()
    assert (base / "server.py").read_text() == "native entry"


@pytest.mark.parametrize("which", ["same", "inside", "parent"])
def test_build_preflight_refuses_source_overlap_before_writes(tmp_path, which):
    repo = tmp_path / "source"
    repo.mkdir()
    target = {"same": repo, "inside": repo / "build", "parent": tmp_path}[which]
    with pytest.raises(ValueError):
        validate_build(repo, target)
    assert list(repo.iterdir()) == []


def test_asset_checker_detects_missing_nested_module_and_html_resource(tmp_path):
    (tmp_path / "js/core").mkdir(parents=True)
    (tmp_path / "js/core/app.js").write_text('import "../features/view.js";')
    (tmp_path / "index.html").write_text('<script src="/js/missing.js"></script>')
    _, errors = web_assets.inspect(tmp_path)
    assert len(errors) == 2
    (tmp_path / "js/features").mkdir()
    (tmp_path / "js/features/view.js").write_text("export const ready=true;")
    (tmp_path / "js/missing.js").write_text("// now present")
    assets, errors = web_assets.inspect(tmp_path)
    assert errors == [] and "/js/features/view.js" in assets


def test_secret_gate_reads_unicode_staged_content_not_clean_worktree(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "中文文档.md"
    secret = "sk-" + "A" * 24
    path.write_text(secret, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", path.name], check=True)
    path.write_text("working tree fixed, index still contains the test secret", encoding="utf-8")
    monkeypatch.setattr(scan_leaks, "ROOT", tmp_path)
    monkeypatch.setattr(scan_leaks, "DEFAULT_NEEDLES", tmp_path / "absent")
    monkeypatch.setattr(sys, "argv", ["scan_leaks.py"])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert scan_leaks.main() == 1
    assert "中文文档.md" in output.getvalue() and secret not in output.getvalue()


def test_secret_gate_fails_closed_on_git_error(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_leaks, "ROOT", tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        scan_leaks.tracked_files()


def test_contrast_gate_rejects_low_contrast_palette(tmp_path, monkeypatch):
    path = tmp_path / "tokens.css"
    text = check_contrast.TOKENS.read_text(encoding="utf-8").replace("#1d1d1f", "#ffffff")
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(check_contrast, "TOKENS", path)
    with contextlib.redirect_stdout(io.StringIO()):
        assert check_contrast.main() == 1


@pytest.mark.parametrize("folder,name,field", [("flatlay", "state.json", "state"), ("references", "job.json", "status")])
def test_stop_refuses_running_jobs_for_both_layouts(tmp_path, folder, name, field):
    path = tmp_path / folder / "test" / name
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({field: "running"}))
    with pytest.raises(ValueError, match="任务"):
        manage.ensure_idle(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows argv身份校验")
def test_stop_matches_exact_script_state_and_port(tmp_path):
    state = tmp_path / "本机状态"
    config = {"state": state, "port": 8891}
    def row(script, state_path, port):
        return {"Name": "python.exe", "CommandLine": subprocess.list2cmdline([
            "C:/Python/python.exe", "-u", str(script), "--state", str(state_path), "--port", str(port)])}
    script = manage.ROOT / "scripts/bridge/desktop_bridge.py"
    assert manage.owned_process(row(script, state, 8891), config)
    assert not manage.owned_process(row(script, str(state) + "-test", 8891), config)
    assert not manage.owned_process(row(str(script) + ".bak", state, 8891), config)
    assert not manage.owned_process(row(script, state, 8892), config)


def test_bridge_drain_is_authenticated_and_resumable(tmp_path):
    executable = tmp_path / "fake"
    executable.touch()
    app = desktop_bridge.create_app(executable, "synthetic-token")
    with TestClient(app) as client:
        assert client.post("/admin/drain").status_code == 403
        client.headers["Authorization"] = "Bearer synthetic-token"
        assert client.post("/admin/drain").status_code == 200
        assert client.get("/health").json()["status"] == "maintenance"
        assert client.post("/chat", json={}).status_code == 503
        assert client.post("/admin/resume").status_code == 200
        assert client.post("/admin/resume").status_code == 200
        assert client.get("/health").json()["status"] == "ready"


def test_busy_drain_returns_immediately_and_never_later_pauses_service(tmp_path):
    executable = tmp_path / "fake"
    executable.touch()
    entered, release = threading.Event(), threading.Event()
    def runner(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return "synthetic"
    app = desktop_bridge.create_app(executable, "synthetic-token", runner=runner)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer synthetic-token"
        payload = {"model": desktop_bridge.MODEL, "messages": [{"role": "user", "content": "合成测试"}]}
        result = []
        thread = threading.Thread(target=lambda: result.append(client.post("/chat", json=payload)))
        thread.start()
        assert entered.wait(3)
        try:
            start = time.monotonic()
            assert client.post("/admin/drain").status_code == 409
            assert time.monotonic() - start < 1
        finally:
            release.set()
            thread.join(5)
        assert result[0].status_code == 200
        assert client.get("/health").json()["status"] == "ready"
