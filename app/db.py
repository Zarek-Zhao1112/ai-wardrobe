"""SQLite 连接管理与自动迁移。

用法：
    from app import db
    db.init()                       # 建库 + 迁移 + 保证默认用户存在（启动时调一次）

    with db.conn() as c:            # 短连接，自动 commit/rollback
        c.execute(...)

设计说明：单用户自用场景，用「每次请求开一个短连接」最简单也最不容易出错。
SQLite 在 WAL 模式下并发读没有问题；写入用短事务即可。
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from .config import DATA_DIR, DB_PATH, IMAGES_DIR, PROJECT_ROOT  # noqa: F401
from .schema import MIGRATIONS, SCHEMA_VERSION

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
# 推导逻辑已移到 app/config.py。这里**重新导出**同名常量（PROJECT_ROOT /
# DB_PATH / DATA_DIR / IMAGES_DIR），因为它们已被外部使用：
#     app/main.py:46        db.IMAGES_DIR
#     app/routes/meta.py:24 db.DB_PATH
# 保住名字，外部引用就不用改。

DEFAULT_USER_NAME = "我"


def now_iso() -> str:
    """统一的本地时间戳（秒精度，带时区偏移）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}_{raw}" if prefix else raw


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15.0, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


@contextmanager
def conn(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """短连接上下文。正常退出 commit，异常 rollback。"""
    c = _connect()
    try:
        c.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield c
        c.execute("COMMIT")
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------
def _current_version(c: sqlite3.Connection) -> int:
    c.execute(
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(verbose: bool = True) -> int:
    """把库升到 SCHEMA_VERSION，返回最终版本号。"""
    c = _connect()
    try:
        current = _current_version(c)
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"库的 schema 版本 {current} 高于代码支持的 {SCHEMA_VERSION}，"
                "请更新代码而不是降级数据库。"
            )
        if current == SCHEMA_VERSION:
            if verbose:
                print(f"[db] schema 已是 v{current}，无需迁移")
            return current

        for version, statements in sorted(MIGRATIONS, key=lambda x: x[0]):
            if version <= current:
                continue
            if verbose:
                print(f"[db] 应用迁移 v{version} ...")
            c.execute("BEGIN")
            try:
                for sql in statements:
                    c.execute(sql)
                c.execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(version),),
                )
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            current = version

        if verbose:
            print(f"[db] 迁移完成，当前 schema v{current}")
        return current
    finally:
        c.close()


# ---------------------------------------------------------------------------
# 默认用户
# ---------------------------------------------------------------------------
def ensure_default_user() -> str:
    """保证存在默认用户，返回 user_id。单用户场景下就是"我"。"""
    with conn() as c:
        row = c.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
        if row:
            return row["id"]
        uid = new_id("u")
        c.execute(
            "INSERT INTO users(id,name,created_at) VALUES(?,?,?)",
            (uid, DEFAULT_USER_NAME, now_iso()),
        )
        return uid


def get_default_user_id() -> str:
    with conn() as c:
        row = c.execute("SELECT id FROM users ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("库中还没有用户，请先调用 db.init()")
        return row["id"]


def init(verbose: bool = True) -> dict:
    """启动时调用一次：建目录、迁移、保证默认用户存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    version = migrate(verbose=verbose)
    uid = ensure_default_user()
    if verbose:
        print(f"[db] 就绪：{DB_PATH}  (schema v{version}, user={uid})")
    return {"db_path": str(DB_PATH), "schema_version": version, "user_id": uid}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    # 布尔字段还原（SQLite 没有 bool）
    for k in ("is_archived", "favorite"):
        if k in d:
            d[k] = bool(d[k])
    return d


def get_meta(key: str, default: str | None = None) -> str | None:
    with conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
