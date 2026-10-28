"""§23：官方个人CLI图生图任务；幂等查询，不自动重复扣积分。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import threading
import time

from fastapi import HTTPException

MODEL = "deepseek-v4.1-flash"
IMAGE_MODEL = "hunyuan-image-alpha-edit"
MAX_IMAGE = 10 * 1024 * 1024
TTL = 24 * 60 * 60


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg", ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP": return "image/webp", ".webp"
    return None


def make_prompt(kind):
    from app import taxonomy
    label = taxonomy.label_of(kind) if kind in taxonomy.CLOTHING_TYPES else "目标衣物"
    return (f"将参考图中的{label}做成电商抠图白底平铺图。先彻底移除原来的背景，替换为纯白（#FFFFFF，RGB 255,255,255）。"
            "这不是摄影棚或白墙场景：整片留白必须均匀纯白，没有灰底、蓝底、渐变、台面、投影或环境光色。"
            "去除模特的人脸、身体、手脚、人台、衣架、其他不属于目标商品的衣物、"
            "背景、购物界面、商品下架/退款/价格等文字和独立营销附图。只保留目标商品，保持其可见颜色、轮廓、版型、图案、面料质感及衣服本身的标志。"
            "完整展示商品，双袖或裤腿自然展开，居中放在纯白色背景（RGB 255,255,255，#FFFFFF），四周均匀留白。"
            "背景不要灰色、米色、蓝色、渐变、台面或场景，不生成投影，不把衣物本身漂白。"
            "如果参考图已经是衣物平铺图，保持衣物的形态、摆放、大小、细节和原色，只整理背景，不重新设计商品。"
            "被遮挡部分只做保守自然的延续，不添加可有可无的装饰，不夸张锐化，不改变明显款式。输出一张方形图片。")


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def hook_command(executable, guard, directory):
    return shlex.join([str(executable).replace("\\", "/"), "-e", "try{process.exit(require(process.argv[1]).guard(process.argv[2]))}catch(e){process.exit(2)}", str(guard).replace("\\", "/"), str(directory).replace("\\", "/")])


def run_image(executable, entry, directory, source, kind, environment):
    output = directory / "output"
    output.mkdir(exist_ok=True)
    request = {"image1": str(source), "output_dir": str(output), "size": "1024x1024", "prompt": make_prompt(kind), "revise": False}
    _write(directory / "guard.json", request)
    guard = Path(__file__).with_name("flatlay_guard.cjs")
    # 与CLI共用Node；模块缺失、语法/读取错误均归一为阻止码2，不让普通exit1放行。
    preflight = subprocess.run([str(executable), "-e", "try{process.exit(require(process.argv[1]).selfCheck())}catch(e){process.exit(2)}", str(guard)], capture_output=True, timeout=8,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if preflight.returncode: raise ValueError("图像工具校验程序不可用，尚未开始生成")
    # 官方HookExecutor使用bash -lc；必须按shell转义，Windows argv转义会裸露JS括号。
    command = hook_command(executable, guard, directory)
    settings = {"hooks": {"PreToolUse": [{"matcher": "ImageGen", "hooks": [{"type": "command", "command": command, "timeout": 8}]}]}}
    args = [str(executable), str(entry), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", MODEL, "--image-to-image-model", IMAGE_MODEL, "--tools", "NoDefer(ImageGen)", "--allowedTools", "ImageGen",
            "--permission-mode", "dontAsk", "--max-turns", "2", "--no-session-persistence", "--setting-sources", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--settings", json.dumps(settings, ensure_ascii=False),
            "--system-prompt", "只按用户参数调用一次ImageGen图生图，不用其他工具。参数不得更改，失败不重试、不换模型，完成后只回答完成。"]
    message = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "严格使用下列参数：" + json.dumps(request, ensure_ascii=False)}]}}
    result = subprocess.run(args, cwd=directory, env=environment(), input=(json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"),
                            capture_output=True, timeout=210, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    init, tool_paths, credit = {}, [], None
    diagnostics = ""
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        if event.get("type") == "system" and event.get("subtype") == "init": init = event
        if event.get("type") == "result": diagnostics = str(event.get("result", "")) + str(event.get("errors", ""))
        def collect(value):
            nonlocal credit
            if isinstance(value, dict):
                for key, field in value.items():
                    if key == "credit" and isinstance(field, (int, float)) and not isinstance(field, bool) and 0 <= field < 100000: credit = field
                    elif key == "localPath" and isinstance(field, str): tool_paths.append(field)
                    elif isinstance(field, (dict, list)): collect(field)
                    elif key == "text" and isinstance(field, str) and field.startswith('{"type":"image_gen_tool_result"'):
                        try: collect(json.loads(field))
                        except ValueError: pass
            elif isinstance(value, list):
                for field in value: collect(field)
        collect(event)
    if init.get("apiKeySource") != "copilot.tencent.com" or init.get("model") != MODEL:
        raise ValueError("个人CLI登录来源未通过校验，请重新登录。")
    candidates = []
    for name in dict.fromkeys(tool_paths):
        path = Path(name).resolve()
        if path.is_relative_to(output.resolve()) and path.is_file() and not path.is_symlink(): candidates.append(path)
    # 一些CLI版本只把结果路径嵌在tool文本里；仍只接受固定输出目录的一张图片。
    if not candidates: candidates = [p for p in output.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
    if len(candidates) != 1 or not (directory / "tool-claimed").is_file():
        lower = diagnostics.lower()
        if any(x in lower for x in ("authentication", "登录", "login")): raise ValueError("电脑个人CLI需要重新登录。")
        if any(x in lower for x in ("insufficient", "balance", "credit", "积分不足")): raise ValueError("WorkBuddy积分或额度不足，源图已保留。")
        raise ValueError("本次平铺图未生成成功，源图已保留，请稍后重试。")
    path = candidates[0]
    if not 0 < path.stat().st_size <= MAX_IMAGE: raise ValueError("生成图片过大或为空，源图已保留。")
    data = path.read_bytes()
    if not image_type(data): raise ValueError("生成结果不是支持的图片，源图已保留。")
    return data, credit


class FlatlayJobs:
    def __init__(self, directory, executable, entry, busy, environment, runner=run_image):
        self.directory, self.executable, self.entry = Path(directory), executable, entry
        self.directory.mkdir(parents=True, exist_ok=True)
        self.busy, self.environment, self.runner = busy, environment, runner
        self.lock, self.running = threading.RLock(), set()

    def _directory(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token): raise HTTPException(400, "平铺图任务标识无效")
        path = self.directory / token
        if path.is_symlink(): raise HTTPException(400, "平铺图目录无效")
        return path

    def _read(self, token):
        path = self._directory(token) / "state.json"
        try: state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError): raise HTTPException(404, "未找到这次生成任务，请勿自动重复生成")
        if state.get("state") == "running" and token not in self.running:
            state.update(state="failed", error="电脑中转已重启，本次结果无法确认。请先核对用量，再决定是否重新生成。")
            _write(path, state)
            for source in path.parent.glob("input.*"): source.unlink(missing_ok=True)
        return state

    def start(self, payload):
        if not isinstance(payload, dict): raise HTTPException(400, "任务格式不正确")
        token, kind, encoded = payload.get("id"), payload.get("kind", "other"), payload.get("image")
        folder = self._directory(token)
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z_]{1,40}", kind): raise HTTPException(400, "衣物品类无效")
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE + 2) // 3 * 4 + 50: raise HTTPException(413, "图片超过10MB")
        match = re.fullmatch(r"data:image/(?:jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", encoded)
        if not match: raise HTTPException(400, "需要内嵌的JPG、PNG或WebP图片")
        try: data = base64.b64decode(match[1], validate=True)
        except ValueError: raise HTTPException(400, "图片编码无效")
        sniffed = image_type(data)
        if not sniffed or not 0 < len(data) <= MAX_IMAGE: raise HTTPException(400, "源图不支持或超过10MB")
        source_hash = hashlib.sha256(data).hexdigest()
        fingerprint = hashlib.sha256((source_hash + ":" + kind + ":v1").encode()).hexdigest()
        with self.lock:
            self.cleanup()
            if (folder / "state.json").exists():
                state = self._read(token)
                if state.get("fingerprint") != fingerprint: raise HTTPException(409, "同一任务不能更换源图")
                return self.status(token)
            if not self.busy.acquire(blocking=False): raise HTTPException(429, "电脑正在处理其他任务，请稍后再试")
            try:
                folder.mkdir(exist_ok=False)
                source = folder / ("input" + sniffed[1])
                source.write_bytes(data)
                state = {"id": token, "state": "running", "source_hash": source_hash, "fingerprint": fingerprint, "created": time.time(), "image_model": IMAGE_MODEL}
                _write(folder / "state.json", state)
                self.running.add(token)
                thread = threading.Thread(target=self._run, args=(token, source, kind), daemon=True)
                thread.start()
            except Exception:
                self.running.discard(token); self.busy.release()
                raise
            return self.status(token)

    def _run(self, token, source, kind):
        folder = self._directory(token)
        try:
            data, credit = self.runner(self.executable, self.entry, folder, source, kind, self.environment)
            sniffed = image_type(data)
            if not sniffed or not 0 < len(data) <= MAX_IMAGE: raise ValueError("生成图片格式无效")
            result = folder / ("result" + sniffed[1])
            result.write_bytes(data)
            with self.lock:
                state = self._read(token)
                state.update(state="completed", result=result.name, mime=sniffed[0], credit=credit)
                _write(folder / "state.json", state)
        except Exception:
            with self.lock:
                state = self._read(token)
                state.update(state="failed", error="本次生成未能确认成功，源图和填写内容已保留。请先查看WorkBuddy用量，再决定是否重做。")
                _write(folder / "state.json", state)
        finally:
            try:
                try: source.unlink(missing_ok=True)
                except OSError: pass
                shutil.rmtree(folder / "output", ignore_errors=True)
            finally:
                with self.lock: self.running.discard(token)
                self.busy.release()

    def status(self, token):
        with self.lock:
            self.cleanup()
            state = self._read(token)
            result = {key: state[key] for key in ("id", "state", "source_hash", "image_model", "credit", "error") if key in state}
            result["model"] = MODEL
            if state.get("state") == "completed":
                name = state.get("result", "")
                if name not in ("result.png", "result.jpg", "result.webp"): raise HTTPException(503, "生成结果路径无效")
                path = self._directory(token) / name
                if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_IMAGE: raise HTTPException(404, "生成结果已不可用")
                result["image"] = "data:" + state["mime"] + ";base64," + base64.b64encode(path.read_bytes()).decode()
            return result

    def cleanup(self):
        for path in self.directory.iterdir():
            if not path.is_dir() or path.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}", path.name) or path.name in self.running: continue
            try:
                if time.time() - (path / "state.json").stat().st_mtime > TTL:
                    state = json.loads((path / "state.json").read_text(encoding="utf-8"))
                    if state.get("state") == "expired": continue
                    # 保留任务墓碑与指纹，迟到同token POST不能重新扣积分。
                    for child in path.iterdir():
                        if child.name in ("state.json", "state.tmp"): continue
                        if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
                        else: child.unlink(missing_ok=True)
                    state.update(state="expired", error="生成图片缓存已到期，重新生成会另计积分。")
                    state.pop("result", None)
                    _write(path / "state.json", state)
            except (OSError, ValueError): pass

