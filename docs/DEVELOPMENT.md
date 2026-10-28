# 开发与接续

先读根目录AGENTS.md，再读本页。当前产品规则见design/，历史讨论见history/，协作流程见AGENT-COLLAB.md。

## 本机启动

桌面后端使用Python3.13；前端是原生ES模块，无npm构建步骤。Android包含Python3.14，两个解释器用途不同。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip --isolated install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
.venv/Scripts/python.exe run.py
```

浏览器访问http://127.0.0.1:8787/。开发测试服务使用其他端口，例如APP_PORT=8799；不占用正在使用的手机/电脑服务。Windows也可双击start.bat。

## 代码入口

| 工作 | 入口 |
|---|---|
| 页面路由、共享上下文 | web/js/core/app.js |
| HTTP接口与错误处理 | web/js/core/api.js |
| 衣橱与详情 | web/js/features/wardrobe/ |
| 录入、IndexedDB草稿、识别与生图队列 | web/js/features/import/ |
| 搭配、必选约束与试衣入口 | web/js/features/outfits/ |
| 添置方向、实物搜图状态及详情 | web/js/features/purchase/ |
| 共用展示/手势/页面切换 | web/js/components/ |
| 色板、基础布局 | web/css/base/ |
| 页面样式 | web/css/features/ |
| 后端路由 | app/routes/ |
| 衣物、识别、推荐等业务 | app/services/ |
| AI供应商与个人电脑中转客户端 | app/services/llm/ |
| 电脑CLI中转服务 | scripts/bridge/ |
| 构建及验收 | android/、scripts/checks/ |

录入view.js负责表单和生命周期；markup.js负责静态布局，clipboard.js绑定粘贴/拖放，text-input.js负责文案解析与确认；photo-import.js负责逐件确认，queue.js负责自动准备；store.js保留已有IndexedDB名称和草稿格式。改目录不能修改存储标识或丢弃旧草稿。

后端继续使用routes/services分层。必选衣物校验独立为outfit_constraints.py，recommend.py在模型前及持有写锁的保存事务内复用；不得改成仅依赖提示词。

## 常用检查

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q
.venv/Scripts/python.exe -X utf8 tests/smoke_test.py
.venv/Scripts/python.exe -X utf8 scripts/checks/check_contrast.py
.venv/Scripts/python.exe -X utf8 scripts/checks/web_assets.py
```

新增或移动前端资源后：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/checks/web_assets.py --write
```

工具检查静态模块及HTML资源引用，生成sw.js的单份资源清单；激活检查复用该清单。调整缓存行为或发布离线资源时更新SW版本。开发机可联网打开不代表离线模块齐全。

UI门禁需一个全新Chrome调试profile、CDP9333及8799临时服务：

```powershell
node scripts/checks/cdp_ui_check.js
```

覆盖390/768/1440宽度、44px触控、底栏遮挡、控制台、manifest与SW。新增交互按实际风险补浏览器验收；不在用户真实衣橱中做删除、喜欢或付费模型测试。临时库必须使用系统临时目录。

## 电脑AI中转

本机配置与运行状态放在忽略的.local/中，不存进效果图目录：

```text
.local/
  bridge.json
  toolchain.env
  runtime/desktop-bridge/    配对码、任务记录和日志
  tools/workbuddy-cli/      官方CLI本机依赖
  archive/                 本机历史材料
```

将scripts/bridge/config.example.json复制为.local/bridge.json，填写现有官方CLI入口；账号认证仍由官方CLI管理，应用不保存账号密码。已有登录环境迁移目录时应沿用账号与pairing.json，不能自动重新创建配对码。

```powershell
.venv/Scripts/python.exe -X utf8 scripts/bridge/manage.py status
.venv/Scripts/python.exe -X utf8 scripts/bridge/manage.py start
.venv/Scripts/python.exe -X utf8 scripts/bridge/manage.py stop
```

stop核对进程脚本、状态目录、任务记录和CLI子进程，拒绝停止身份不符或仍在工作的服务。非Windows通过原终端或系统服务管理器停止。手机继续使用设置里原地址/端口/配对码；更换代码目录不应触发重新登录或修改模型。

当前识别/排序/搜图核对模型固定deepseek-v4.1-flash，平铺编辑模型hunyuan-image-alpha-edit。实时试衣已有摄像头入口，生成式试衣链路需另行接入，不能将摄像头预览表述为已实现的实时AI换装。

## Android构建

复制android/toolchain.env.example为.local/toolchain.env并配置本机工具路径，详见android/README.md。构建目录保持独立且使用ASCII短路径。

```powershell
& 'C:/Program Files/Git/bin/bash.exe' android/build_apk.sh
```

sync_sources.py按清单同步app/web并删除目标中过期副本，排除宿主pyc；构建完成必须解包比对前端与相关后端，核对实际安装包指纹。覆盖安装使用adb install -r加电脑本地APK路径。

用Python3.14运行android/tools/verify_apk.py加APK路径，可一次核对全部前端文件与后端源码/字节码。

## Git与发布

```powershell
git config core.hooksPath .githooks
git status --short
```

先保存功能基线，再按清理、重构、文档分批提交。钩子检查资源图、密钥和对比度，Windows输出固定UTF-8。推送前确认新增文件也经过扫描；只检查已跟踪旧文件不足以覆盖新模块。

发布源代码、测试、必要Android离线wheel和通用配置；不发布.env、data/、.local/、账号凭据、真实衣物图片、APK或本机SDK/JDK。测试参考图片位于tests/fixtures/reference/，不作为应用内推荐图库。
