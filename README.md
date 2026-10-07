# AI 穿搭助手

> A personal AI stylist that gradually learns your wardrobe, your body and your taste.

## 这是什么

一个**跑在你自己机器上**的个人 AI 穿搭助手。

它不做"拍张照就给你搭一套"的一次性工具，而是长期积累三样东西 —— 你的衣橱、你的身材、你的审美偏好 —— 然后根据你真实的生活场景（今天什么天气、要去哪、想穿哪件）帮你做决定。

一句话：**用得越久，它越懂你。**

它和同类产品最大的区别是**分发方式**：没有云端账号、没有注册流程，数据全部存在你本机。所以除了你自己，没人看得到你的衣柜。

## 现在能做什么（诚实版）

| 能力 | 状态 |
|---|---|
| 衣橱增删改查 · 图片上传 | ✅ |
| 当季可搭组合数 | ✅ |
| 衣橱缺口分析（"我还缺什么"） | ✅ |
| 穿着记录 · 反馈闭环 · 偏好记忆（衰减 + 置信度） | ✅ |
| 推荐搭配（本地打分 + 多样性校验） | ✅ **不配模型也能用** |
| 推荐的排序与理由由大模型补 | ✅ 需配模型 key；不配则退回本地排序 |
| **本地网页界面** | ✅ 零构建，原生 ES module |
| **微信入口**（发消息问搭配 / 记衣服 / 看缺口） | ✅ 需微信里有 ClawBot 插件 + 扫一次码 |
| 发照片自动识别属性 | ❌ **暂不实现**（见下） |
| 粘贴商品分享文案 → 自动抽属性入库 | 🚧 规划中（P5） |

**关于"拍照自动打标"**：这条路需要一个能看图的模型。作者用的 DeepSeek 只有文本模型，
所以这块**暂不实现** —— 代码骨架留着（`app/services/tagging.py`），接上双模态供应商即可启用。

**关于 P5**：原计划是"发商品链接 → 程序去读商品页"。实测发现淘宝等平台在**无登录态下读不到商品数据**
（x5 风控 + 要求登录，六条入口全试过）。但淘宝的**分享文案里本来就带完整商品标题**，
所以改成解析分享文案这条路：**零反爬风险、零维护成本，用户操作一步都不用变**。

## 快速开始

```bash
git clone <repo-url>
cd 穿搭助手

python -m venv .venv

# Windows
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe run.py

# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

然后打开 <http://127.0.0.1:8787/> —— 这就是界面。

**Windows 用户**也可以直接**双击 `start.bat`**：它会检查环境、起服务、并在服务真的起来之后自动打开浏览器。

> 如果 `pip install` 装完却 `import` 不到包，试试加 `--isolated`
> （我们遇到过 pip 被全局 target 配置接管的情况，细节记在 `AGENTS.md`）。

**数据库和目录会自动创建**，第一次跑不需要任何手工初始化。

## 数据放在哪 / 隐私

所有运行时数据都在 `data/` 下，**已在 `.gitignore` 中排除**：

| 文件 | 内容 |
|---|---|
| `data/wardrobe.db` | 你的衣橱（SQLite） |
| `data/images/` | 衣服照片 |
| `data/clawbot_session.json` | 微信登录凭据（绑定了微信才有） |
| `data/app.log` | 运行日志 |

**没有任何东西会离开这台机器**，除非你自己配了模型 API ——
那种情况下，只有搭配候选的一小段**文字清单**会发给模型，用来排序和写理由。

想把数据放到别的盘（比如同步盘）：设 `WARDROBE_DATA_DIR`。

## 环境变量

复制 `.env.example` 为 `.env` 再填（`.env` 已被 gitignore）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | 监听地址。改 `0.0.0.0` 可让同一局域网内的手机访问 |
| `APP_PORT` | `8787` | 监听端口 |
| `APP_OPEN_BROWSER` | — | 设 `1` 则启动后自动打开浏览器（`start.bat` 用的） |
| `WARDROBE_DATA_DIR` | `<项目根>/data` | 数据目录 |
| `WARDROBE_DB` | — | 直接指定数据库文件，优先级最高（主要给测试用） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | — | **可选**。任何 OpenAI 兼容接口：DeepSeek / 豆包 / 硅基流动 / 本地 Ollama |
| `VISION_MODEL` | — | 可选，视觉模型（用于打标；DeepSeek 没有） |
| `KNOWLEDGE_VAULT_DIR` | 空 = 功能关闭 | 可选，你的 Obsidian 笔记库路径（P4 知识库技能用） |
| `CLAWBOT_WORKER_ENABLED` | `true` | 关掉微信后台轮询线程（跑测试时必须关） |

**不配模型也能用**：推荐退回本地排序，只是没有理由说明。

## 微信入口

**不需要部署任何额外服务** —— iLink 是纯 HTTP，程序**主动长轮询**去取消息，
所以**不需要公网 IP、不需要内网穿透、不需要 Docker**。

1. 起服务，打开 <http://127.0.0.1:8787/bind.html>
2. 点「开始绑定」→ 手机微信扫码 → 授权
3. 凭据存进 `data/clawbot_session.json`，后台线程开始收消息（**不用重启**）
4. 在微信里跟它说话：
   - `我有一件深灰宽松卫衣` → 回「对」入库
   - `今天穿什么` → 给 3 套，带序号和理由
   - `2` / `这套行` / `换一套` → 记反馈或换一批
   - `缺什么` → 缺口分析
   - 发**语音**也行（上游自带转文字）
5. 想断开：绑定页点「解绑」，或删掉那个 json 文件

⚠️ 需要你的微信里有 **ClawBot 插件**（官方灰度放量中，没有则这条路走不通）。

## 目录结构

```
穿搭助手/
├── run.py                  启动入口（端口占用检测 / 可选自动开浏览器）
├── start.bat               Windows 一键启动
├── app/
│   ├── config.py             路径与配置（含零依赖 .env 加载器）
│   ├── db.py                 连接管理 + schema 自动迁移
│   ├── schema.py             建表语句 + 版本化迁移
│   ├── taxonomy.py           品类受控词表 + 槽位 + 组合数算法
│   ├── models.py             接口的请求/响应契约
│   ├── main.py               FastAPI 装配
│   ├── cli.py                命令行（造数据 / 批量导入）
│   ├── services/             业务逻辑（路由层保持"薄"）
│   │   ├── items.py  wear.py  wardrobe.py  images.py
│   │   ├── preference.py     偏好记忆：条件化衰减 + 置信度
│   │   ├── recommend.py      推荐：本地打分 → MMR →（可选）模型排序
│   │   ├── tagging.py        图片打标（需要视觉模型，默认不可用）
│   │   ├── feedback.py       反馈 → 触发偏好增量
│   │   ├── conversation.py   微信会话状态机（**不认协议**）
│   │   ├── knowledge/        知识库技能（P4）
│   │   └── llm/              模型接入层（供应商无关）
│   ├── channels/             微信通道（**认协议不认业务**）
│   │   └── clawbot.py            iLink 协议：扫码 / 长轮询 / 发送
│   └── routes/               HTTP 接口
├── web/                    前端：原生 ES module，**零构建**
│   ├── index.html  bind.html
│   └── js/  api · ui · app(hash 路由) · fields(表单) · view-*
├── docs/                   设计文档
├── tests/                  测试（pytest + 端到端 smoke）
└── data/                   运行数据（不进 git）
```

**两条架构规矩**（动手改代码前建议先看 `AGENTS.md`）：

- 口诀：**本地准备 → 大模型 → 本地校验**（两头厚、中间薄）。会"静默算错"的一律留在本地。
- `channels/` 认协议不认业务；`services/conversation.py` 认业务不认协议 ——
  **换通道（企业微信 / Telegram / 命令行）时业务层一行不用改**。

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 地基：数据库、品类词表、衣橱 CRUD、缺口分析、穿着记录 | ✅ |
| P1 | 后端完整：偏好记忆、推荐、批量导入、图片上传 | ✅ |
| P2 | 本地网页界面 | ✅ |
| P3 | **微信入口** —— iLink 打通（扫码 / 收发 / 三个场景） | ✅ |
| P4 | 知识库技能（微信里聊学习 → 读笔记 → 写成新笔记） | ✅ |
| P5 | 商品分享文案 → 自动抽属性入库 | 🚧 规划中 |
| — | 发照片自动识别（需要视觉模型） | ❌ 明确不做 |

## 设计文档

- [`docs/设计文档_v0.2.md`](docs/设计文档_v0.2.md) —— **产品规格（现行版本）**。
  逐条标注了相对 v0.1 的 `【保留】/【修订】/【新增】/【推翻】`，包含大量推翻与重做的理由。
  想看"为什么这么设计"就读它。
- [`docs/p3-design.md`](docs/p3-design.md) —— 微信入口的实现设计（分层、会话状态机、过期策略）
- [`docs/knowledge-design.md`](docs/knowledge-design.md) —— 知识库技能的实现设计
- [`docs/ilink-protocol.md`](docs/ilink-protocol.md) —— iLink 协议笔记，
  **每条都标了证据等级**（✅ 已实测 / 📄 来自资料）。这部分对想接微信的人可能最有用。

## 许可证

MIT，见 [LICENSE](LICENSE)。
