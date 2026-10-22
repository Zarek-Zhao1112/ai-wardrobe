"""APK 内的 Python 服务入口。

Java 侧调用：`py.getModule("server").callAttr("start", filesDir, 8787)`

## 为什么要单独一个入口（而不是复用 run.py）

`run.py` 是给桌面/Termux 用的：它去读 `.env`、校验 `APP_HOST`/`APP_PORT`，
并且默认把数据写到**源码根下的 `data/`**。而 APK 里的 Python 源码是从
assets 解压出来的**只读**目录，写不进去。

`config.py` 提供了 `WARDROBE_DATA_DIR` 覆盖数据目录，所以这里只要在
**import `app.*` 之前**把它指向 app 私有目录即可 —— 业务代码一行都不用改。

⚠️ 顺序是硬约束：`config.py` 在 import 期就把路径算好并缓存，
   先 import 再设环境变量是无效的。
"""

from __future__ import annotations


def start(data_dir, port):
    """起 uvicorn，阻塞当前线程（Java 侧已把它放在单独线程里）。"""
    import os
    import pathlib
    import traceback

    data = pathlib.Path(str(data_dir))
    data.mkdir(parents=True, exist_ok=True)

    # ★ 必须在 import app.* 之前设好
    os.environ["WARDROBE_DATA_DIR"] = str(data)
    # 这台机器没绑微信 token：不关掉 worker 会每 5 秒刷一次重试日志
    os.environ.setdefault("CLAWBOT_WORKER_ENABLED", "false")

    print(f"[server] data_dir={data}", flush=True)

    try:
        import uvicorn

        from app.main import app

        cfg = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=int(port),
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(cfg)
        server.run()
    except BaseException:
        # 把 Python 的栈打出来 —— 否则 Java 侧只看到一句 "Python 启动失败"，
        # 在真机上完全没法定位（本项目的日志都进 logcat，用 adb logcat -s python 看）
        traceback.print_exc()
        raise
