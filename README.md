# AI 衣橱

个人衣橱与审美搭配应用。手机APK自带本地服务和SQLite数据库，网页使用原生ES模块；AI通过你配置的接口或电脑个人CLI中转工作。

## 当前功能

- 一次多选照片，自动识别属性并生成白底平铺图，逐件确认入库。
- 衣橱筛选、只读详情、红色喜欢、确认删除和玻璃展开动效。
- 选择完整审美方法，从已有衣橱组套；可指定一件衣物为每套必选。
- AI提出添置方向，并寻找来源明确的实物商品参考图；缺图时可重试。
- 搭配横滑、历史与反馈，摄像头试衣入口；生成式实时换装尚需另外接入。
- 保留后端的文字导入、微信通道、偏好记忆和历史穿着API；当前详情不开放编辑/记录穿着。

## 桌面启动

需要Python3.13。前端不需要npm安装或打包。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip --isolated install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
.venv/Scripts/python.exe run.py
```

打开http://127.0.0.1:8787/，Windows可使用start.bat。未配模型时可查看衣橱并使用本地搭配排序；照片识别、平铺与搜图需要对应服务。

## 继续开发

先读[开发文档](docs/DEVELOPMENT.md)与[AGENTS.md](AGENTS.md)。

| 目录 | 用途 |
|---|---|
| app/routes、app/services | Python API与业务逻辑 |
| web/js/core | 路由、API、字段与通用UI |
| web/js/components | 共用手势、展示与动效 |
| web/js/features | 衣橱、录入、搭配、添置 |
| web/css/base、features | 基础色板/布局及功能样式 |
| scripts/bridge | 电脑AI中转与统一管理入口 |
| scripts/checks | 资源、对比度、密钥与浏览器门禁 |
| android | APK源码、构建与精确同步 |
| tests | 临时库/假模型测试，含前端内存测试与参考图片夹具 |
| docs/design、history | 现行产品设计与历史记录 |

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q
.venv/Scripts/python.exe -X utf8 tests/smoke_test.py
.venv/Scripts/python.exe -X utf8 scripts/checks/web_assets.py
.venv/Scripts/python.exe -X utf8 scripts/checks/check_contrast.py
git config core.hooksPath .githooks
```

移动/新增前端文件后运行web_assets.py --write更新离线清单。原生JS模块、HTML与缓存引用都会检查；不依赖开发者手工维护两份名单。

## 手机与AI中转

Android构建需要Python3.14、JDK、SDK和Gradle，见[Android说明](android/README.md)。电脑中转的账号、CLI和任务状态放在本机.local/，启动方式见[开发文档](docs/DEVELOPMENT.md#电脑ai中转)。历史Termux脚本位于deploy/legacy-termux/。

## 数据与发布

桌面运行数据默认在data/，可用WARDROBE_DATA_DIR覆盖；手机数据位于应用私有目录。识别、生图、推荐与搜图会向你选择的服务发送相应图片或衣物信息。

Git发布源码、通用配置、测试与必要Android离线wheel；data/、.env、.local/、登录/配对信息、真实衣物图片、APK和本机工具链不入库。配置示例只含空值或占位路径，详见.env.example与scripts/bridge/config.example.json。
