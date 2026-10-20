# 交接：换一台机器继续开发

> **最后更新**：2026-10-20 · **当前 commit**：`c2efa3a`
> 这份文档的用途：换机器后，人和 AI 都能在 5 分钟内接上上下文。
> **先读 `AGENTS.md`（约定与禁区），再读本文件（进度与待办）。**

---

## 一、现在在哪（已完成、已推送）

| 能力 | 状态 | 关键文件 |
|---|---|---|
| 衣橱 / 搭配 / 缺口 / 录入 四个页面 | ✅ | `web/js/view-*.js` |
| 前端设计系统（Apple 风 + token 化） | ✅ | `docs/ui-contract.md` · `web/css/tokens.css` |
| PWA（可装到手机） | ✅ | `web/sw.js` · `web/manifest.webmanifest` |
| 粘贴商品文案录入（P5） | ✅ | `services/ingest.py` |
| 视觉打标（P6，上传照片自动认属性） | ✅ | `services/tagging.py` |
| **裤长独立成维度**（schema v2） | ✅ | `taxonomy.py` · `schema.py` |
| **纯图录入自动起名**（白色棉T恤） | ✅ | `items.auto_name()` |
| **Ctrl+V 粘贴 / 拖拽图片** | ✅ | `web/js/view-add.js` |
| 提交门禁（密钥 + 对比度） | ✅ | `.githooks/pre-commit` |

**测试基线**：`pytest` 775 passed · `smoke_test` 83 项 0 失败。

---

## 二、新机器怎么跑起来

```bash
git clone https://github.com/Zarek-Zhao1112/ai-wardrobe.git
cd ai-wardrobe

# 1. 虚拟环境
python -m venv .venv

# 2. 装依赖（⚠️ 见下方「坑 1」）
.venv/Scripts/python -m pip install -r requirements-dev.txt

# 3. 配模型 key（.env 不在仓库里，必须自己建）
cp .env.example .env
#   然后编辑 .env，至少填这三行：
#     LLM_BASE_URL=https://api.deepseek.com/v1
#     LLM_API_KEY=<你的 key>
#     LLM_MODEL=deepseek-v4-pro
#     VISION_MODEL=deepseek-flash     ← 不填则打标不可用（其余功能正常）

# 4. 起服务
.venv/Scripts/python run.py           # http://127.0.0.1:8787
```

**跑完先验证**：

```bash
CLAWBOT_WORKER_ENABLED=false .venv/Scripts/python -m pytest -q      # 期望 775 passed
CLAWBOT_WORKER_ENABLED=false .venv/Scripts/python tests/smoke_test.py  # 期望 83 项 0 失败
.venv/Scripts/python scripts/check_contrast.py                       # 期望 15 组 0 不达标
```

⚠️ **`CLAWBOT_WORKER_ENABLED=false` 不能省**：不然测试会去连微信长轮询，卡住。

---

## 三、下一步做什么（按优先级）

### P0 · 把衣橱喂起来（阻塞一切）

**为什么**：衣橱现在是 **0 件**。搭配、缺口、组合数全靠数据，
空着的话所有功能都是空转，也看不出设计对不对。

**怎么做**：录入页传照片（有图就行，不用先选品类，AI 会认并自动起名）。
建议先来：上衣 3–4 件、下装 3 件、鞋 2–3 双。

**验收**：衣橱页出现卡片；搭配页能出第一套；缺口页说出缺什么。

---

### P1 · 修「服务没起时 PWA 静默显示旧版」

**为什么**：`sw.js` 的 `networkFirst` 在请求失败时回落缓存，
**但界面不告诉你**。2026-10-20 因此白排查了一轮：
服务没起来 → 看到的是几天前的旧界面 → 误以为"代码没更新"。

**怎么做**：`web/sw.js` 的 `networkFirst` 里，从缓存回落时给页面一个标记
（比如在响应头或注入一个 `window.__STALE__`），前端显示一条
「⚠️ 当前是离线缓存，服务没连上」。

**验收**：停掉服务 → 打开页面 → 能看到那条提示；服务起来后刷新 → 提示消失。

---

### P2 · `deploy/` 提交进仓库

**为什么**：`deploy/` 一直是**未跟踪**状态（`git status` 里是 `??`），
里面有手机部署脚本，用户装到手机就靠它。开源别人也需要。

**现状**：4 个文件 —— `termux-setup.sh`（装）、`sync-termux.sh`（同步）、
`termux-autostart.sh`（开机自启）、`README.md`、`fetch_android_wheel.py`。

**为什么还没提交**：原计划"等真机跑通再提交"。目前
`termux-setup.sh` 跑通过；`sync-termux.sh` 和 `termux-autostart.sh` 是
2026-10-20 新写的，**还没在真机上验证过**。

**验收**：手机能开机自启 → 点 PWA 图标就能用 → 再把 `deploy/` 提交。

---

### P3 · 手机端与电脑端的数据要不要打通

**现状**：手机（Termux 自跑）和电脑各有一份 `data/wardrobe.db`，**互不相通**。
手机录的衣服电脑看不到，反之亦然。

**要决策的不是"怎么做"，而是"要不要做"**：
- 做：需要同步机制（导出/导入、或局域网共享一个库）—— 有冲突风险
- 不做：明确"主力机是哪台"，另一台只当临时查看

**⚠️ 这是产品决策，动手前必须问用户。**

---

### P4 · 从商品详情页借鉴的三点（原定计划，未开工）

来源：用户给的识货商品详情页（2026-10-20 调研）。**只借鉴信息层级，不抄内容**
（那是购买页，这是衣橱页）。

| # | 借鉴什么 | 换成什么 | 难度 |
|---|---|---|---|
| ① | 参数压成「横排速览」，不摊成长表单 | 详情页 14 个字段现在全摊开，手机上要滚很久 | 小 |
| ② | 「笔记精选」（真人穿这件） | **「我穿它的记录」** —— 数据已有（`GET /items/wear/events`），但界面只显示"穿了 3 次" | 中 |
| ③ | 「精选推荐」（相似商品） | **「这件能配什么」** | 大 |

⚠️ ③ 有个硬限制：`POST /outfits/recommend` 只接受**场景/季节**，
**不接受"以某件单品为中心"**，要改推荐服务。
固定单品属于**硬约束**，不能只靠 MMR 软约束（会静默出错），要在后置校验里卡死。

---

## 四、改代码前必须知道的约定

| 约定 | 要点 |
|---|---|
| **架构口诀** | 本地准备 → 大模型 → 本地校验。**会"静默算错"的一律留在本地** |
| **词表是地基** | `taxonomy.py` 改一个词会影响：品类识别、槽位、组合数、打标、粘贴录入 |
| **槽位决定一切** | 算错 `slot` → 该单品**从所有搭配里静默消失**（不报错） |
| **schema 迁移** | 只能加表/加列/加索引；`SCHEMA_VERSION` +1，在 `MIGRATIONS` 末尾追加 |
| **提交门禁** | `git config core.hooksPath .githooks`（每台机器一次），提交时自动查密钥+对比度 |
| **不要 `git add -A`** | 显式列路径。`data/`、`.env`、`.workbuddy/`、内部文档都不能进仓库 |

---

## 五、换机器也可能踩的坑

1. **pip 装不进 venv**
   有些机器配了全局 `pip.target`。用 `python -m pip --isolated install ...`。
2. **`git push` 报 `could not read Username`**
   沙箱/无终端环境下凭据选择器无法交互 → 用一次性 token URL：
   `git -c credential.helper= -c http.sslVerify=false push "https://<TOKEN>@github.com/..."`
   推完**确认 `.git/config` 里没有残留 token**。
3. **`git push` 报 502**
   是网络层瞬时问题，**重试一次通常就过**。别急着怀疑 token。
4. **改了前端却看不到变化**
   浏览器 **ES module 缓存**。用新 profile 目录 + `Network.setCacheDisabled`。
   给 hash 路由加 `?nocache=` 是**错的**（`?` 在 `#` 后属于 fragment，会让路由匹配不上）。
5. **手机端 `git pull` 报 `not a git repository`**
   手机上的目录是**传文件**部署的，没有 `.git`。用 `deploy/sync-termux.sh` 打包含覆盖。
6. **手机端改了代码却还在跑旧逻辑**
   `pkill + nohup` 会**绕过 `termux-services` 托管**：sv 会自动重启一份（旧代码），
   你再手动起一份 → 端口冲突 → 实际跑的还是旧的。
   **必须用 `sv restart`**。验证方式是**问服务要一份前端文件**，不是只看文件时间。
