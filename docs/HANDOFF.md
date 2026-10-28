# 当前开发交接

详细开发步骤见[DEVELOPMENT.md](DEVELOPMENT.md)，协作规则见[AGENT-COLLAB.md](AGENT-COLLAB.md)。

## 当前功能

- 衣橱、逐件确认的多图自动识别与白底平铺、玻璃录入面板。
- 只读衣物详情、红色喜欢、确认删除、以当前衣物为必选的审美搭配。
- 添置方向自动配实物商品参考；无图时保持准备/重试状态。
- 搭配横滑与摄像头试衣入口，保留反馈和历史。
- 电脑官方个人CLI中转，识别模型及账号保持现有配置。

## 目录维护原则

前端按core/components/features分组；录入入口已拆出模板、剪贴板和文案模块。后端维持routes/services，必选约束单独复用。脚本按bridge/checks/assets分组；本机工具与运行状态集中.local/，不入Git。历史交接移至[2026-10记录](history/handoff-2026-10.md)，旧Termux部署归档deploy/legacy-termux/。

新增/移动前端文件后运行scripts/checks/web_assets.py --write，提交门禁核对生成清单。构建同步会清理独立构建目录中过期的app/web文件，避免新旧模块混包。

## 验证与接续

功能基线为796678c，目录重构提交c80bdeb。重构后pytest1118通过、1跳过；烟雾83/0、对比15/0；资源图47项；SW内存故障注入5项；独立浏览器35项交互及390/768/1440门禁通过；真机只读15项通过，未调用真实模型。所有审查P1/P2均修复复核。

手机覆盖更新后，衣物、图片指纹、录入草稿、手填、添置结果/请求与模型/配对配置逐项一致。APK用Python3.14核对全部48个web文件及48个后端模块，再确认手机base.apk、Download与本机dist指纹一致。用户数据不回滚、不用于写操作测试。

已清理可重新下载的旧安装包并归档旧工具材料。当前CLI和中转状态迁至.local/tools及.local/runtime；账号、配对码、端口和模型不变。manage.py已实测stop/start/status，服务ready。后续使用统一入口，别再运行历史效果图目录中的旧启动脚本。

继续开发先读AGENTS.md和DEVELOPMENT.md；界面现行规则见design/ui-contract.md及各功能设计文档。不要从历史README的编辑/裁剪/穿着记录说明恢复已取消的前台入口，不修改现有账号、模型或用户数据作为重构手段。
