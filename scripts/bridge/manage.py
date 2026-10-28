"""电脑中转统一入口；配置/状态/CLI依赖留在忽略目录，不读取账号凭据。"""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.bridge.desktop_bridge import BUILD


def path_value(value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def load_config(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    port = raw.get("port", 8891)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError("中转端口须为1024到65535的整数")
    cli = raw.get("cli", "node")
    cli = shutil.which(cli) if not Path(cli).is_absolute() else cli
    if not cli or not Path(cli).is_file():
        raise ValueError("找不到CLI运行程序，请检查本机bridge.json")
    entry, state = path_value(raw["cli_entry"]), path_value(raw.get("state", ".local/runtime/desktop-bridge"))
    if not entry.is_file():
        raise ValueError("找不到官方CLI入口，请先安装本机CLI依赖")
    return {"cli": str(Path(cli).resolve()), "cli_entry": str(entry), "state": state, "port": port}


def health(config: dict) -> dict | None:
    try:
        token = json.loads((config["state"] / "pairing.json").read_text(encoding="utf-8"))["token"]
        response = httpx.get(f"http://127.0.0.1:{config['port']}/health", headers={"Authorization": "Bearer " + token}, timeout=2, trust_env=False)
        return response.json() if response.status_code == 200 else None
    except (OSError, KeyError, ValueError, httpx.HTTPError):
        return None


def windows_processes() -> list[dict]:
    command = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, encoding="utf-8", check=True, timeout=20,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = json.loads(result.stdout)
    return rows if isinstance(rows, list) else [rows]


def command_args(command: str) -> list[str]:
    from ctypes import wintypes
    count = ctypes.c_int()
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    pointer = split(command, ctypes.byref(count))
    if not pointer:
        return []
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def owned_process(row: dict, config: dict) -> bool:
    if row.get("Name", "").lower() != "python.exe":
        return False
    args = command_args(row.get("CommandLine") or "")
    script_index = 2 if len(args) > 2 and args[1] == "-u" else 1
    if len(args) <= script_index or args.count("--state") != 1:
        return False
    try:
        script = Path(args[script_index]).resolve()
        state = Path(args[args.index("--state") + 1]).resolve()
        port = int(args[args.index("--port") + 1]) if "--port" in args else 8891
    except (ValueError, IndexError, OSError):
        return False
    return script == ROOT / "scripts/bridge/desktop_bridge.py" and state == config["state"] and port == config["port"]


def ensure_idle(state: Path) -> None:
    for path in [*state.glob("references/*/job.json"), *state.glob("flatlay/*/state.json")]:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("status", record.get("state")) in {"queued", "running"}:
            raise ValueError("还有识别/生图/搜图任务在运行，请结束后再停止中转")


def stop(config: dict) -> None:
    state = config["state"]
    ensure_idle(state)
    if os.name != "nt":
        # 未部署过的系统不按PID猜进程身份，交给当前终端或服务管理器。
        raise ValueError("非Windows请在启动终端或系统服务管理器停止中转")
    rows = windows_processes()
    owners = [row for row in rows if owned_process(row, config)]
    if not owners:
        if health(config):
            raise ValueError("端口有服务但进程身份不匹配，未停止其他程序")
        return
    family = {row["ProcessId"] for row in owners}
    for _ in range(len(rows)):
        descendants = {row["ProcessId"] for row in rows if row["ParentProcessId"] in family}
        if descendants <= family:
            break
        family |= descendants
    if any(row["ProcessId"] in family and row.get("Name", "").lower() in {"node.exe", "codebuddy.exe"} for row in rows):
        raise ValueError("官方CLI仍有任务在执行，未停止中转")
    token = json.loads((state / "pairing.json").read_text(encoding="utf-8"))["token"]
    headers = {"Authorization": "Bearer " + token}
    base = f"http://127.0.0.1:{config['port']}"
    # 服务端与接单/忙锁原子协调，封住检查结束后新任务进入的窗口。
    try:
        response = httpx.post(base + "/admin/drain", headers=headers, timeout=3, trust_env=False)
    except httpx.HTTPError as error:
        # 响应未知也先尝试恢复接单；服务端控制请求不排队，不会稍后再进入维护。
        try:
            httpx.post(base + "/admin/resume", headers=headers, timeout=3, trust_env=False)
        except httpx.HTTPError:
            pass
        raise ValueError("未确认维护状态，未停止进程，请检查连接后重试") from error
    if response.status_code != 200:
        raise ValueError("中转不能安全停止：仍在运行任务或版本不支持安全维护")
    try:
        ensure_idle(state)
        for row in owners:
            os.kill(int(row["ProcessId"]), signal.SIGTERM)
    except BaseException:
        httpx.post(base + "/admin/resume", headers=headers, timeout=3, trust_env=False)
        raise
    for _ in range(40):
        if not health(config):
            return
        time.sleep(.1)
    raise ValueError("中转尚未停止，请检查本机进程")


def start(config: dict) -> dict:
    current = health(config)
    if current:
        if current.get("build") != BUILD:
            raise ValueError("旧版中转仍在运行，请先stop再start")
        return current
    state = config["state"]
    state.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-u", str(ROOT / "scripts/bridge/desktop_bridge.py"), "--cli", config["cli"], "--cli-entry", config["cli_entry"], "--state", str(state), "--port", str(config["port"])]
    with (state / "server.log").open("ab") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
    (state / "process.json").write_text(json.dumps({"pid": process.pid, "port": config["port"]}), encoding="utf-8")
    for _ in range(60):
        current = health(config)
        if process.poll() is not None:
            raise ValueError("中转启动失败，请查看本机运行目录的server.log")
        if current and current.get("build") == BUILD:
            return current
        time.sleep(.2)
    process.terminate()
    raise ValueError("中转启动超时，请查看本机日志")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--config", type=Path, default=ROOT / ".local/bridge.json")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.action == "stop":
            stop(config)
            print("电脑中转已停止")
            return 0
        result = start(config) if args.action == "start" else health(config)
        if not result:
            print("电脑中转未运行或配对配置不匹配")
            return 1
        print(json.dumps({"status": result.get("status"), "port": config["port"], "build": result.get("build"), "model": result.get("model"), "image_model": result.get("image_model"), "reference_search": result.get("reference_search")}, ensure_ascii=False))
        return 0
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
