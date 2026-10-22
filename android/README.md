# android/ —— APK 壳

把整个 `ai-wardrobe`（FastAPI + 前端）打成 Android APK 的全部源码。
**这一层是仓库外的历史遗留，2026-10-22 收进仓库的**：之前它散在
`C:/wbuild/aiw`（Gradle 工程）和 `_build/build_apk.sh`，两个地方都不在 git 里，
换台机器就重建不出来。

## 一条命令构建

```sh
sh android/build_apk.sh
```

脚本干四件事，任何一步不通过就**停下报错**，不会打出"看起来成功"的包：

1. 从 `android/` 把 Gradle 骨架铺到构建目录 `C:/wbuild/aiw`
2. 把仓库的 `web/` 和 `app/` 覆盖同步进 `app/src/main/python/`（APK 里是**拷贝**，不是链接）
3. 逐字节校验快照 + 特征串（`window.__wz` / `wd-hard` / `onConsoleMessage` / `onShowFileChooser`）
4. 跑 `gradle assembleDebug`

产物：`C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk`（约 19 MB）

装机：

```sh
adb install -r "C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk"
```

同签名**覆盖安装**能直接 Success，MIUI 不拦；只有全新装才 `INSTALL_FAILED_USER_RESTRICTED`，
那种情况才需要手点 APK。

## 为什么不在仓库目录里直接跑 Gradle

仓库路径含中文（`C:/Users/MCXS/工作流打通/ai衣橱/ai-wardrobe`）。
Gradle 和 Chaquopy 在 Windows 上处理中文路径会出转义问题，所以统一铺到纯 ASCII
短路径 `C:/wbuild/aiw` 去构建。**构建目录是产物区，不是源码**，删掉也能重建。

想换构建目录：`AIW_BUILD_DIR=D:/aiw sh android/build_apk.sh`（同样得是纯 ASCII 短路径）。

## 编译产物 vs 源码

| 进仓库 | 不进仓库 | 为什么 |
|---|---|---|
| 3 个 `.java`、`server.py`、`AndroidManifest.xml`、`strings.xml` | `app/src/main/python/{app,web}` | 由脚本从仓库根部同步过来，入库就是两份会漂移的副本 |
| 4 个 `.gradle` / `gradle.properties` | `local.properties` | 含本机 SDK 绝对路径，脚本现生成 |
| `wheels/*.whl`（3.5 MB，20 个） | `.gradle/`、`build/`、`app/build/` | Gradle 缓存与产物 |
| | `.env` | 含 API key；脚本从仓库根复制过去 |

### ⚠️ `.env` 会被打进 APK

`build_apk.sh` 会把仓库根的 `.env`（含 `LLM_API_KEY`）复制进包。
自己的手机自己用没问题；**要给别人装，先想清楚这件事** —— APK 是可解包的。

## 目录

```
android/
├── build_apk.sh              ← 唯一入口
├── settings.gradle           ← 仓库镜像（本地 maven 那行是本机路径，见下）
├── build.gradle              ← AGP 8.7.3 + Chaquopy 17.1.0
├── gradle.properties         ← 本机 buildPython 路径
├── app/build.gradle          ← Chaquopy 配置：Python 3.14 + 离线 wheel
├── app/src/main/
│   ├── AndroidManifest.xml
│   ├── java/com/zarek/wardrobe/
│   │   ├── WardrobeApp.java      10 行，Application 子类，唯一作用是让 Chaquopy 起来
│   │   ├── WardrobeService.java  前台服务，在子线程跑 uvicorn（START_STICKY 保活）
│   │   └── MainActivity.java     367 行，WebView 套壳 + 文件选择器回调
│   ├── python/server.py      APK 内 Python 入口（Java 侧 callAttr("start", filesDir, 8787)）
│   └── res/values/strings.xml
├── wheels/                   20 个离线 wheel，见「wheels 从哪来」
└── tools/                    构建期/真机调试脚本，不参与打包
```

## 运行时结构

```
MainActivity ──WebView──> http://127.0.0.1:8787
     │                          ▲
     │ startForegroundService   │ uvicorn（子线程）
     ▼                          │
WardrobeService ─────────────────┘
     └─ py.getModule("server").callAttr("start", filesDir, 8787)
```

### ★ server.py 里有一句顺序是硬约束

`WARDROBE_DATA_DIR` 必须在 `import app.*` **之前**设好 —— `config.py` 在 import 期就把
数据目录算出来并缓存了，先 import 再设环境变量完全无效。这也是它不复用 `run.py` 的原因：
`run.py` 默认把数据写到源码根下的 `data/`，而 APK 里源码在 assets 解压出来的只读目录。

## MainActivity 里踩过的两个坑（都写进注释了）

**1. 文件选择器**：`WebChromeClient.onShowFileChooser()` 的默认实现 `return false`，
于是 `<input type=file>` 点了**没有任何反应**（不弹选择器、不报错、`change` 事件也不来）。
必须自己实现，而且三种情况都要把 `ValueCallback` **结清一次且仅一次**：

| 情况 | 处理 |
|---|---|
| 选中 | `onReceiveValue(uri[])` |
| 用户取消 | `onReceiveValue(null)` —— 漏了会 callback 泄漏，第二次点彻底哑 |
| 拉起失败 | **自己结账 + `return true`**，绝不能 `return false`（会双结账同一个 callback） |

**2. Activity 重建**：`configChanges` 必须写全
（`orientation|screenSize|smallestScreenSize|screenLayout|keyboardHidden|uiMode`）。
少写一个，进退分屏 / 改字体大小 / 切深色模式都会重建 Activity → WebView 跟着重来
→ 表单清空、正在等的文件选择器回调直接作废。
配合 `launchMode="singleTask"`，常驻通知点一下才能"回到原页面"而不是新建一个。

## 工具链（不进仓库，约 700 MB）

| 组件 | 版本 | 路径 | 缺了会怎样 |
|---|---|---|---|
| JDK | Temurin 17.0.20.1 | `_build/jdk` | `JAVA_HOME` 无效，Gradle 直接不启动 |
| Android SDK | platform 35 + build-tools 34/35 | `_build/android-sdk`，另有 ASCII 符号链接 `C:/wbuild/sdk` | `SDK location not found` |
| Gradle | 8.x（`_build/gradle/bin/gradle.bat`） | `_build/gradle` | 无 |
| build Python | 3.14.8 | `C:/wbuild/py314` | Chaquopy 报 "buildPython not found" |
| local-maven | Chaquopy python target 3.14.8-0（arm64-v8a + stdlib zip + pom） | `C:/wbuild/local-maven` | 报 `Could not find .../gradle-plugin/...`，见 `settings.gradle` 注释 |

`_build/` 与 `C:/wbuild/` 都在仓库外，是**本机手工装的一次性环境**。
`build_apk.sh` 启动时逐个检查存在性，缺哪个就直说是哪个。

### 换机器要改的两处

1. `gradle.properties` → `aiw.buildPython`
2. `settings.gradle` → 第一行 `maven { url 'C:/wbuild/local-maven' }`

（`local-maven` 那行没法参数化：`pluginManagement` 必须是 settings 文件的第一个块，
块前不能定义变量。校验时 Gradle 只认 pluginManagement 的第一个仓库，
所以那份 zip 不能挪到 mavenCentral 之类的地方。）

## wheels 从哪来

ChaQuopy 17 起 pip 强制 `--only-binary`，装不了 sdist；而它的预编译仓库没有 pydantic。
PyPI 上的 aarch64 wheel 是 glibc 的，Android 用 bionic，装不上。所以：

| wheel | 来源 |
|---|---|
| 19 个纯 Python 包 | PyPI 官方 wheel，版本锁死（`tools/grab_wheels.py` 能重抓） |
| `pydantic_core-2.46.4-cp314-cp314-android_24_arm64_v8a.whl` | **自制**：Termux 里用 rust 编出的 `.so` + 手搓 `dist-info`，见 `tools/build_pc_wheel.py` |

重抓纯 Python 包：

```sh
python android/tools/grab_wheels.py android/wheels
```

自制 pydantic_core（需要先在手机上编出 `.so`）：

```sh
python android/tools/build_pc_wheel.py <含 .so 的源目录> android/wheels
# 会顺便检查 ELF PT_LOAD 对齐 —— Android 15 的 16 KB 页要求 0x4000，
# 4 KB 对齐的段在 16 KB 页设备上 dlopen 直接崩
```

## tools/

| 脚本 | 用途 |
|---|---|
| `grab_wheels.py` | 从清华镜像抓指定版本的 pure-python wheel |
| `build_pc_wheel.py` | 把手机上的 `pydantic_core` `.so` 重新打成本地可装的 Android wheel |
| `unpack.py` | 解包 APK，核对里面到底装了什么（"源码改了但装上去还是旧界面"的判据） |
| `verify_watchdog.js` | CDP 五场景验证前端看门狗（正常 / 模块没起来 / 真挂 / 只慢 / 正常页含"加载中"） |
| `check_sw.js` | 检查 Service Worker 版本与缓存桶 |
| `diag_webview.js` | WebView 侧诊断（UA、状态协议、资源加载） |
| `probe_phone.js` | 真机接口探测 |
| `reload_phone.js` | 真机强制重载 / 清缓存 |

⚠️ MIUI 禁了 `input tap/swipe/text/keyevent`（无 `INJECT_EVENTS`），
**APK 里的按钮只能用户手点**，adb 代劳不了。看日志用 `adb logcat`：

```sh
adb logcat -s Wardrobe python chromium
```
