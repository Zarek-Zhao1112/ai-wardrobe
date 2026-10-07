"""集中配置：路径只在这里推导一次，其他模块从这里 import。

数据路径优先级：
    WARDROBE_DB          精确指定库文件（供测试用，最高优先级）
  > WARDROBE_DATA_DIR    指定数据目录（供高级用户把衣柜放到别处）
  > <项目根>/data         默认

关于 APP_HOST / APP_PORT：**刻意不在这里**。
它们只被 run.py 使用，而 run.py 对其做了容错校验（非法端口给出可读报错）。
搬进来的话就得用裸 int()，非法值会在 import 期抛 ValueError，
导致 `from app import db` 直接失败、且报错信息看不出是 APP_PORT 的问题。
它们是"启动参数"，留在入口更贴语义。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> int:
    """把项目根目录的 `.env` 读进 `os.environ`，返回载入的键数。

    **为什么自己写而不引 python-dotenv**：本项目刻意保持零依赖倾向
    （SQLite 用标准库、不引前端框架），而 .env 的语法简单到不值得多一个包。
    分发场景下用户 `cp .env.example .env` 填上 key 即可，不必设系统环境变量。

    规则（够用即可，不追求完整 dotenv 语义）：
    - 忽略空行与 `#` 注释；支持行首 `export `；值两侧的引号会被剥掉
    - **真实环境变量优先**：已在 `os.environ` 里的键不覆盖 ——
      这样测试/CI 能照常用环境变量压过 .env（`tests/conftest.py` 依赖这一点）
    - 读文件失败**不抛异常**（配置读取不该让服务起不来）
    """
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    loaded = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


DOTENV_LOADED = _load_dotenv()


def _env_flag(name: str, default: bool) -> bool:
    """读一个布尔型环境变量。

    认这些写成"假"：`0` / `false` / `no` / `off`（大小写不限）。
    空值或没设 → 用 `default`（**不抛异常** —— 见本文件末尾那段说明）。
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")

_db_env = os.environ.get("WARDROBE_DB")
_dir_env = os.environ.get("WARDROBE_DATA_DIR")

if _db_env:
    DB_PATH = Path(_db_env)
else:
    _base = Path(_dir_env) if _dir_env else (PROJECT_ROOT / "data")
    DB_PATH = _base / "wardrobe.db"

DATA_DIR = DB_PATH.parent
IMAGES_DIR = DATA_DIR / "images"

# 前端静态页（P2）。属于代码仓库，**不在 data/ 下**，所以不会被 .gitignore 排除。
WEB_DIR = PROJECT_ROOT / "web"

# ---------------------------------------------------------------------------
# 模型接入（P1）
# ---------------------------------------------------------------------------
# 供应商无关：任何 OpenAI 兼容接口都行（豆包 / DeepSeek / OpenAI / vLLM / 本地 Ollama）。
#
# ⚠️ key 不填也能启动 —— 这是刻意的。"先搭架构、key 稍后填"要求
# 没配 key 时服务照常起来、其余功能（衣橱 CRUD / 缺口分析 / 穿着记录）
# 不受影响，只有打标与搭配生成会明确告知"未配置"，而不是启动就崩。
# 所以这里全部只用 os.environ.get + 默认值，**不做任何会在 import 期抛异常的校验**。
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
# 视觉模型（拍照打标用）。**刻意不回落**到文本模型 —— 回落只在
# "同一供应商同时提供文本与视觉"时才有意义；对纯文本供应商（如 DeepSeek）
# 回落后 `VISION_READY` 会**误报 True**，打标会拿一个看不懂图的模型去认图，
# 报出来的是"模型炸了"而不是"没配视觉模型"，误导排查。
VISION_MODEL = os.environ.get("VISION_MODEL", "")

# 能力开关：业务层用它决定"能不能真的打标 / 生成"，
# 而不是等真去调用了才炸。也让 /health 能直接反映出来。
LLM_READY: bool = bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)
VISION_READY: bool = bool(LLM_BASE_URL and LLM_API_KEY and VISION_MODEL)

# 是否请求"别思考"（payload 里带 `thinking: {"type": "disabled"}`）。
#
# ⚠️ **这是 DeepSeek 专有字段，不是 OpenAI 标准的一部分。**
#
# 默认 `true`，因为实测用的 `deepseek-v4-pro` 是**推理模型**，reasoning 与正文
# **共用** token 额度：不抑制时它为一个 713 字符的 prompt 写了 7857 字思考，
# 单次 52~78 秒，而正文只有几十字；加上后 1.8 秒（≈30 倍），质量不变。
#
# 但**严格校验未知参数的端点**（严格 OpenAI / 部分 vLLM 部署）会对这个字段返回 400，
# 导致所有调用失败。那时把它设成 `false` 即可，不用改代码：
#     LLM_THINKING_DISABLED=false
LLM_THINKING_DISABLED: bool = _env_flag("LLM_THINKING_DISABLED", True)

# 是否启动微信通道的后台 worker（长轮询收消息）。
#
# 默认开。**测试环境必须关掉**（`tests/conftest.py` 已设）——
# 否则每个起 `TestClient` 的测试都会拉一个后台线程去戳微信，
# 既慢又会在日志里刷一堆无关的失败重试。
#
# 关掉后其他一切照常，只是不主动收消息（仍可用 `/channel/wechat/send` 手动发）。
CLAWBOT_WORKER_ENABLED: bool = _env_flag("CLAWBOT_WORKER_ENABLED", True)

# 知识库（Obsidian "第二大脑" vault）根目录。**P4 知识库技能用**。
#
# 默认**空字符串 = 功能自禁用** —— 这是刻意的：没配路径时，知识库相关意图
# 明确回复"未配置"，而不是拿着空路径去扫，更不会误写到某个盘符根目录。
#
# 与 `data/` 下的运行时数据性质不同：这是**外部目录**，读=整库扫描，
# 写=**只允许新建**且只能落在白名单领域目录下（见 `docs/knowledge-design.md`）。
#
# ⚠️ Windows 上配这个值请写进 `.env` 文件，**不要用命令行临时环境变量** ——
#    Git Bash 会把反斜杠吃掉（`D:\我的笔记\vault` → `D:我的笔记vault`）。
KNOWLEDGE_VAULT_DIR: str = os.environ.get("KNOWLEDGE_VAULT_DIR", "")
