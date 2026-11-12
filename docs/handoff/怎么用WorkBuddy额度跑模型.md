# 怎么用 WorkBuddy 账号额度跑模型（给 AI 的说明）

> 复制下面整段给任何 AI，它就知道这套机制怎么调、怎么修、哪里会踩坑。
> 最后更新：2026-11-05。配对码不写在这里，见 `.local/runtime/desktop-bridge/pairing.json`。

---

## 复制给 AI 的内容（从这里开始）

我这台 Windows 电脑上有一套「用 WorkBuddy 账号额度跑模型」的通道，不用 API key，
是驱动官方 CLI 消耗我账号里的额度。项目在 `C:\Users\MCXS\工作流打通\ai衣橱\ai-wardrobe`。
你要改它、修它或基于它加功能，按下面说明做，不要自己发明另一套。

### 一、链路

```
手机 App / 任何客户端
  → http://<电脑局域网IP>:8891
  → scripts/bridge/desktop_bridge.py （FastAPI，电脑中转）
  → codebuddy CLI（官方 npm 包，必须在真 TTY 里跑）
  → WorkBuddy 账号额度
  → 模型 deepseek-v4.1-flash
```

### 二、关键路径（全部相对项目根）

| 用途 | 路径 |
|---|---|
| 中转服务 | `scripts/bridge/desktop_bridge.py` |
| 启停管理 | `scripts/bridge/manage.py`（start / stop / status） |
| TTY 驱动 | `scripts/bridge/tty_runner.py` |
| CLI 入口 | `.local/tools/workbuddy-cli/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy` |
| Node | `C:\Program Files\nodejs\node.exe` |
| 运行目录 | `.local/runtime/desktop-bridge`（`pairing.json` 配对码、`server.log` 日志） |
| 双击启动 | `启动电脑中转.bat` |
| Python | `.venv\Scripts\python.exe` |

### 三、启动与自检

```bat
cd /d C:\Users\MCXS\工作流打通\ai衣橱\ai-wardrobe
.venv\Scripts\python.exe scripts\bridge\manage.py start
```

成功返回：`{"status":"ready","port":8891,"build":"<哈希>","model":"deepseek-v4.1-flash",...}`
`status` 子命令查状态，`stop` 停止。**build 哈希变了说明代码改过，旧实例必须重启才生效。**

### 四、调用接口

```http
GET  http://<IP>:8891/health
Authorization: Bearer <pairing.json 里的 token>

POST http://<IP>:8891/chat
Authorization: Bearer <pairing.json 里的 token>
Content-Type: application/json

{
  "model": "deepseek-v4.1-flash",
  "messages": [{"role": "user", "content": "..."}],
  "schema": null,
  "json_output": false
}
```

返回：`{"model":"deepseek-v4.1-flash","text":"...","engine":"account"}`

- `model` 必须**正好**是 `deepseek-v4.1-flash`，写别的直接 400。
- `schema` 传 JSON Schema 可要结构化输出，服务端会把它拼进 system。
- `json_output: true` 要求纯 JSON 回包。
- `engine: "account"` 表示走账号额度；另有 `engine: "api"` 走电脑上另配的 API key。

### 五、四个硬约束（少一个就报错）

1. **启动 CLI 前必须剥掉所有 `ACC_PRODUCT_CONFIG*` 环境变量。**
   WorkBuddy 桌面端会把 `ACC_PRODUCT_CONFIG_PATH` 溢出到 `%Temp%\workbuddy-product-spill-*\`，
   里面 `authentication.id=workbuddy-desktop`。CLI 读到它只认那个身份的 token，而那份凭据
   不落盘或早过期，于是每次都报 `Authentication required`。剥掉后 CLI 回落到包内 `product.json`
   （CodeBuddy / `cli-external-link`），走 `/login` 写盘的
   `%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\Tencent-Cloud.coding-copilot.info`。

2. **CLI 必须跑在真 TTY 里**（mintty + bash 组合），普通管道重定向不行。

3. **同一时刻只允许一个 CLI 请求**。服务端用 `busy` 锁，第二个直接 429「电脑正在识别」。

4. **不要继承** `CODEBUDDY_` / `ANTHROPIC_` / `LLM_` / `OPENAI_` 前缀的环境变量，
   也不要继承 `NODE_OPTIONS` / `ELECTRON_RUN_AS_NODE`。`http(s)_proxy` 要保留。

### 六、报错对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `Authentication required` / 401 | 桌面端登录过期 | 重开 WorkBuddy 桌面端登录 |
| `配对校验失败` / 403 | token 不对 | 核对 `.local/runtime/desktop-bridge/pairing.json` |
| `电脑正在识别` / 429 | 上一单没跑完 | 等几秒重试 |
| `10048` 绑定失败 | 已有实例占着端口 | 先 `manage.py stop` 或结束进程 |
| 手机报「连接超时」 | 手机存的 IP ≠ 电脑当前 IP | `Get-NetIPAddress` 查电脑 IP，对比手机 `files/desktop-bridge.json` |
| 电脑 ping 不通手机 | Windows 默认挡 ICMP | 别用 ping 判断，直接打端口 |

### 七、验收标准

三条全中才算可用：

1. `/health` 返回 `status: ready`
2. `/chat` 发一句话能拿回 `text`
3. 返回里 `engine` 是 `account`

### 八、额度消耗在哪

按 token 计，不是按次数。主要三个口子：

- **拍照/上传识别**（带图，最贵）——每件衣服入库一次
- **分析衣橱 / 出搭配**（纯文本）——点一次算一次，最多 12 件进 prompt
- **图片生成**（平铺图/款式图）——走另一个图片模型，单价通常更高

已内置的三个省额度的闸：`limit=1`（一次只出一套）、`MAX_TO_LLM=12`（最多 12 件）、
`busy` 单通道锁（不会并发烧）。**「换一套」每点一次就是一次完整调用**，别连点。

## （复制到这里结束）
