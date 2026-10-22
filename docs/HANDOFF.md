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
| **手机相册直读**（后端读 `/sdcard`） | ⏸ 代码与测试在，2026-10-22 起无 UI 入口 | `routes/items.py::/items/album/*` · `web/js/album.js` |
| **详情页换图 / 裁剪** | ✅ 真机验证 | `view-item.js` · `crop.js` |
| **删除单品连图片一起删** | ✅ 真机验证 | `items.delete()` · `images.remove_item_images()` |
| **原生 APK（Chaquopy 打包，脱离 Termux）** | ✅ 装机跑通，`<input type=file>` 待手动点验 | 见「三、手机端」 |
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
| 跑法 | `.venv/Scripts/python.exe run.py` | **原生 APK** `com.zarek.wardrobe`（Chaquopy 内嵌 Python 3.14） |
| 路径 | `C:/Users/MCXS/工作流打通/ai衣橱/ai-wardrobe` | 小米 12S Ultra，APK 装出来的 |
| 数据 | `data/wardrobe.db` —— **1 件** | `/data/user/0/com.zarek.wardrobe/files/wardrobe.db` —— **0 件** |
| 模型 | `.env`：`LLM_MODEL` / `VISION_MODEL` 都是 `deepseek-flash` | 同配置已打进 APK（`/health` 报 `llm_ready:true` / `vision_ready:true`） |
| 访问 | `http://127.0.0.1:8787` | APK 里开屏就是 WebView → `http://127.0.0.1:8787` |

两份**数据、配置都不同步**。手机是唯一能测视觉打标和相册的真机。

### 手机端（2026-10-22 换成原生 APK，Termux 版已弃用）

- 设备 `3b502e83` / `2203121C` / `thor`，Android 15（HyperOS）。
  旧机是小米 14 Pro `e59aeb65`，**已弃用**。
- **APK 源码在仓库里 `android/`**（2026-10-22 收进来的：Java 壳、Gradle 配置、
  离线 wheel、构建脚本都在，`android/README.md` 是完整说明）。
  实际构建仍跑在 `C:/wbuild/aiw`（**故意用纯 ASCII 短路径** —— Gradle + Chaquopy
  在中文长路径下会踩各种转义问题），那个目录**是产物区，删了能重建**。
- 构建：`sh android/build_apk.sh`（老习惯 `sh _build/build_apk.sh` 是一行转发，仍然可用）。
  脚本会从仓库铺骨架 + 同步 `web/` `app/` `.env` + 逐字节校验 + 特征串断言，任何一步不过就停。
- 构建产物：`C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk`（约 19 MB），
  归档到 `_build/dist/ai-wardrobe-debug.apk`。
- ⚠️ **APK 莫名其妙变大但条目没变**：AGP 增量打包的僵尸数据（实测 `.env` 变 157 字节
  → 包从 19.0 MB 涨到 24.1 MB）。判据 `python android/tools/apk_diff.py 旧.apk 新.apk`，
  解法 `sh android/build_apk.sh clean`。
- **装机**：`adb install -r "C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk"` 一步到位。
  ⚠️ Windows 版 adb 的 `install` 参数必须是**本地路径**。写成 `/sdcard/Download/x.apk`
  会报 `failed to stat /sdcard/...` —— 它当成本地文件去找，不是手机上的路径
  （`push` 的目标才是手机路径）。2026-10-22 踩过。
  覆盖安装（同签名）MIUI **不拦**；全新安装会 `INSTALL_FAILED_USER_RESTRICTED`，只能手动点。
- **服务由 `WardrobeService` 前台服务起**（`WardrobeService.java::bootPython`），
  带常驻通知。`onStartCommand` 返回 `START_STICKY`，被系统杀了会重启。
- 数据目录由 `WARDROBE_DATA_DIR` 覆盖成 app 私有目录（`getFilesDir()`），
  APK 里的 Python 源码是只读的，不能往源码根下写 `data/`。
- ★ **`<input type=file>` 必须靠 `WebChromeClient.onShowFileChooser`**。
  默认 `WebChromeClient` 的实现在这里 `return false`（= 我不处理），WebView 就
  **什么也不弹、也不报错**。2026-10-22 之前就是这个坑，表现是"点选择文件没反应"。
  另外详情页选完文件**必须自己渲染缩略图** —— 原生 input 选完不显示文件名，
  页面上一个字都不变，用户照样以为坏了。
- 旧的 Termux 版（`~/wzbbti`，`~/.bashrc` 钩子自启）**还装着但已不用**。
  它和 APK 抢同一个 8787 端口，**同一时刻只能有一个在跑**。
  若某天 APK 起不来，先确认 Termux 没占着端口。

---

## 四、下一步做什么（按优先级）

### ✅ 2026-10-22 下午 ·「后端没响应」假警报（已修，已装机验证）

**现象**：手机上打开 app，页面写着「后端没响应 / Termux 里的服务可能被系统挂起了 /
回到桌面打开一次 Termux」。而同一时刻 `/health` 秒回、视觉打标 1.5 秒出结果、
`/items/album/photos` 3000 张可读 —— **后端完全正常**。

**三层根因，一层比一层隐蔽**：

| 层 | 根因 | 证据 |
|---|---|---|
| ① 判据错 | `index.html` 的内联看门狗 7 秒后只看「`#view` 里还有没有『加载中』」，有就断言后端挂了。而 `app.js` 的 `boot()` 是 `health(6s 超时) → taxonomy(10s 超时)` **串行**，最坏 16 秒才有内容 —— 7 秒时本来就还在加载 | 读代码 + 冷启动实测 |
| ② 会误伤正常页 | 「加载中」这三个字**合法地**出现在正常页面里：衣橱页点「加载更多」时按钮文字就是它。旧判据会把整页盖掉，数据回来后写进**已被替换的节点** → 永久停在面板上 | 只读审查发现（P1） |
| ③ HTTP 缓存 | 覆盖安装后手机上**长期跑旧 js**：`networkFirst` 的 `fetch()` 走浏览器 HTTP 磁盘缓存，普通刷新、甚至 SW 的 network-first 都拿副本 | 实测：清缓存前页面里 `window.__wz` 不存在，而 `fetch('/js/app.js')` 返回新版；`Network.clearBrowserCache` 后立刻正常 |

**修法**：
- `app.js` 汇报 `window.__wz.boot`（`booting` / `ok` / `down`），**不存在本身就是答案**（模块没跑）。
  看门狗 9 秒先看状态：`ok/down` 一个字不动；`booting` 再给 8 秒；`missing` 立刻探 `/health` 决定说
  「页面脚本没加载起来」还是「后端没响应」。脚本没起来时多一个「清缓存重载」按钮。
- `sw.js` 所有 `fetch` 加 `cache: "no-store"`，VERSION v13→**v14**（缓存策略变了必须 +1）。
- 删掉 `renderBackendDown()` 的「试着打开 Termux」按钮：Termux 版已弃用，而它在 APK 里点下去
  会真去唤起 com.termux，`.bashrc` 钩子随即 **再起一个服务抢 8787**。文案也从"划掉任务重开"
  改成"系统设置里强制停止"—— 前台服务没有 `stopWithTask`，划掉只关页面，服务照跑。
- 壳补 `onConsoleMessage` → `logcat -s Wardrobe` 现在能看到 JS 的 WARNING/ERROR。
- `_build/build_apk.sh` 加同步 + 断言（**P0**：原来只跑 gradle，前端靠手动 `cp -r`，漏一次就打出旧包）。

**验证（都是实测，不是推断）**：
- 五场景 CDP 测试：正常不误报 / 模块没起来说脚本 / 真挂才说挂 / 脚本在跑只是慢 / **正常页里有"加载中"不被覆盖** —— 5/5
- `cdp_ui_check.js` 新增 16 秒看门狗复检（原来每页只等 1.2–1.8 秒，这段代码零覆盖）
- 手机冷启动后 `window.__wz.boot = "ok"`，缓存桶 `wzbbti-shell-v14`，**全程没手动清缓存**
- `pytest`：794 passed / 1 skipped

**仍未做**：从 SW 缓存回落时界面**不告诉你**（见下面 P1），这次只是让回落拿到的是新文件。

### P0-a · 手动点验 file chooser（1 分钟，只能人来点）

APK 已装机、服务 `healthy`、相册 3000 张可读、前端确认是新版。
**唯一没被人工点过的是 `<input type=file>`** —— 这台 MIUI 把 `input tap` 禁了
（缺 `INJECT_EVENTS`），adb 代劳不了。

验收三步：
1. 录入页点「选一张照片」→ 应弹出系统选择器（**不弹就是壳还没生效**，看 `adb logcat -s Wardrobe`）
2. 随便选一张 → **页面上应立刻出现缩略图**（不出现就是前端 `item-picked` 没生效）
3. 点「保存」→ 结果区应报"照片已自动识别：AI 帮你补了 N 项"

同一套验证详情页：点「换一张照片」→ 选一张 → 应出现缩略图 → 点「上传 / 替换」。

### P0 · 把衣橱喂起来（阻塞一切）

**为什么**：电脑 1 件、手机 0 件。搭配、缺口、组合数全靠数据，空着全是空转。

**怎么做**：手机上有图就能录 —— 录入页「选一张照片」→ 选照片 → 保存时自动识别。
建议先来：上衣 3–4 件、下装 3 件、鞋 2–3 双。

**验收**：衣橱页出现卡片；搭配页能出第一套；缺口页说出缺什么。

---

### P1 · 修「服务没起时 PWA 静默显示旧版」（**部分已修**）

**为什么**：`sw.js` 的 `networkFirst` 在请求失败时回落缓存，**但界面不告诉你**。
2026-10-20 因此白排查了一轮：服务没起来 → 看到几天前的旧界面 → 误以为代码没更新。

**2026-10-22 进展**：
- ✅ 更严重的那一半修了 —— `fetch` 加 `cache: "no-store"` 之后，**回落也拿不到 HTTP 缓存里的旧文件**，
  「改了前端但手机跑旧 js」这条不再发生（见上面那节）。
- ❌ 仍缺：真的回落到 SW 缓存时（后端确实不可用），界面**没有任何标记**。
  `grep -n "stale\|离线" web/sw.js web/js/*.js` 仍然为空。

**怎么做**：从缓存回落时给响应加一个头（例如 `X-From-Cache: 1`），`api.js` 看到就
在状态栏打一个「⚠️ 离线缓存」。注意导航请求回落的是 HTML，加头要改 `sw.js` 里 `matchShellAnywhere` 的返回。

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
| **相册浮层只留一份** | `web/js/album.js`（`openAlbumPicker`）。2026-10-22 起两页都不调它了（照片入口收口成原生 `<input type=file>`，`.sr-only` 藏 input + 按钮 `.click()`），但它**不能复制第二份** —— 要恢复只能按 `view-add.js` 顶部注释里的两步改回去 |
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
10. **WebView 里 `<input type=file>` 点了没反应**：一定是没实现
    `WebChromeClient.onShowFileChooser`。默认实现 `return false`，WebView 问了一圈没人接，
    **不弹窗、不报错、连 `change` 都不来** —— 光看日志查不出来，必须看代码。
    修的时候还有两个配套的坑：① 用户取消**必须** `onReceiveValue(null)` 结账，
    否则回调泄漏、第二次点彻底哑；② 选完**必须自己渲染缩略图**，原生 input 选完
    不显示文件名，页面毫无变化，用户会当成还是坏的。
11. **跑 Gradle 前必须 `unset PYTHONPATH PYTHONHOME`**：Chaquopy 用 `buildPython`
    指定的 3.14 解释器，环境变量一挤进去它自己的 `Lib` 就找不到，报 `Fatal Python error`。
    `_build/build_apk.sh` 里已经处理了。
12. **APK 工程不要放中文长路径**：`C:/wbuild/aiw` 是故意的。Gradle + Chaquopy
    在 `C:\Users\...\工作流打通\...` 这种路径下会踩转义/编码问题。
13. **`adb install` 全新装被 MIUI 拦**（`INSTALL_FAILED_USER_RESTRICTED`），
    但**覆盖安装（同签名、已装过）能直接成功**。全新装只能 `adb push` 到
    `/sdcard/Download/` 让用户手动点。
