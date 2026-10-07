"""启动脚本。

    python run.py                           # 默认 127.0.0.1:8787
    APP_PORT=9000 python run.py             # 换端口
    APP_OPEN_BROWSER=1 python run.py        # 起来后自动打开浏览器（`start.bat` 用的）
    WARDROBE_DB=/tmp/x.db python run.py     # 换库文件（测试用）
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# 保证从任意工作目录运行都能 import 到 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_HOST = "127.0.0.1"
# 刻意避开 3000（本机基金看板已占用）和 8000（开发时常见冲突）
DEFAULT_PORT = 8787

#: 端口已被占用时的退出码。`start.bat` 靠它区分"服务真的跑了"和
#: "什么都没起" —— 后者那个窗口没必要留着，自动关掉更省事。
EXIT_ALREADY_RUNNING = 3


def _configure_logging() -> None:
    """日志同时写 stderr **和文件**。

    ⚠️ **为什么必须有文件**：微信通道的 worker 跑在**后台线程**里，它出问题时
    （拉取失败 / 消息解析失败 / 发送失败）只用 `log.exception` 记一笔。
    服务如果是后台起的，stderr 没人看得见 —— 于是"用户在微信里发了消息但没反应"
    就只能靠猜。真机联调前必须有。

    日志落 `data/app.log`（`data/` 已在 `.gitignore` 里）。
    """
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        from app import config

        path = Path(config.DATA_DIR) / "app.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    except OSError:
        pass          # 落盘失败不该让服务起不来
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def _port_in_use(host: str, port: int) -> bool:
    """这个地址:端口上有没有人在听。"""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _open_browser_when_ready(url: str, host: str, port: int, *, timeout: float = 30.0) -> None:
    """后台线程：等端口真的通了再开浏览器。

    ⚠️ **不能立刻开** —— uvicorn 从绑定端口到能响应请求之间要几百毫秒，
    先开的话用户看到的是一个"无法访问"的错误页，还得手动刷新。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_in_use(host, port):
            webbrowser.open(url)
            return
        time.sleep(0.3)


def main() -> None:
    _configure_logging()
    log = logging.getLogger("run")

    host = os.environ.get("APP_HOST", DEFAULT_HOST)
    try:
        port = int(os.environ.get("APP_PORT", str(DEFAULT_PORT)))
    except ValueError:
        log.error("APP_PORT 必须是数字，收到：%r", os.environ.get("APP_PORT"))
        raise SystemExit(2) from None

    # 浏览器要访问的地址。绑 0.0.0.0 时不能拿它当 URL，得换成 127.0.0.1。
    browse_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    page = f"http://{browse_host}:{port}/"
    open_browser = os.environ.get("APP_OPEN_BROWSER", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # ⚠️ 端口被占用时**必须停下**，不能换个端口接着起。
    # 微信通道的 worker：一个机器人只能有**一个**进程在轮询 —— 两个进程同时
    # 收消息会抢同一个游标，**静默地**重复回复或漏消息。端口被占 = 已经有一个在跑，
    # 这里就只负责把页面打开，不再起第二个。
    if _port_in_use(browse_host, port):
        log.warning("端口 %s 上已经有服务在跑了 —— 本次不重复启动。", port)
        log.info("要用它的话，直接开这个页面：%s", page)
        log.info("想重启的话：先把原来那个黑窗口关掉，再运行一次。")
        if open_browser:
            webbrowser.open(page)
        raise SystemExit(EXIT_ALREADY_RUNNING)

    # pre-flight：先把库建好、迁移跑完，有问题在这里就暴露，
    # 而不是等 uvicorn 起来之后才在请求里炸
    from app import db

    info = db.init(verbose=True)

    import uvicorn

    log.info("服务地址：%s", page)
    log.info("接口文档：%s/docs", page.rstrip("/"))
    log.info("数据库： %s", info["db_path"])

    if open_browser:
        # daemon 线程：uvicorn.run 是阻塞的，开浏览器只能在旁边等
        threading.Thread(
            target=_open_browser_when_ready, args=(page, browse_host, port), daemon=True
        ).start()

    # reload=False：--reload 依赖 watchfiles，依赖清单里没有装。
    # 需要热重载的话单独 pip install watchfiles 再改这里。
    uvicorn.run("app.main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
