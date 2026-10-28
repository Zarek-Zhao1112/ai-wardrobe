# 把后端装到手机上（Termux）

让**手机自己当服务器**，电脑不用一直开着。装完是「Termux 里跑后端 + 浏览器加到主屏幕」。

> 为什么不把后端留在电脑上：那样出门/关机就用不了。装到手机上，数据和服务在同一台设备里。

## 一、装（一次性）

1. 装 **Termux**
   ⚠️ 必须从 **F-Droid** 或 [GitHub Releases](https://github.com/termux/termux-app/releases) 下，
   **Google Play 上的版本已停止维护**（会出各种怪问题）。
2. 打开 Termux，粘这几行：

```sh
pkg install -y git
git clone --depth 1 https://github.com/Zarek-Zhao1112/ai-wardrobe.git ~/wzbbti
sh ~/wzbbti/deploy/legacy-termux/termux-setup.sh
```

脚本会自己处理几个安卓特有的坑（下面「四、怎么回事」一节讲了为什么）：

- 自动挑一个**能用的 PyPI 镜像**（国内镜像不一定都能用，实测清华的当前 pip 解析不了）
- 把 `pydantic-core` 的**安卓预编译包**取回来装（PyPI 上的 aarch64 包是 glibc 的，装不上）
- 绕开 `requirements.txt` 里对 pydantic 的**死锁**（锁的版本在安卓上没有对应 wheel）

脚本**可以反复跑**：中断了、或者过一阵要更新，再跑一次就行。

> 手机上 `git clone` 慢的话，也可以在电脑上克隆好，
> 把整个目录传进手机（**注意别把 `.env` 和 `data/` 一起传出去**）。

## 二、跑起来

如果上一步的服务已经托管起来了（脚本会告诉你），直接跳到浏览器：

```sh
cd ~/wzbbti && python run.py     # 没托管的话，手动跑（前台运行，Ctrl-C 停）
```

然后手机浏览器打开 <http://127.0.0.1:8787>，菜单里选「**添加到主屏幕**」。

## 三、保活 —— 先说结论

**安卓会杀后台，而且杀得很彻底。** 实测（2026-10，某 MIUI 机器）：

| 手段 | 实测结果 |
|---|---|
| `termux-wake-lock` | ✅ 有用，防止 CPU 深睡 |
| 电池优化白名单 | ✅ 有用，但**不能保证不被杀** |
| `termux-services` 托管 | ✅ 崩溃/被杀后**重开 Termux 会自动拉起**（实测过） |
| **Termux:Boot 开机自启** | ❌ **没成**。手机重启后 Termux 根本没被拉起（白名单、服务定义、boot 脚本都在） |

所以推荐**「按需启动」**，不折腾开机自启：

> **手机重启后，第一次用之前先点一下 Termux 图标**（等 3~5 秒服务自己起来），再开主屏幕上的图标。

这个模式的好处：不需要「自启动」权限（这类系统默认全关着，且不一定给），
系统杀进程也无所谓 —— 点一下它就回来。

如果你确实想要常驻，前三条照做：

```sh
# 1) 唤醒锁（每次开机后执行一次）
termux-wake-lock

# 2) 电池白名单（设置 → 应用 → Termux → 电池 → 不受限制；并允许发通知）

# 3) termux-services 托管 —— ★ 装完必须把 Termux 完全关掉再打开！
pkg install -y termux-services
# （服务定义由 termux-setup.sh 生成；装完关掉 Termux 再重跑一次那个脚本即可）
```

## 四、怎么回事（几个反直觉的点）

**为什么 `pydantic-core` 要特殊处理？** 它是 Rust 扩展。PyPI 上的 aarch64 wheel 是
manylinux(**glibc**)，Termux 是 **bionic libc** → 装不上。社区有预编译（TUR），
但**包本体托管在 GitHub Releases，国内手机上直连拉不动**（实测 90 秒没下完 2MB）。
脚本用 `deploy/legacy-termux/fetch_android_wheel.py` 走 GitHub 代理取回来再装。

**为什么 `requirements.txt` 里锁的版本在手机上装不上？** 它锁 `pydantic==2.13.4`，
要 `pydantic-core==2.46.4`，而 TUR 上只有 2.50.1。脚本的做法是：把 pydantic 的锁去掉，
用约束文件钉住已装好的 pydantic-core，让 pip 自己反解出匹配的 pydantic。

**为什么镜像还要探测？** 国内 PyPI 镜像**不一定都能用**。实测（pip 26.x）：

| 镜像 | pip 能不能用 |
|---|---|
| 阿里云 / 腾讯云 / 官方 | ✅ |
| **清华** | ❌ 索引页正常返回 200，pip 却报 `Could not find a version … (from versions: none)` |

所以脚本先探再选，而不是写死一个。

**为什么加了主屏幕还是带地址栏？** 因为「添加到主屏幕」不一定真的装成 App（WebAPK）。
实测某机器上根本没生成 WebAPK 包 → 图标只是**普通快捷方式**，
点开仍是带地址栏的标签页。要真「独立图标 + 全屏」得走 TWA/Capacitor 打包。

**为什么只有 `127.0.0.1` 能装成 App？** 浏览器要求 **Service Worker 必须在安全上下文**里
（`https` 或 `localhost`/`127.0.0.1`）。用局域网 IP（`http://192.168.x.x:8787`）打开**装不了**
—— 这是浏览器的规矩，不是 bug。局域网 IP 只适合「电脑访问手机上的服务」。

## 五、常见问题

| 现象 | 原因 / 解法 |
|---|---|
| 手机上打不开页面 | 多半是**服务没在跑** —— 先点一下 Termux 图标等服务起来（页面此时会显示引导页，点「重试」） |
| 电脑上打不开 `192.168.x.x:8787` | 手机可能在**息屏省电**（WiFi 被挂起）。让屏幕亮着 / 插着电再试 |
| `pip install pydantic-core` 编译失败 | 脚本会先试预编译包；实在不行才现场编译 Rust，内存小容易 OOM → 可改用 `proot-distro` 装 Ubuntu |
| `pkg upgrade` 之后服务起不来 | 大版本升级（Python 3.x→3.y）会让已装的包失效 → 重跑 `deploy/legacy-termux/termux-setup.sh` |
| 想换数据目录 | 设 `WARDROBE_DATA_DIR`（见根目录 `README.md` 的环境变量表） |

## 六、数据

数据在 `~/wzbbti/data/`（SQLite 文件 + 照片）。**换手机或重装前把整个 `data/` 拷走即可。**
