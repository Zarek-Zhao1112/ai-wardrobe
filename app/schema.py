"""SQLite 表结构 + schema 版本化与自动迁移。

迁移机制沿用基金看板那套（lib/schema.js: _schemaVersion / SCHEMA_VERSION / MIGRATIONS）：
- 库里的版本存在 meta 表的 schema_version 键
- 启动时对比 SCHEMA_VERSION，按顺序跑缺的 MIGRATIONS
- 每个迁移是一个 (version, [sql...]) 元组，只在版本落后时执行
- 迁移只允许「向后兼容的增量」：加表、加列、加索引；不删列不改列类型

数据模型设计依据：
- 字段设计照搬 wardrowbe 的 ClothingItem（已源码实证），因为那套设计经过验证
- 我们额外补了 wardrowbe 确实没有的东西：
    * fit 真正入库（它在 wardrowbe 里是死字段：schema 有、无 DB 列、不在白名单）
    * slot（槽位）—— 缺口分析和组合数计算的地基
    * scene（场景）
    * wear_events —— outfit 级的穿着真相源，解决 wardrowbe 的 ItemHistory.outfit_id
      可空导致「某天穿了哪一整套」重建不出来的问题
    * preference_memory.context_key —— 条件化记忆分桶（季节 x 场合），
      防止「冬天不穿短袖」被误判成「不喜欢短袖」而衰减掉
    * body_profile —— 身材画像，wardrowbe 完全没有
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final[int] = 1

# 首次建库（version 0 -> 1）
_MIGRATION_001: Final[list[str]] = [
    # ---- 元信息 ----
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,

    # ---- 用户（单用户自用，但保留 user_id 约束：
    #      成本几乎为零，万一日后要做多人/转 SaaS 不用重构） ----
    """
    CREATE TABLE IF NOT EXISTS users (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,

    # ---- 衣橱单品 ----
    """
    CREATE TABLE IF NOT EXISTS clothing_items (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

        -- 图片（image_path 允许为空：先建单品、后补照片）
        image_path     TEXT,
        thumbnail_path TEXT,
        image_hash     TEXT,                       -- pHash，用于上传去重

        -- 受控分类（taxonomy.py）
        type    TEXT NOT NULL DEFAULT 'other',     -- 受控 type_key
        subtype TEXT,                              -- 自由补充
        slot    TEXT NOT NULL DEFAULT 'accessory', -- top/bottom/full/outer/shoes/accessory

        -- 受控属性（多值字段存 JSON 数组）
        colors        TEXT NOT NULL DEFAULT '[]',
        primary_color TEXT,
        pattern       TEXT,
        material      TEXT,
        fit           TEXT,                        -- wardrowbe 的死字段，这里真正入库
        style         TEXT NOT NULL DEFAULT '[]',
        season        TEXT NOT NULL DEFAULT '[]',
        formality     TEXT,
        scene         TEXT NOT NULL DEFAULT '[]',
        tags          TEXT NOT NULL DEFAULT '{}',  -- 扩展位

        -- 元数据
        name          TEXT,
        brand         TEXT,
        purchase_date TEXT,
        purchase_price REAL,
        favorite      INTEGER NOT NULL DEFAULT 0,
        notes         TEXT,

        -- AI 打标状态
        ai_confidence  REAL,
        ai_description TEXT,
        tagging_status TEXT NOT NULL DEFAULT 'pending',  -- pending|tagged
        tagged_by      TEXT,                             -- ai|manual|external
        tagged_at      TEXT,

        -- 使用追踪
        wear_count       INTEGER NOT NULL DEFAULT 0,
        last_worn_at     TEXT,
        last_suggested_at TEXT,
        suggestion_count INTEGER NOT NULL DEFAULT 0,
        acceptance_count INTEGER NOT NULL DEFAULT 0,

        -- 洗护状态（P0 只建列，接口后补）
        wears_since_wash INTEGER NOT NULL DEFAULT 0,
        last_washed_at   TEXT,
        wash_interval    INTEGER,

        -- 生命周期
        is_archived INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_user    ON clothing_items(user_id, is_archived)",
    "CREATE INDEX IF NOT EXISTS idx_items_slot    ON clothing_items(user_id, slot)",
    "CREATE INDEX IF NOT EXISTS idx_items_type    ON clothing_items(user_id, type)",
    "CREATE INDEX IF NOT EXISTS idx_items_tagging ON clothing_items(user_id, tagging_status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_hash ON clothing_items(user_id, image_hash) WHERE image_hash IS NOT NULL",

    # ---- AI 生成的搭配方案 ----
    """
    CREATE TABLE IF NOT EXISTS outfits (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        item_ids     TEXT NOT NULL,                 -- JSON 数组，只允许真实 item_id
        scene        TEXT,
        weather      TEXT,
        reason       TEXT,                          -- 推荐理由（可解释性）
        confidence   REAL,
        status       TEXT NOT NULL DEFAULT 'proposed', -- proposed|accepted|rejected|swapped
        generated_by TEXT NOT NULL DEFAULT 'llm',      -- llm|manual
        source       TEXT,                          -- wechat|web|api
        created_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_outfits_user ON outfits(user_id, created_at)",

    # ---- 穿着事件（我们新增：outfit 级的「某天穿了哪一整套」真相源） ----
    """
    CREATE TABLE IF NOT EXISTS wear_events (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        worn_at    TEXT NOT NULL,
        outfit_id  TEXT REFERENCES outfits(id) ON DELETE SET NULL,  -- 可空：用户自己搭的也算
        item_ids   TEXT NOT NULL,          -- JSON 数组
        occasion   TEXT,
        weather    TEXT,
        source     TEXT NOT NULL DEFAULT 'self_recorded',  -- ai_suggested|self_recorded
        notes      TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wear_user_date ON wear_events(user_id, worn_at)",

    # ---- 单品级穿着明细（抄 wardrowbe 的 ItemHistory，但补 wear_event_id 串联） ----
    """
    CREATE TABLE IF NOT EXISTS item_history (
        id            TEXT PRIMARY KEY,
        item_id       TEXT NOT NULL REFERENCES clothing_items(id) ON DELETE CASCADE,
        wear_event_id TEXT REFERENCES wear_events(id) ON DELETE SET NULL,
        outfit_id     TEXT REFERENCES outfits(id) ON DELETE SET NULL,
        worn_at       TEXT NOT NULL,
        occasion      TEXT,
        notes         TEXT,
        created_at    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_history_item ON item_history(item_id, worn_at)",
    "CREATE INDEX IF NOT EXISTS idx_history_date ON item_history(worn_at)",

    # ---- 反馈 ----
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id             TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        -- 三个指向里至少有一个：
        --   outfit_id     对 AI 生成的方案反馈
        --   wear_event_id 对某次"实际穿了什么"反馈
        --   item_ids      用户自己搭的一套（无方案也无穿着事件时的快照）
        outfit_id      TEXT REFERENCES outfits(id) ON DELETE CASCADE,
        wear_event_id  TEXT REFERENCES wear_events(id) ON DELETE CASCADE,
        item_ids       TEXT NOT NULL DEFAULT '[]',   -- JSON 数组快照
        feedback_type  TEXT NOT NULL,        -- like|dislike|swap
        rating         INTEGER,              -- 1..5 总体
        comfort_rating INTEGER,              -- 1..5 舒适度
        style_rating   INTEGER,              -- 1..5 风格
        comment        TEXT,
        created_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_feedback_outfit ON feedback(outfit_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_user   ON feedback(user_id, created_at)",

    # ---- 偏好记忆（结构化 + 条件化衰减） ----
    """
    CREATE TABLE IF NOT EXISTS preference_memory (
        id             TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        tag_type       TEXT NOT NULL,   -- color|style|occasion|weather|fit|material|pattern|slot|formality
        tag_value      TEXT NOT NULL,
        context_key    TEXT NOT NULL DEFAULT 'global',  -- 条件化分桶，如 'summer|daily'
        score          REAL NOT NULL DEFAULT 0,         -- -1..1，原始（未衰减）
        evidence_count INTEGER NOT NULL DEFAULT 0,
        pos_weight     REAL NOT NULL DEFAULT 0,         -- 正向证据累计强度
        neg_weight     REAL NOT NULL DEFAULT 0,         -- 负向证据累计强度
        confidence     REAL NOT NULL DEFAULT 0,         -- 0..1
        last_updated   TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pref_unique ON preference_memory(user_id, tag_type, tag_value, context_key)",

    # ---- 身材画像（wardrowbe 完全没有） ----
    """
    CREATE TABLE IF NOT EXISTS body_profile (
        user_id   TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        height_cm REAL,
        weight_kg REAL,
        chest_cm  REAL,
        waist_cm  REAL,
        hip_cm    REAL,
        body_type TEXT,          -- 如 slim/regular/athletic/curvy
        notes     TEXT,
        updated_at TEXT NOT NULL
    )
    """,

    # ---- 微信会话状态（ClawBot 的 context_token 必须原样回传，这里持久化） ----
    """
    CREATE TABLE IF NOT EXISTS conversation_state (
        id             TEXT PRIMARY KEY,
        channel        TEXT NOT NULL DEFAULT 'clawbot',
        peer_id        TEXT NOT NULL,
        context_token  TEXT,
        state          TEXT NOT NULL DEFAULT '{}',
        last_message_at TEXT,
        created_at     TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_peer ON conversation_state(channel, peer_id)",
]

# 版本表：追加新迁移时在末尾加 (n, [...])，并把 SCHEMA_VERSION 改成 n
MIGRATIONS: Final[list[tuple[int, list[str]]]] = [
    (1, _MIGRATION_001),
]
