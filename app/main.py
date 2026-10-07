"""FastAPI 应用装配。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__, config, db
from .channels import ClawBotWorker, get_channel
from .routes import channel as routes_channel
from .routes import items as routes_items
from .routes import meta as routes_meta
from .routes import outfits as routes_outfits
from .routes import preferences as routes_preferences
from .routes import tagging as routes_tagging
from .routes import wardrobe as routes_wardrobe
from .services import conversation

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建目录 + 迁移 + 保证默认用户存在。幂等，重复启动不会出问题。
    info = db.init(verbose=True)
    app.state.db_info = info
    logger.info("服务就绪，数据库：%s", info["db_path"])

    # 微信通道的后台 worker。
    #
    # ⚠️ **它不跑在 HTTP 请求线程里** —— iLink 的长轮询一次 hold 35 秒，
    # 放在请求里会把整个 Web 堵死。所以起一个后台线程。
    #
    # **未绑定时它不会白转**：`fetch_updates` 抛 `ChannelError`，
    # worker 退避几秒再试 —— 于是用户扫码绑定后 5 秒内自动开始收消息，
    # **不需要重启服务**。
    #
    # 测试环境用 `CLAWBOT_WORKER_ENABLED=false` 关掉（`conftest.py` 已设），
    # 否则每个起 `TestClient` 的测试都会拉一个线程去戳微信。
    worker: ClawBotWorker | None = None
    if config.CLAWBOT_WORKER_ENABLED:
        worker = ClawBotWorker(get_channel(), conversation.handle)
        worker.start()
        logger.info("微信通道 worker 已启动")
    else:
        logger.info("微信通道 worker 已禁用（CLAWBOT_WORKER_ENABLED=false）")

    try:
        yield
    finally:
        if worker is not None:
            worker.stop()
            logger.info("微信通道 worker 已停止")


app = FastAPI(
    title="AI 穿搭助手",
    description=(
        "个人 AI Stylist —— 长期积累衣橱与反馈，越用越懂你。\n\n"
        "**未配置模型 key 时**：视觉打标不可用（返回 503），搭配理由为空；"
        "但推荐本身仍会退回**本地排序** —— 它本来就基于你的偏好记忆，不是残废。\n\n"
        "可用：衣橱 CRUD、批量导入、图片上传、缺口分析、组合数、穿着记录、"
        "反馈闭环与偏好记忆查看。"
    ),
    version=__version__,
    lifespan=lifespan,
)

# 只挂图片目录。用 check_dir=False 是因为：
#   目录是 db.init() 在 lifespan 里才建的，
#   而 StaticFiles 默认 check_dir=True，在 import 期挂会直接抛错。
app.mount(
    "/images",
    StaticFiles(directory=str(db.IMAGES_DIR), check_dir=False),
    name="images",
)

app.include_router(routes_meta.router)
app.include_router(routes_items.router)
app.include_router(routes_wardrobe.router)
app.include_router(routes_outfits.router)
app.include_router(routes_preferences.router)
app.include_router(routes_tagging.router)
app.include_router(routes_channel.router)

# ⚠️ 前端静态页必须挂在**所有 router 之后**：Starlette 按注册顺序匹配路径，
# 挂在 "/" 的 StaticFiles 会吃掉它之后注册的一切。
# `html=True` 让 `/` 自动返回 index.html。
# `check_dir=False` 的理由同 /images —— 缺目录时 `/` 返回 404，
# 而不是让 import 期就崩（那会连 /docs 都打不开，更难排查）。
# 原 `GET /`（返回 JSON 版本信息）已被首页取代，版本信息移到 /health。
app.mount(
    "/",
    StaticFiles(directory=str(config.WEB_DIR), html=True, check_dir=False),
    name="web",
)
