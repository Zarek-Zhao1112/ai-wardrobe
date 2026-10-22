# 交接：换一台机器继续开发

> **最后更新**：2026-10-22 · **写这份文档时的 commit**：`a2e3f1a`（以 `git log -1` 为准 —— 本文件自己也会被提交，别盯着这行比）
> 这份文档的用途：换机器后，人和 AI 都能在 5 分钟内接上上下文。
> **先读 `AGENTS.md`（约定与禁区），再读本文件（进度与待办）。**

---

## 一、现在在哪（已完成、已推送）

| 能力 | 状态 | 关键文件 |
|---|---|---|
| 衣橱 / 搭配 / 缺口 / 录入 四个页面 | ✅ | `web/js/view-*.js` |
| 单品详情页（改属性 / 换图 / 裁剪 / 删除） | ✅ | `web/js/view-item.js` |
| 前端设计系统（Apple 风 + token 化） | ✅ | `docs/ui-contract.md` · `web/css/tokens.css` |
| PWA（可装到手机） | ✅ | `web/sw.js`（v12）· `web/manifest.webmanifest` |
| 粘贴商品文案录入（P5） | ✅ | `services/ingest.py` |
| 视觉打标（P6，上传照片自动认属性） | ✅ | `services/tagging.py` |
| 裤长独立成维度（schema v2） | ✅ | `taxonomy.py` · `schema.py` |
| 纯图录入自动起名（白色棉T恤） | ✅ | `items.auto_name()` |
| **手机相册直读**（后端读 `/sdcard`，绕过 PWA 壳选不了文件） | ✅ 真机验证 | `routes/items.py::/items/album/*` · `web/js/album.js` |
| **详情页换图 / 裁剪** | ✅ 真机验证 | `view-item.js` · `crop.js` |
| **删除单品连图片一起删** | ✅ 真机验证 | `items.delete()` · `images.remove_item_images()` |
| 提交门禁（密钥 + 对比度） | ✅ | `.githooks/pre-commit` |
| `deploy/` 入库（手机部署脚本） | ✅ 已提交 | `deploy/*` |

**测试基线（实测，2026-10-22）**：

```bash
CLAWBOT_WORKER_ENABLED=false .venv/Scripts/python -m pytest -q    # 794 passed / 1 skipped
CLAWBOT_WORKER_ENABLED=false .venv/Scripts/python tests/smoke_test.py  # 83 项 0 失败
.venv/Scripts/python scripts/check_contrast.py                    # 15 组 0 不达标
```

那 1 个 skipped 是 `test_p4_w5_regressions::test_within_rejects_symlink_pointing_outside`：
Windows 上 `os.path.realpath` 对**末段不存在**的路径不解析中间链接，"库外链接"会被
判成"在库内"。**安全缺口还在**（POSIX 上没这问题），建得出 symlink 的机器上它才会真跑。

---

## 二、新机器怎么跑起来

```bash
git clone https://github.com/Zarek-Zhao1112/ai-wardrobe.git
cd ai-wardrobe

python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt

# .env 不在仓库里，必须自己建（cp .env.example .env 后至少填这四行）
#   LLM_BASE_URL=https://api.deepseek.com/v1
#   LLM_API_KEY=<你的 key>
#   LLM_MODEL=deepseek-flash        ← 不要用 deepseek-v4-pro：它只支持 text，接视觉会失败
#   VISION_MODEL=deepseek-flash     ← 不填则打标不可用（其余功能正常）

CLAWBOT_WORKER_ENABLED=false .venv/Scripts/python run.py    # http://127.0.0.1:8787
```

**跑完先跑上面那三条基线**（见「一」末尾）。`CLAWBOT_WORKER_ENABLED=false` 不能省：
不然会去连微信长轮询，测试卡住。

---

## 三、两台机器现在各是什么状态

| | 电脑 | 手机 |
|---|---|---|
| 路径 | `C:/Users/MCXS/工作流打通/ai衣橱/ai-wardrobe` | 小米 12S Ultra，Termux `~/wzbbti` |
| 数据 | `data/wardrobe.db` —— **1 件** | 私有目录 DB —— **0 件** |
| 模型 | `.env`：`LLM_MODEL` / `VISION_MODEL` 都是 `deepseek-flash` | 同上 |
| 访问 | `http://127.0.0.1:8787` | **`http://127.0.0.1:8787`**（PWA 加桌面也用这个） |

两份**数据、配置都不同步**。手机是唯一能测视觉打标和相册的真机。

### 手机端（2026-10-22 从零重装，不是升级）

- 设备 `3b502e83` / `2203121C` / `thor`，Android 15。
  旧机是小米 14 Pro `e59aeb65`，**已弃用**。
- 服务由 `~/wzbbti/start.sh` 起，`~/.bashrc` 里挂了钩子：
  打开 Termux 时 `pgrep -f "python run.py" || sh start.sh`。
- **重启服务**：`adb shell am force-stop com.termux` + `am start -n com.termux/.HomeActivity`，
  十几秒后钩子自己拉起。**不用在手机上敲命令。**
- **服务必须由 Termux app 启动，不能用 `adb run-as`**：run-as 的 SELinux 上下文是
  `runas_app`，读不到 `/sdcard` → 相册接口 `available:false`。
  判据：`cat /proc/<pid>/attr/current` 必须是 `untrusted_app_27`。

---

## 四、下一步做什么（按优先级）

### P0 · 把衣橱喂起来（阻塞一切）

**为什么**：电脑 1 件、手机 0 件。搭配、缺口、组合数全靠数据，空着全是空转。

**怎么做**：手机上有图就能录 —— 录入页「从手机相册选」→ 选照片 → 保存时自动识别。
建议先来：上衣 3–4 件、下装 3 件、鞋 2–3 双。

**验收**：衣橱页出现卡片；搭配页能出第一套；缺口页说出缺什么。

---

### P1 · 修「服务没起时 PWA 静默显示旧版」（未做）

**为什么**：`sw.js` 的 `networkFirst` 在请求失败时回落缓存，**但界面不告诉你**。
2026-10-20 因此白排查了一轮：服务没起来 → 看到几天前的旧界面 → 误以为代码没更新。
（2026-10-22 复核：`grep -n "stale\|离线" web/sw.js web/js/*.js` 仍然为空，**确认没做**。）

**怎么做**：从缓存回落时给页面一个标记，前端显示「⚠️ 当前是离线缓存，服务没连上」。

---

### P2 · 「换图是否覆盖已填属性」—— 产品决策，没拍板

**现状**：`POST /items/{id}/image` 走 `overwrite=False`。AI 认出了新照片的属性，
但**因为库里已有值而全部丢弃**，页面却还显示「已打标（AI）」。

**为什么不能随手改**：库里没有「哪个字段是谁改的」记录，改成全覆盖会**抹掉用户手改的值**。
要么接受"换图等于重来"，要么先加字段来源记录。

---

### P3 · 手机端与电脑端的数据要不要打通（未决策）

**现状**：两份 DB 互不相通。手机录的电脑看不到，反之亦然。

**要决策的不是"怎么做"，而是"要不要做"**：
- 做：需要同步机制（导出/导入、或局域网共享一个库）—— 有冲突风险
- 不做：明确"主力机是哪台"，另一台只当临时查看

**⚠️ 产品决策，动手前必须问用户。**

---

### P4 · `is_archived` 是个空转字段

**现状**：`schema.py` 有 `is_archived` 列，衣橱页有「含已归档」勾选框，
但**没有任何入口能把它设成 1**（`grep -rn is_archived web/js/` 只有读取）。
勾上那个框永远勾不出东西。

**两条路**：① 加「归档 / 取消归档」按钮（配合"回收站"语义）；
② 干脆删掉这条 UI 和列，别留半成品。

---

### P5 · 从商品详情页借鉴的三点（原定计划，未开工）

来源：用户给的识货商品详情页（2026-10-20 调研）。**只借鉴信息层级，不抄内容**。

| # | 借鉴什么 | 换成什么 | 难度 |
|---|---|---|---|
| ① | 参数压成「横排速览」 | 详情页 14 个字段现在全摊开，手机上要滚很久 | 小 |
| ② | 「笔记精选」（真人穿这件） | **「我穿它的记录」** —— 数据已有，界面只显示"穿了 3 次" | 中 |
| ③ | 「精选推荐」（相似商品） | **「这件能配什么」** | 大 |

⚠️ ③ 有硬限制：`POST /outfits/recommend` 只接受**场景/季节**，不接受"以某件单品为中心"。
固定单品属于**硬约束**，不能只靠 MMR 软约束（会静默出错），要在后置校验里卡死。

---

## 五、改代码前必须知道的约定

| 约定 | 要点 |
|---|---|
| **架构口诀** | 本地准备 → 大模型 → 本地校验。**会"静默算错"的一律留在本地** |
| **词表是地基** | `taxonomy.py` 改一个词会影响：品类识别、槽位、组合数、打标、粘贴录入 |
| **槽位决定一切** | 算错 `slot` → 该单品**从所有搭配里静默消失**（不报错） |
| **schema 迁移** | 只能加表/加列/加索引；`SCHEMA_VERSION` +1，在 `MIGRATIONS` 末尾追加 |
| **删除必须连图一起删** | 图片按 `<item_id>.<ext>` 命名、一件一份。删记录不删文件 = 空间只增不减（2026-10-22 修的就是这个）。新增任何删除路径都必须经过 `items.delete()` |
| **图片 URL 一律带版本号** | `api.imageUrl(path, item.updated_at)`。文件名固定，换图后 URL 不变，不加版本会拿到旧图 —— 裁剪会拿旧位图把新图裁坏 |
| **相册浮层只有一处** | `web/js/album.js`（`openAlbumPicker`），录入页和详情页共用，别复制第二份 |
| **提交门禁** | `git config core.hooksPath .githooks`（每台机器一次） |
| **不要 `git add -A`** | 显式列路径。`data/`、`.env`、`.workbuddy/`、内部文档都不能进仓库 |

---

## 六、换机器 / 上手机也可能踩的坑

1. **pip 装不进 venv**：有些机器配了全局 `pip.target`，用 `python -m pip --isolated install ...`。
2. **`git push` 报 `could not read Username`**：用一次性 token URL 推，
   推完**确认 `.git/config` 里没有残留 token**。
3. **`git push` 报 502**：网络层瞬时问题，重试一次通常就过。
4. **改了前端却看不到变化**：浏览器 **ES module 缓存**。给 hash 路由加 `?nocache=`
   是**错的**（`?` 在 `#` 后属于 fragment，会让路由匹配不上）。
5. **手机端没有 `.git`**：目录是传文件部署的。用 `deploy/sync-termux.sh` 打包覆盖，
   或单个文件 `adb push` + `run-as cp`。**验证方式永远是问服务要一份前端文件**，
   不是看文件时间。
6. **Termux 上装 pydantic 要现场编译 Rust**（Termux python 是 3.14，PyPI 没有 Android wheel）：
   `pkg install -y rust binutils`，然后 **cargo 必须配国内镜像**
   （`~/.cargo/config.toml` 写中科大 sparse 索引），否则 crates.io 一个包都下不来。
   编译约 15 分钟，产物缓存在 `~/.cache/pip/wheels/`。
7. **小米 MIUI 把 `input tap/swipe/text/keyevent` 全禁了**（缺 `INJECT_EVENTS`），
   没法在手机上代替用户敲命令。**debug 版 Termux 的 `run-as` 可用**，能绕开：
   `adb shell "run-as com.termux /data/data/com.termux/files/usr/bin/bash -c ..."`。
   但它读不了 `/sdcard`、写不了 `/data/local/tmp`（中转文件只能电脑 push 进去，日志写 `$HOME`）。
8. **`adb install` 被 MIUI 拦**：`INSTALL_FAILED_USER_RESTRICTED`。把 APK 推到
   `/sdcard/Download` 让用户手动点安装；`-g`（自动授权）在这台机上直接 `SecurityException`。
9. **`.env` 里的 key 不要落在 `/sdcard`**：中转文件用完立刻删（`/sdcard` 任何 app 都能读）。
