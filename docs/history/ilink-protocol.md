# iLink / ClawBot 协议要点（P3 实现依据）

> **本文用途**：P3 微信通道的实现依据。协议细节从公开资料整理，**不是官方文档的完整复制**。
>
> ⚠️ **证据分级（务必看清，别把整理当实测）**
>
> | 标记 | 含义 |
> |---|---|
> | ✅ **已实测** | 我在本机真发过请求、拿到了响应 |
> | 📄 **来自资料** | 从公开文档/教程整理，**本机未验证**。实现时必须先探再信 |
>
> 官方网关：`https://ilinkai.weixin.qq.com`（本文统一简称 `base`）
> 本机网络可达性：✅ **已实测**（域名解析 + TLS 正常）

---

## 0. 为什么值得做

微信 2026-03-22 开放的**官方**协议（签过《微信 ClawBot 功能使用条款》），
**不是逆向**。纯 HTTP/JSON，**不需要 SDK、不需要公网 IP**（长轮询由云端中转）。

对我们最要紧的一点：**绑定只需要一次扫码**，不需要用户去装 OpenClaw 或配连接器
—— 所以它可以完全内建在本项目里（`app/channels/clawbot.py`）。

---

## 1. 端点清单

| 端点 | 方法 | 作用 | 证据 |
|---|---|---|---|
| `/ilink/bot/get_bot_qrcode` | GET | 获取登录二维码 | ✅ 已实测 |
| `/ilink/bot/get_qrcode_status` | GET | 轮询扫码状态 | ✅ 已实测（**也是长轮询，见下**） |
| `/ilink/bot/getupdates` | POST | **长轮询收消息**（服务端 hold 最多 ~35s） | 📄 来自资料 |
| `/ilink/bot/sendmessage` | POST | 发消息 | 📄 来自资料 |
| `/ilink/bot/sendtyping` | POST | 发"正在输入…" | 📄 来自资料 |
| `/ilink/bot/getuploadurl` | POST | 获取 CDN 上传地址 | 📄 来自资料 |
| `/ilink/bot/getconfig` | POST | 获取 Bot 配置 | 📄 来自资料 |

### ✅ 已实测：`get_bot_qrcode`

```
GET {base}/ilink/bot/get_bot_qrcode?bot_type=3
→ 200
{
  "ret": 0,
  "qrcode": "1bf7d41a08969e6dafc06dad34436bc2",
  "qrcode_img_content": "https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=<qrcode>&bot_type=3"
}
```

要点：
- **不需要鉴权**（还没登录，当然没有 token）
- `qrcode_img_content` 是**扫码内容**（URL），不是图片 —— 要自己渲染成二维码
- `qrcode` 是个 ID，后面轮询状态要用它
- 📄 资料称 `expire_time` 约 **120 秒**（实测响应里**没有**这个字段，但二维码会过期，
  实现时要能"重新生成"）

---

## 2. 绑定流程（三步）

```
① GET  /ilink/bot/get_bot_qrcode?bot_type=3     → qrcode + 扫码URL
② GET  /ilink/bot/get_qrcode_status?qrcode=xxx  → 轮询
     status: wait → scanned → confirmed
③ confirmed 时返回：
     bot_token        ← 后续所有请求的 Bearer
     ilink_bot_id     形如 "xxx@im.bot"
     ilink_user_id    形如 "xxx@im.wechat"
     baseurl
```

### ✅ 已实测：`get_qrcode_status` 的真实行为

```json
{"ret": 0, "status": "wait"}      // 刚生成的有效二维码
{"ret": 0, "status": "expired"}   // 过期的 / 不存在的 qrcode
```

**三条实测结论**（发布时文档都没写）：

1. ★ **它也是长轮询，hold 约 30 秒**（实测 30.1s）。
   文档把它描述成"一次普通轮询"，**不对**。
   → **前端必须按长轮询用**：发一个请求、等它返回、再发下一个。
   **不要**每 2 秒调一次 —— 那会在 30 秒内堆积 15 个挂住的请求。
   → 我们这边的 `timeout` 必须 > 30s（用 45s），否则永远 `ReadTimeout`。
2. **字段名就是 `status`**（不是嵌套、不是别名），与资料一致 ✓
3. `expired` 是真实取值。**不存在的 qrcode 也返回 `expired`**（不报错）——
   所以"二维码失效"和"乱填一个 qrcode"从响应上分不出来，这是正常的。

**未观测**（需要真扫码，属禁区）：`scanned` 与 `confirmed` 两个取值、
以及 `confirmed` 时那几个字段的**真实名字**（`bot_token`? `ilink_bot_id`?）。
→ 实现里对这四个字段做了**别名兼容**（同时认 `token`/`bot_id`/`user_id`/`base_url`），
避免照抄错名导致拿不到 token。

**拿到 token 后要持久化** —— 存在 `data/clawbot_session.json`（已 gitignore）。
不用数据库的理由：**删文件即可重置绑定**，跟业务数据解耦。
📄 资料称消息保活约 24 小时 → token 可能过期需要重扫，**待实测确认**。

---

## 3. 请求头（收 / 发消息都要）

📄 来自资料：

```
Content-Type:      application/json
AuthorizationType: ilink_bot_token
Authorization:     Bearer <bot_token>
X-WECHAT-UIN:      <base64(random_uint32)>
```

⚠️ `AuthorizationType` 这个**非标准头**是这个协议特有的，别漏。

---

## 4. 收消息（长轮询）

```
POST {base}/ilink/bot/getupdates
{
  "get_updates_buf": "<上次返回的游标>",
  "base_info": { "channel_version": "1.0.2" }
}
```

响应：

```json
{
  "ret": 0,
  "get_updates_buf": "xxx",
  "msgs": [{
    "msg_id": "xxx",
    "from_user_id": "user@im.wechat",
    "to_user_id": "bot@im.bot",
    "message_type": 1,
    "context_token": "xxx",
    "create_time_ms": 1234567890000,
    "item_list": [{ "type": 1, "text_item": { "text": "消息内容" } }]
  }]
}
```

### ⚠️ 两个必须记住的点

1. **`get_updates_buf` 是同步游标**：收到后**原样保存、下次原样回传**，
   **不要解析、不要修改**。它决定"从哪继续拉"。丢了会重复收或漏收。
2. **`context_token` 必须原样回传**（设计文档 §13 的"关键坑 1"）——
   不回传消息就**关联不到正确的会话窗口**，而且**不报错**（静默错配）。
   这是本协议最容易踩的坑。

### 消息内容类型（`item_list[].type`）

| type | 内容 | 取值路径 |
|---|---|---|
| 1 | 文本 | `text_item.text` |
| 2 | 图片 | `image_item.media` / `image_item.thumb_media` |
| 3 | 语音 | `voice_item.media` / `voice_item.text`（**有转文字**） |
| 4 | 文件 | `file_item.file_name` / `file_item.media` |
| 5 | 视频 | `video_item.media` / `video_item.thumb_media` |

★ **语音自带转文字**（`voice_item.text`）—— 对我们有额外价值：**用户可以直接说
"这件怎么搭"，不用打字**。早上出门前 30 秒的场景里，说话比打字快得多。

---

## 5. 发消息

```
POST {base}/ilink/bot/sendmessage
{
  "msg": {
    "to_user_id":   "<入站的 from_user_id>",
    "client_id":    "<客户端生成的唯一 ID，用于去重>",
    "message_type": 2,
    "message_state": 2,
    "context_token": "<★ 从入站消息原样带过来>",
    "item_list": [{ "type": 1, "text_item": { "text": "要发的内容" } }]
  },
  "base_info": { "channel_version": "1.0.2" }
}
```

| 字段 | 值 | 说明 |
|---|---|---|
| `message_type` | 2 | 2 = BOT 发出的消息（入站是 1） |
| `message_state` | 2 | 2 = FINISH |
| `client_id` | 自生成 | 唯一即可，用于**去重**（重发不会被当成两条） |
| `context_token` | 从入站取 | **必须原样**，否则关联不到会话 |

---

## 6. 媒体文件（图片 / 语音 / 视频）

📄 来自资料，⚠️ **本机未验证**：

- CDN 上的媒体是 **AES-128-ECB 加密**的，下载后必须解密
- 上传走 `getuploadurl` 拿地址再传
- CDN 域名形如 `novac2c...`（资料里被截断，实现时要探）

**对我们的影响**：场景 B（发照片打标）要先过这一关。
→ 所以 P3 的**第一版不做图片**（见 `docs/tasks.md` 的 P3 范围），
先把文字链路（场景 A/C/E）跑通，图片作为第二阶段 —— **这样能把"协议能通"和
"媒体解密能通"两个风险分开验证**，而不是一次赌两件没验过的事。

---

## 7. 已知限制（📄 来自资料）

| 限制 | 影响 |
|---|---|
| **仅单聊**（暂不支持群聊） | 我们的场景本来就是一对一，无影响 |
| 微信版本要求 iOS 8.0.70+ / Android 8.0.68+ | 用户需确认版本 |
| ClawBot 插件**灰度放量中**，可能看不到入口 | ⚠️ **这是最大的不确定性**：用户微信里可能还没这个插件 |
| 消息保活约 24 小时 | token 可能要重扫 —— **待实测** |
| 插件不访问通讯录与历史记录 | 隐私上是好事 |

⚠️ **第一条要提前确认的事**：用户的微信 `我 → 设置 → 插件` 里**有没有 ClawBot**。
没有的话整条路走不通（且不是我们能解决的）。**这件事要在写代码之前问**。

---

## 8. 实现位置与职责边界

```
app/channels/clawbot.py     ← 纯协议层：绑定 / 长轮询 / 收发 / 媒体解密
app/services/conversation.py ← 会话状态机：意图识别 → 路由到业务
```

**边界**：`clawbot.py` **不认识任何业务概念**（不知道"穿搭""单品""搭配"），
它只负责"把微信消息变成 `InboundMessage`"和"把文本发出去"。
反过来 `conversation.py` 不认识任何协议细节（不知道 `context_token`、不知道长轮询）。
→ 这样换通道（企业微信 / Telegram / 网页）时，`conversation.py` 一行不用改。

---

## 9. 本机环境注意

- `curl` **被沙箱拦截** → 用 `.venv/Scripts/python.exe` + `httpx` 探测（✅ 已验证可行）
- 长轮询会 hold 35 秒 → 起服务做测试时**别用会超时的探测方式**
- 测试时**不要真的完成绑定**（那会占掉用户的微信绑定位）——
  绑定只在用户亲自扫码时发生
