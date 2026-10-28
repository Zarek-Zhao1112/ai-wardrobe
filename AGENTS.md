# AGENTS.md

给编码 agent 的项目说明书。只写**不被告知就不会知道**的东西；通用最佳实践不写。

> 多个 agent 一起干活（谁写入、谁审查、缺陷怎么定档）→ 先读 `docs/AGENT-COLLAB.md`。

---

## 这是什么

个人 AI 穿搭助手（单用户自用，本地跑）。Python 3.13 + FastAPI + SQLite（标准库 `sqlite3`）。
**没有 Docker、没有 Postgres、没有 Redis** —— 这是刻意的，为了"一条命令跑起来"。

## Commands

在项目根目录（本文件所在目录）下执行：

```bash
# 起服务（默认 127.0.0.1:8787）
.venv/Scripts/python.exe run.py

# 端到端烟雾测试（83 项）
.venv/Scripts/python.exe tests/smoke_test.py

# 装依赖 —— 必须带 --isolated
.venv/Scripts/python.exe -m pip --isolated install <pkg>
```

## 本机环境的坑

**不写在这里，你一定会踩。**
⚠️ 下面几条是**作者本机环境**特有的（Windows + 一个会拦截删除的沙箱 + pip 被全局配置接管）。
换个环境不一定适用 —— 但如果你也踩到了，照这里的办法改。
（Windows 专属的路径/命令，macOS / Linux 用户按惯例替换即可。）

| 坑 | 表现 | 正确做法 |
|---|---|---|
| pip 用户级配置指向共享包区 | 直接 `pip install` 会装进 `X:\python_packages`，**venv 仍是空的** | 加 `--isolated`。⚠️ `PIP_TARGET=`（空值）**无效** —— pip 把空环境变量当未设置 |
| `rm -rf` 被 safe-delete shim 拦截 | 走回收站，**中文路径会 mojibake 后失败**（`穿搭助手`→`绌挎惌鍔╂墜`），报 `SAFE_DELETE_FAIL_CLOSED` | 删之前**不要 `2>/dev/null`**；批量清理用 Python 的 `pathlib` 循环删，或干脆不生成 |
| **Git Bash 会弄坏反斜杠序列** | 在 `python -c "..."` / `node -e "..."` 里写 `\n`，进程**实际收到 `/n`**（正斜杠）→ `str.replace` 静默不匹配：**你以为改了，其实没改** | 把脚本**写成文件**（heredoc 或 Write 工具）再执行；必须内联时用 `chr(10)` 代替 `\n` |
| `curl` 被沙箱拦 | 报 "An error occurred while sending the request" | 用 `.venv/Scripts/python.exe` + `httpx` 探测 |
| `docker` / `podman` / `psql` / `redis-server` / `wsl` | 全都没有 | 不要提出依赖它们的方案 |

## Testing

```bash
.venv/Scripts/python.exe tests/smoke_test.py
# 期望输出：通过 83 项，失败 0 项
```

- **临时库必须落在系统临时目录**：`tempfile.mkdtemp()`（`smoke_test.py:25` 已是此法）。
  ⚠️ **禁止**在项目根建 `./.tmp_*` 或 `./data/xxx_test.db` —— 会留残留。
- **起服务一律带非默认端口**：`APP_PORT=8799 .venv/Scripts/python.exe run.py`。
  默认 8787 可能被别的进程占用，或两个 agent 同时起会撞。
- **不要用 `grep -c "check("` 数测试项** —— `def check(` 也会被数进去。
  当前真实是 **83 项**（P4 加了第 8/9 两节共 22 项；加完记得更新这里和上面两处）。

### 前端 UI 门禁（只读）

```bash
node scripts/checks/cdp_ui_check.js                          # 浏览器里量
.venv/Scripts/python.exe scripts/checks/check_contrast.py    # 纯算，不用浏览器
```

`cdp_ui_check.js` 用 CDP 驱动真 Chrome，量**三视口**（390 / 768 / 1440）的横向溢出、
手机端**触控目标 ≥44px**、**内容不被底部栏遮住**、Service Worker 注册、manifest，并出截图。
前置：Node ≥ 18 + Chrome 以 `--remote-debugging-port=9333` 常驻 + 应用在 8799 上跑。

`check_contrast.py` 按 WCAG 2.1 检查 token 色板上的**实际文字组合**（15 组，全部要 ≥4.5:1）。
**对比度是肉眼查不出来的那一类缺陷** —— 别跳过它。

判据见两个脚本的头部注释，规格见 `docs/design/ui-contract.md`。

⚠️ **Chrome 的 `--user-data-dir` 必须每次用全新目录** —— 复用会踩 ES module / SW 缓存，
表现为"改了代码却看不到变化"（假失败）。

## 提交 / 发布前的门禁

```bash
# 每台机器装一次：让 Git 用仓库自带的钩子（提交信息 + 提交内容两道）
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/commit-msg   # macOS / Linux 需要
```

**两道钩子，都在 `git commit` 时自动跑 —— 不用手动记得**：

| 钩子 | 拦什么 | 失败表现 |
|---|---|---|
| `commit-msg` | **提交信息**里的高置信密钥形态（`ghp_` / `sk-` / `AKIA` / 私钥 …） | 拒收，提示改 message |
| `pre-commit` | ① `scripts/checks/scan_leaks.py`（暂存文件里的密钥）② `scripts/checks/check_contrast.py`（对比度 < 4.5:1） | 拒收，列出命中的行 / 不达标的配色 |

- 两者都**自带自证**（先证明扫描器真能命中已知坏样本，再报结果），所以"通过"不是假绿。
- 误报可 `git commit --no-verify` 绕过 —— 但**先确认真的是误报**。
- `pre-commit` **找不到 Python 时只警告、不拦**（开源用户可能没建 venv）。
  因为"环境没装好"就挡住正常提交，会让人养成习惯性 `--no-verify`，门禁等于废掉。
- 暂存区为空时直接跳过（内容跟上次提交一样 → 上次已查过）。
- `scripts/checks/scan_leaks.py` 只扫 `git ls-files`（即"进仓库的那一份"）。个人 needle
  （真实姓名 / 本机路径）放 `scripts/leak_needles.local.txt`（已 gitignore，不进仓库）。

⚠️ **改门禁脚本后必须自证**：造一个假密钥 / 一个不达标的颜色，确认它真的拒收。
2026-10-20 实测四种场景：空暂存跳过 ✓ / 干净内容放行 ✓ / 假密钥拒收 ✓ / 对比度 1.04 拒收 ✓。

## 项目结构

```
run.py                      唯一入口
app/
  config.py                 配置集中处（含模型 key 的读取，全部有默认值、不会 import 期崩）
  schema.py                 SQLite 表结构 + 版本化迁移
  db.py                     连接 + 迁移执行（从 config 重新导出 DB_PATH/IMAGES_DIR）
  models.py                 Pydantic 请求/响应契约
  taxonomy.py               品类受控词表 + 槽位 + 组合数算法
  main.py                   FastAPI 装配
  services/                 业务逻辑（路由保持薄，逻辑都在这里）
    items.py  wear.py  wardrobe.py
    llm/                    模型接入层（供应商无关）
  routes/                   HTTP 层：参数校验 → 调 service → 按响应模型返回
android/                  Android APK 壳（Java + Gradle + 离线 wheel）
                          构建：`sh android/build_apk.sh`；坑与工具链见 android/README.md
                          ⚠️ shell 代码在仓库、构建目录在 C:/wbuild/aiw（纯 ASCII 路径）
docs/                       设计文档（**规格在这里**）
tests/                      测试
data/                       运行时数据（gitignored，**不要手动改**）
```

## 代码风格

- **注释用中文**。引用设计文档用 `§x.y` 章节号（不是路径）。
- **路由保持薄**：校验参数、调 service、返回。不在这里写业务逻辑，也不直接拼 SQL。
- **业务逻辑进 `services/`**，这样以后换入口（微信 / 网页 / CLI）不用改业务层。
- 数据库时间戳一律 `db.now_iso()`；日期字段用 `db.now_iso()[:10]`（`YYYY-MM-DD`）。
- 单品的多值字段在库里是 JSON 文本，读写用 `services/items.py` 的 `_loads_list` / `json.dumps(..., ensure_ascii=False)`。

```python
# 正确：短连接 + 自动 commit/rollback
with db.conn() as c:
    c.execute("SELECT ...", (arg,))

# 错误：自己管连接
c = sqlite3.connect(...)
```

## Do Not Modify

**除非你被明确指派修改，以下文件不要动**（它们的改动会影响多个模块，需要统一决策）：

| 文件 | 为什么 |
|---|---|
| `app/schema.py` | 改表结构要写迁移，且只在版本落后时执行 |
| `app/models.py` | 对外契约，改一处影响所有路由与前端 |
| `app/config.py` | 配置集中处 |
| `app/main.py` | 路由挂载点，是**唯一的共享写入点**（每加一个路由文件都要 `include_router`） |
| `app/taxonomy.py` | 品类词表是缺口分析/组合数/打标的地基 |

`app/__init__.py`、`app/services/__init__.py`、`app/routes/__init__.py` 都只是 docstring
（**没有模块清单**）→ 新增模块**不需要**改它们。

## 规格在哪（动手前先读）

| 内容 | 文件 |
|---|---|
| 行为规格：定位 / 场景 / 数据模型 / 指标 | `docs/history/设计文档_v0.2.md` |
| 算法规格：偏好衰减公式、LLM 架构 | `docs/history/设计文档_v0.2.md` §5 / §10 |
| 微信通道的实现设计 | `docs/history/p3-design.md` |
| 微信 iLink 协议（带 ✅实测 / 📄资料 分级） | `docs/history/ilink-protocol.md` |
| 路线图与当前阶段 | `README.md` 的「路线图」一节 |

**关键公式**（实现时逐字对齐，别自己简化）：`context_key = {season}|{scene}`、
衰减 `w(Δt)=0.5**(Δt_days/45)`、增量更新 `score=(score*w_old*n + r*k)/(n*w_old + k)`、
`confidence=(n/(n+5))*(0.5+0.5*|decayed_score|)`、MMR `λ=0.7`。

## 报告工作时的规矩

- **不要说"已完成"** —— 给出可复现的命令输出（如 `通过 83 项，失败 0 项`）或文件指纹。
- 没验证的结论标"未验证"；是推断就写"推断"。**不要用"唯一 / 只有 / 必然"这类排他性词**，
  除非你真的穷举验证过。
- 发现规格本身有问题（矛盾、缺口）→ **停下来报告**，不要自己拍板填。

## 目录整理后的入口

继续开发先读docs/DEVELOPMENT.md与docs/HANDOFF.md。现行设计集中docs/design/，历史讨论docs/history/。web/js分core、components与features；features包含wardrobe/import/outfits/purchase。新增或移动前端文件必须运行scripts/checks/web_assets.py --write，再以默认检查模式验证。不要手抄SW清单。

电脑中转入口scripts/bridge/manage.py start|stop|status，本机配置在忽略目录.local/bridge.json；登录和pairing.json保持本机。Android工具链配置在.local/toolchain.env，构建目录不要放源码内部。构建同步工具会清过期副本，路径检查必须在任何骨架复制之前。

保留既有IndexedDB标识、模型、账号、手机用户数据与schema，目录重构不得顺带变更这些契约。
