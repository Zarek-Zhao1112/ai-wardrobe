"""§19.4：让官方 CLI 用上账号凭据——必须在真 TTY 里跑。

实测（2026-11-03）：用 subprocess 直接起 CLI（管道 / CREATE_NO_WINDOW）时，
CLI 判定自己是非交互客户端，只打印「Authentication required. Please use /login」；
只有 stdin 是真终端时，它才会走账号凭据（init 事件里 apiKeySource=copilot.tencent.com）。
纯管道环境造不出真 TTY（ConPTY 的输出端读不出来），但 Git Bash 自带的 mintty 可以：
开一个隐藏的真终端（-w hide），把 stdout/stderr 重定向到文件，
避免终端折行把 stream-json 拆坏；再从文件里读回事件流。
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
SHIM_DIR = HERE / "shim"  # reg/wmic 的替身：CLI 取机器码会调它们，Win11 已无 wmic，且系统级工具在沙箱里被拦
MINTTY_CANDIDATES = (
    r"C:\Program Files\Git\usr\bin\mintty.exe",
    r"C:\Program Files (x86)\Git\usr\bin\mintty.exe",
)


def bsy(path) -> str:
    """Windows 路径转 bash 风格路径。"""
    text = str(path).replace("\\", "/")
    if len(text) > 2 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def mintty_path() -> str | None:
    for candidate in MINTTY_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return shutil.which("mintty")


def bash_path() -> str | None:
    # 计划任务等环境 PATH 里没有 bash，必须先按绝对路径找 Git for Windows 的 bash。
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("bash")


def tty_available() -> bool:
    return os.name == "nt" and bool(mintty_path()) and bool(bash_path())


# 账号来源白名单：旧版 CLI 报 copilot.tencent.com，新版桌面账号方案报 www.workbuddy.cn。
# 校验目的是「用的是个人账号额度，而不是项目的直付密钥」，两种域名都算通过。
ACCOUNT_KEY_SOURCES = ("copilot.tencent.com", "www.workbuddy.cn", "workbuddy.cn")


def account_source_ok(source) -> bool:
    text = str(source or "").strip().lower().rstrip("/")
    if not text:
        return False
    if text in ACCOUNT_KEY_SOURCES:
        return True
    return "copilot.tencent.com" in text or "workbuddy.cn" in text


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def run_cli(args, *, cwd, env, stdin_bytes: bytes, timeout: int, cli_cwd=None) -> SimpleNamespace:
    """跑官方 CLI，返回同 subprocess.run 一样的 returncode/stdout/stderr。

    cli_cwd: CLI 进程自己的工作目录。默认落在 CLI 安装包目录（不污染业务目录），
    但账号登录态按项目作用域存放，识别链路必须与登录时一致，故允许显式指定。
    """
    if not tty_available():
        return subprocess.run(args, cwd=cwd, env=env, input=stdin_bytes,
                              capture_output=True, timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    work = Path(cwd)
    msg = work / "tty_in.json"
    out = work / "tty_out.json"
    err = work / "tty_err.txt"
    code_file = work / "tty_exit.txt"
    inner = work / "tty_inner.sh"
    outer = work / "tty_launch.sh"
    log = work / "tty_mintty.log"
    for stale in (out, err, code_file, log):
        try:
            stale.unlink()
        except OSError:
            pass

    _write(msg, stdin_bytes.decode("utf-8"))

    node = str(args[0])
    entry = str(args[1]) if len(args) > 1 else None
    rest = list(args[2:] if entry else args[1:])
    package = Path(entry).resolve().parent.parent if entry else Path.cwd()
    run_dir = Path(cli_cwd).resolve() if cli_cwd else package
    # entry 尽量用相对 run_dir 的短路径；不在其下则退回绝对路径（bash 风格）。
    entry_arg = None
    if entry:
        try:
            entry_arg = str(Path(entry).resolve().relative_to(run_dir))
        except ValueError:
            entry_arg = bsy(Path(entry).resolve())

    command = "'%s' '%s' %s" % (bsy(node), entry_arg, " ".join(shlex.quote(a) for a in rest))
    _write(inner, "\n".join([
        "export PATH='%s':/c/Windows/system32:/c/Windows:/usr/bin" % bsy(SHIM_DIR),
        "cd '%s'" % bsy(run_dir),
        "%s < '%s' > '%s' 2> '%s'" % (command, bsy(msg), bsy(out), bsy(err)),
        "echo $? > '%s'" % bsy(code_file),
        "",
    ]))
    _write(outer, "'%s' --log '%s' -w hide -h never -e bash '%s'\n" % (
        bsy(mintty_path()), bsy(log), bsy(inner)))

    try:
        subprocess.run([bash_path(), str(outer)], cwd=cwd, env=env, timeout=timeout,
                       capture_output=True)
    except subprocess.TimeoutExpired as error:
        # 与 subprocess.run 的超时一致：把已落盘的部分输出带回，交给上层按超时分支处理。
        raise subprocess.TimeoutExpired(args, timeout,
                                        output=out.read_bytes() if out.exists() else b"",
                                        stderr=err.read_bytes() if err.exists() else b"") from error

    returncode = 0
    if code_file.exists():
        try:
            returncode = int(code_file.read_text(encoding="utf-8", errors="replace").strip())
        except ValueError:
            returncode = 0
    return SimpleNamespace(returncode=returncode,
                           stdout=out.read_bytes() if out.exists() else b"",
                           stderr=err.read_bytes() if err.exists() else b"")
