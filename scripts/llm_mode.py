"""模型通道切换：OpenAI / WorkBuddy 额度 / DeepSeek。

为什么需要它：三条路的开关**不在同一处**，手改容易漏。

    通道          什么时候生效                        代价
    ------------  ----------------------------------  --------------------
    openai        没有 data/desktop-bridge.json 时    按量付费，2~5s
    workbuddy     有 data/desktop-bridge.json 时      走账号额度，8~15s
    deepseek      同 openai，但 .env 换回官方地址      余额 -0.00，暂不可用

`app/services/llm/client.py::get_client()` 的判断顺序是「先看中转配置，
没有才用 .env」，所以 workbuddy 与另外两个是**互斥**的：加文件即切过去，
删文件即切回来。本脚本把这套动作收成一条命令，并且切换后自动重启服务
（.env 是 import 期读的，不重启不生效）。

用法：
    python scripts/llm_mode.py            # 交互菜单（双击 switch-model.bat 等价）
    python scripts/llm_mode.py status     # 只看现在走哪条
    python scripts/llm_mode.py openai     # 切到 OpenAI
    python scripts/llm_mode.py workbuddy  # 切到 WorkBuddy 额度
    python scripts/llm_mode.py deepseek   # 切回 DeepSeek 直付

    --no-restart   只改配置，不重启服务
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
PROFILE_PATH = ROOT / ".local" / "llm-profiles.json"
BRIDGE_PATH = ROOT / "data" / "desktop-bridge.json"
PAIRING_PATH = ROOT / ".local" / "runtime" / "desktop-bridge" / "pairing.json"
BASE_URL = "http://127.0.0.1:8787"
BRIDGE_PORT = 8891

START = "# >>> LLM_MODE"
END = "# <<< LLM_MODE"

MODES = {
    "openai": {"label": "OpenAI（快，按量付费）", "bridge": False, "env": True},
    "workbuddy": {"label": "WorkBuddy 额度（慢，不花钱）", "bridge": True, "env": False},
    "deepseek": {"label": "DeepSeek 直付（需充值，当前余额不足）", "bridge": False, "env": True},
}


# ----------------------------------------------------------------------
# .env 读写
# ----------------------------------------------------------------------
def current_mode() -> str:
    """从 .env 的标记行读当前模式；没有标记说明是手写的旧格式。"""
    if not ENV_PATH.is_file():
        return "unknown"
    match = re.search(rf"^{re.escape(START)}:\s*(\w+)", ENV_PATH.read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else "unknown"


def apply_env(mode: str) -> None:
    """把 .env 里标记段整段换成目标模式的值，其余内容一行不动。"""
    profiles = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    if mode not in profiles:
        raise SystemExit(f"配置里没有 {mode}，先跑一次初始化")
    lines = [f"{START}: {mode}"]
    lines += [f"{key}={value}" for key, value in profiles[mode].items()]
    lines.append(END)
    block = "\n".join(lines)

    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.is_file() else ""
    pattern = re.compile(rf"^{re.escape(START)}.*?^{re.escape(END)}", re.S | re.M)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text, count=1)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    ENV_PATH.write_text(text, encoding="utf-8")


def apply_bridge(enabled: bool) -> str:
    """加/删中转配置。返回一句人话说明发生了什么。"""
    if not enabled:
        if BRIDGE_PATH.exists():
            BRIDGE_PATH.unlink()
            return "已删除中转配置（改走 .env）"
        return "中转配置本来就不存在"
    if BRIDGE_PATH.exists():
        return "中转配置已存在，无需改动"
    if not PAIRING_PATH.is_file():
        raise SystemExit(
            "找不到中转配对码（.local/runtime/desktop-bridge/pairing.json）。\n"
            "需要先在电脑上启动过一次中转，手机才能连；这一步请先跑：\n"
            "    python scripts/bridge/manage.py start"
        )
    token = json.loads(PAIRING_PATH.read_text(encoding="utf-8"))["token"]
    BRIDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BRIDGE_PATH.write_text(
        json.dumps({"url": f"http://127.0.0.1:{BRIDGE_PORT}", "token": token}, ensure_ascii=False),
        encoding="utf-8",
    )
    return "已写入中转配置（优先走 WorkBuddy 额度）"


# ----------------------------------------------------------------------
# 运行状态
# ----------------------------------------------------------------------
def health() -> dict | None:
    try:
        response = httpx.get(f"{BASE_URL}/health", timeout=3, trust_env=False)
        return response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def env_values() -> dict[str, str]:
    """把 .env 读成 dict（只认 KEY=VALUE，忽略注释与空行）。"""
    values: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return values
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def describe(mode: str, info: dict | None) -> list[str]:
    values = env_values()
    lines = [f".env 里写的模式：{mode}（{MODES.get(mode, {}).get('label', '未知')}）"]
    lines.append(f"中转配置文件：{'存在' if BRIDGE_PATH.exists() else '不存在'}")
    if info is None:
        lines.append("服务状态：8787 没在跑（或没响应）")
        return lines
    bridge_on = bool(info.get("desktop_bridge", {}).get("configured"))
    running = "WorkBuddy 额度" if bridge_on else "OpenAI / DeepSeek 直连"
    lines.append(f"服务实际走：{running}")
    lines.append(f"服务认的模型：{info.get('llm_model')} / 视觉 {info.get('vision_model')}")

    expect_bridge = MODES.get(mode, {}).get("bridge")
    if expect_bridge is not None and expect_bridge != bridge_on:
        lines.append("⚠️ 配置与运行状态不一致 —— 需要重启服务才生效")
    elif not bridge_on:
        want = values.get("LLM_MODEL")
        if want and want != info.get("llm_model"):
            lines.append(f"⚠️ .env 写的是 {want}，服务还停在 {info.get('llm_model')} —— 需要重启")
    return lines


def wait_ready(seconds: float = 40.0) -> dict | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        info = health()
        if info is not None:
            return info
        time.sleep(0.6)
    return None


def _decode(raw: bytes) -> str:
    """Windows 控制台命令的输出可能是 UTF-8，也可能是 GBK（netstat 就是）。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="ignore")


def restart() -> str:
    """关掉占用 8787 的进程，再用 start.bat 开一个新的窗口。"""
    try:
        raw = subprocess.run(
            ["netstat", "-ano"], capture_output=True, timeout=15
        ).stdout or b""
    except (OSError, subprocess.SubprocessError):
        return "没查到端口占用，请手动重启服务"
    out = _decode(raw)
    pid = None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(":8787") and parts[3] == "LISTENING":
            pid = parts[4]
            break
    if pid:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=15)
        time.sleep(1.5)
    subprocess.Popen(
        ["cmd", "/c", "start", "", "start.bat"],
        cwd=str(ROOT),
        env={**os.environ, "APP_OPEN_BROWSER": "0"},
        shell=False,
    )
    return "已重启服务（新窗口）"


# ----------------------------------------------------------------------
# 命令
# ----------------------------------------------------------------------
def switch(mode: str, do_restart: bool) -> int:
    if mode not in MODES:
        raise SystemExit(f"不认识的模式：{mode}")
    model_before = env_values().get("LLM_MODEL")
    if MODES[mode]["env"]:
        apply_env(mode)
    message = apply_bridge(MODES[mode]["bridge"])
    print(f"[1/3] 配置已切换 → {MODES[mode]['label']}")
    print(f"      {message}")

    # 只有 .env 真的变了才需要重启：`get_client()` 每次调用都会重读中转配置，
    # 所以 OpenAI ↔ WorkBuddy 之间是**即时生效**的，白重启一次只会中断服务。
    if model_before == env_values().get("LLM_MODEL"):
        print("[2/3] 不用重启 —— 这类切换在下次请求时自动生效")
        print("[3/3] 当前状态：")
        for line in describe(current_mode(), health()):
            print("      " + line)
        return 0

    if not do_restart:
        print("[2/3] 跳过重启 —— 改动还没生效")
        return 0
    print(f"[2/3] {restart()}")
    info = wait_ready()
    print("[3/3] 重启后状态：")
    for line in describe(current_mode(), info):
        print("      " + line)
    return 0 if info else 1


def menu() -> int:
    mode = current_mode()
    info = health()
    print("=" * 52)
    print("  模型通道切换")
    print("=" * 52)
    for line in describe(mode, info):
        print("  " + line)
    print("-" * 52)
    print("  1 = OpenAI          快，2~5 秒，按量付费")
    print("  2 = WorkBuddy 额度  慢，10 秒左右，不花钱")
    print("  3 = DeepSeek 直付   余额 -0.00，暂时用不了")
    print("  回车 = 什么都不做")
    print("-" * 52)
    choice = input("  请按数字后回车：").strip()
    mapping = {"1": "openai", "2": "workbuddy", "3": "deepseek"}
    if choice not in mapping:
        print("  没有改动。")
        return 0
    print()
    return switch(mapping[choice], do_restart=True)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    do_restart = "--no-restart" not in argv
    if not args:
        return menu()
    if args[0] == "status":
        for line in describe(current_mode(), health()):
            print(line)
        return 0
    return switch(args[0], do_restart)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
