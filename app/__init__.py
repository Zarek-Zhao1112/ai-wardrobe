"""AI 穿搭助手 —— 本地服务（单用户自用）。

数据模型参考同类开源项目已验证的字段设计，并补上它们缺的部分：
真正入库的 `fit`、槽位化 `slot`、outfit 级穿着真相源 `wear_events`、
条件化记忆 `preference_memory.context_key`、身材画像 `body_profile`。

产品设计见 `docs/设计文档_v0.2.md`。
"""

__version__ = "0.1.0"
