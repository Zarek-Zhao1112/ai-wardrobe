# 交接：换一台机器继续开发

> **最后更新**：2026-10-28 · **写这份文档时的 commit**：`a2e3f1a`（以 `git log -1` 为准 —— 本文件自己也会被提交，别盯着这行比）
> 这份文档的用途：换机器后，人和 AI 都能在 5 分钟内接上上下文。
> **先读 `AGENTS.md`（约定与禁区），再读本文件（进度与待办）。**

---

## 本轮追加：只读衣物详情与必选搭配（2026-10-28，已覆盖安装，尚未提交）

用户确认最终稿，当前规则为card-detail-design §33/ui-contract §33。红色实心喜欢、只读已知属性、蓝色「用这件搭配」与浅红「删除这件衣服」；删除玻璃确认，取消/系统返回只关确认，喜欢保留展开/滚动。移除编辑、记录穿着、次数与详情再生图；后台API/已有数据不删。衣橱卡片喜欢文案同步为红心，次数改品类。

搭配入口#outfits/id显示本次必选，用户显式生成，保留审美/季节/3或5套/反馈/横滑/试衣。候选召回到模型、本地补齐和保存硬约束该件；无效/归档/越权/家居/季节冲突明确拒绝。BEGIN IMMEDIATE内复核并整批保存，沿用快照JSON，schema4。返回恢复背景筛选/分页和详情位置，跨页删除失效集合防止迟到结果复活衣服，删除当前必选回衣橱。

主agent唯一写入，两位只读审查分别核对后端和前端。P1保存竞态、删除后迟到结果、P2刷新焦点均修复并复核，无剩余审查项。最终pytest：1104 passed,1 skipped；烟雾83/0；对比15/0；独立临时库CDP 24项+删除竞态2项+深链/减少动态/嵌套返回4项通过；三视口原UI门禁无溢出/小触控/底栏遮挡。未调用真实模型或改手机用户衣物。

APK路径C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk，19,686,082字节，SHA256=12c781802346f23ba5ac5bb16183e530da72cbcf4bd061bbf30b9d17fca1ea98。SW v46，全部56个web文件与7个涉及的后端模块经Python3.14解包核对；已同步_build/dist/ai-wardrobe-debug.apk与可视化目录ai-wardrobe-v46-debug.apk。前版v45和本轮改动前源码备份位于可视化目录item-anchor-implementation-before。

USB断开后用户重连，重新获取即时before快照再install-r，返回Success。当前精确应用PID5738，CDP19225转发到webview_devtools_remote_5738。installed base.apk/手机Download/电脑dist/核验包SHA256一致。真机只读15/0：v46缓存、56份web源文件逐字节指纹、OpenAPI必选参数、只读详情、红心样式临时DOM样本、双按钮布局、删除确认取消/返回、必选入口及回到原详情滚动、退出回衣橱；POST/PUT/PATCH/DELETE与JS异常均0，没有实际删除/喜欢/生成推荐。手机停留衣橱页。

安装前与真机交互后两次比对均一致：26件衣物、59张引用图片、8条录入记录及所有属性/图片SHA、照片Blob、手填/草稿、添置结果/请求/已记下、模型/schema与bridge配置。忽略草稿根updatedAt，未回滚用户改动，模型deepseek-v4.1-flash及账号不变。证据：item-anchor-final-pytest.json、item-anchor-app-report.json、item-anchor-delete-race-report.json、item-anchor-deep-return-report.json、apk-item-anchor-verification.json、phone-item-anchor-report.json、item-anchor-data-verification.json、item-anchor-install-hashes.json；真机截图phone-item-anchor-{detail,delete,ready}.png。脚本与before/after快照在本聊天可视化目录。临时8834/9385服务已关闭，真实电脑桥8891保持运行；实际AI生成测试仍以隔离模型验收为准，不把入口验收表述为真机AI生成。

## 本轮追加：添置建议必配真实商品图（2026-10-28，已覆盖安装，尚未提交）

用户确认purchase-required-photo-preview，授权正式接入。主agent唯一写入，两位只读agent分别审查后端幂等/缓存与前端媒体/交互；本轮P0/P1/P2均已修复复核。当前规格为search-reference-design.md §32，覆盖旧§31“独立找图/无图文字卡”。

- 正式建议须实物图已load/decode，才和方向正文一起出现；完整方向先显示，缺图只加载或失败重试。results/saved/详情/配搭照片均门控，图坏则剔除，最后一图失效撤下该卡；保存busy时立即遮正文，结束后只回退一次，并优先回焦同方向重试。无纯文字/生图/固定图库展示选项，成功卡与详情无独立找图按钮。
- 可见result/saved且有从未登记的方向时，POST `/wardrobe/purchase/references/ensure` 自动补齐；home/GET不新建任务。服务端合并同plan的多个收藏片段，在RLock内登记稳定UUID，复用任意旧终态/未知记录，不因重挂重放收费任务。显式retry才用新job，未知ensure响应走needsEnsure，重试先幂等确认原任务。断网的pending保留原ID与查询，不变成新收费任务。
- 媒体解码缓存分job+retained/complete阶段，忽略旧阶段迟到回调；pending坏图不停止轮询，完成阶段重新验证同路径新字节。既有坏缓存校验SHA后原子替换，不再只判断文件存在。异步刷新保留普通卡/方法/生成按钮焦点和审美展开，优先标签按原计划排名。
- 本轮初次两次PC合成队列测试在进入runner前失败；单跑和线程跟踪通过，推断可能涉及Windows读状态与文件替换竞争。PC状态JSON落盘补PermissionError最多5次、10ms间隔的本地替换重试，并有合成拒绝后成功测试；不重跑模型。最终pytest **1084 passed/1 skipped**，专项43 passed（含3项解码阶段纯VM、16项请求/手势内存边界），smoke83/0、对比度15/0。
- 正式前端+临时库/假模型 **22/0**：慢图解码屏障、4宽度、备选来源、部分失败、单方向重试、切页复用、busy失图、未知ensure不重复收费，零外网/JS异常。原390/768/1440门禁通过、manifest200/SW受控。首次Chrome“超视口裁区”截图等待，关闭本轮精确路径Node客户端后改视口截图，验收通过；这不是应用请求超时。
- APK **19,681,045字节**，SHA256 `b8c19dd9534954c9e8addebd79c39da824ee772964837c7ef5f524a7957f7107`，**56个web文件逐字节一致**，SW **v45**/schema4。Android Python3.14读取4个后端.pyc逐项递归co_code/co_consts核对一致。install -r Success，installed base.apk/手机Download/电脑dist指纹相同。
- 安装前后 **26件衣物、59份引用图片、8条录入记录**及属性、SHA/照片Blob、草稿/手填、模型/schema、推荐/已记下/请求、bridge设置逐项一致（仅忽略draft根updatedAt）。没有回滚用户改动。真机只读 **9/0**：真实v45缓存/接口/模块/解码API，零写API/异常；没有进入会自动补图的旧结果、启动旧录入批次或摄像头。本轮真实搜索/识别/生图调用均0，功能收费链路在隔离环境验收；不要声称已重新实测真实搜索命中率。
- PC沿用原CLI/账号/配对/模型，health.reference_search=true，DS4.1与hunyuan-image-alpha-edit不变。部署BUILD `6b20602f72ae18de16cae19e0ac171ea692f7dba3ad4fbcbf149d4d1fd5c245f`，launcher PID31600；确认无CLI子任务及queued/running搜图才更新。保留实际8891中转。手机旧19224调试失联，按com.zarek.wardrobe精确pid重新19225转发；未注入系统输入/解锁。应用重启有短暂pid不可读，等待启动后恢复快照。
- 改动前13份源码和v44 APK在required-photo-implementation-before。无schema迁移，无stage/commit/reset。临时8833/9381验收服务收尾关闭；预览HTML仍是示例，未写成正式搜索结果。

证据在可视化目录：required-photo-final-report.json、required-photo-app-report.json、required-photo-ui-gate/report.json、required-photo-final-pytest.json/log、apk-required-photo-verification.json、required-photo-install-hashes.json、required-photo-data-verification.json、phone-required-photo-entry-report.json、required-photo-bridge-deployment.json，以及white-phone-required-photo-install-{before,after}.json和purchase-phone-required-photo-install-{before,after}.json。

## 本轮追加：自动寻找实物参考与搭配手机触控（2026-10-28，已覆盖安装，尚未提交）

用户确认purchase-search-interaction-preview和outfit-touch-interaction-preview并说“可以的”，授权正式接入。本轮主agent唯一写入，两路只读审查分别负责来源/任务/网络安全和前端触控/异步/焦点；本轮报告的P0/P1/P2均已修复复核。规格见新docs/design/search-reference-design.md §31。

- 搭配：新增horizontal-swipe.js，在图片区锁轴横拖>50px换整套，纵向保持原生滚动；圆点/按钮仍可用，衣物和文字同步更新，减少动态效果直接切换。短拖停顿、纵拖、第二指取消（含停留后抬手）、拖出边界和原生dragstart不会误开衣物；单击沿用已有玻璃详情。路由unmount释放手势/动画。保留原八审美/3或5套/季节/反馈/历史、inFlight与旧tryon deck保护，实时AI服务仍未接通，不能宣称已实现AI实时换装。
- 参考图：不再按固定6张图库匹配；原purchase-reference.js保留路径和全部旧接口，兼容转发至新media模块。新推荐文字成功落盘后登记各方向独立任务，页面先展示文字再GET轮询补图；老推荐/回看/GET/重启不自动创建收费搜索，用户可点“找实物参考”。只重新找图不重做方向。无新API密钥、账号切换或“使用积分”字段。
- 官方当前个人CLI2.173.0只有text2text WebSearch，无原生ImageSearch。固定DS4.1，精确查询的一次WebSearch+独占PreToolUse guard，关其他工具/MCP/会话/项目设置。仅接受官方init、工具call ID匹配的原生rawResponse网页URL；不采用模型最终回答编写的网址。网页实际Product元数据/明确商品路径的og:image→公开DNS/IP固定连接/逐跳验证与抓取总预算→真实图像字节。轮流看商品前三角度、最多6候选，DS再选≤3且每页≤1；面料成分/触感未知不臆断、不因这些未知信息否决外观参考。sources.json只保存公开搜索来源，不保存CLI原文。 最终补齐HTML空属性/异常JSONLD类型容错，分享海报URL与图像尺寸比例预过滤；核对结果要求single_garment/no_person/no_marketing_text/visual_match四项严格true，同ID重复或矛盾排除，价格/营销/界面文字禁止。
- 手机/PC稳定UUID幂等、queued/running/unknown/complete/empty/failed/interrupted状态；未知提交只查原ID，不自动再POST。新推荐显式授权的链路可后台继续，重启不重放收费任务。PC队列串行，只有CLI搜索/核对时占共享锁，抓网页不阻塞识别锁。照片缓存到手机私有images，原图/平铺/草稿不改；失败保留方向和旧图。坏/丢失PC图片给明确failed，避免手机长期pending无法显式重试。
- UI：一主图最多两张备选，点击和横滑切换，明确图片来源；少于3张按实际显示，空/失败/查询未知分开说明。旧快照不覆盖较新任务，卸载停轮询；保存忙期间遗漏的终态在finally补刷新，来源/缩略图/查询控件回焦；不重画整块玻璃层或更改返回历史。
- 验证：最终pytest **1072 passed/1 skipped**（含搜图后端、12项纯内存手势/请求边界及原摄像头/参考媒体14项），smoke83/0、对比度15/0。实际正式前端+临时库假搜索 **24/0**，四宽度图片边界/44px/来源、多图点击横滑/单独重找、原生图片拖放、单品详情开合及重挂；原三视口门禁通过、manifest200、SW受控，无异常/外网访问。新增实测慢速响应头在总预算内断开、候选副图与每页去重回归。未对假模型效果宣称推荐准确率。 最终全量结果已保存search-touch-final-pytest.json/log；沙箱临时目录权限失败的尝试单独保留为sandbox.log，放开系统临时目录权限后隔离回归通过，未改用户库。
- 真实能力验证：当前账号官方WebSearch成功，来源来自实际工具结果。前三个公开合成方向因印花/模特等不符合目标，最终没有照片。第四次查询含site:rats.jp提示，实际返回其他商家（并未强制域名限制）；旧规则误选findsindex.com的分享/价格海报，人工复核后撤销该正样本，未写入手机。已加入分享URL、尺寸/比例预过滤及逐候选四项严格核对，并补重复/矛盾结果的排除回归。随后复用第四次已保存的真实来源，纯灰V领方向仍为0张；将公开合成测试方向改为符合候选外观的灰色拼色图案V领开衫后，DS4.1选中1张nipponsaritote.com真实单品照片，已人工查看无模特/价格/营销文字。这个后续正样本证明取图与核对链路可用，不能宣称通用搜索命中率。正式应用不会为迁就照片改动用户方向，没有固定品牌/域名或图库；找不到保留方向并单独重试。共4次真实搜索、6次照片核对，均为公开合成方向，未上传手机衣橱、未调用生图。合格照片及来源见reference-clean-match-1.jpg、reference-clean-match-source.json及workbuddy-reference-pattern-probe.json；旧reference-live-match.png/source.json明确标为rejected，不得作为成功示例。
- 最终APK **19,676,975字节**、SHA256 `1f5eea8efda6ef391b7ec24c7de24ad8937b08e90fbb36efe7bed8d02c554a1b`，**56个web文件逐字节一致**，SW **v44**、schema4。Android实际Python3.14，PC venv3.13；使用C:/wbuild/py314/python.exe读取APK.pyc，与工作区编译的递归co_code/co_consts/签名/名字比较，4个手机后端模块一致。覆盖安装Success，installed base.apk/手机Download/电脑dist指纹一致。旧v43包与修改前源码在search-touch-implementation-before保留。
- 安装前基线已是 **26件衣物、59份引用图片、8条录入记录**（用户此前28→26的变化发生在本次覆盖安装前）。安装后衣物属性、图片SHA/Blob、草稿/手填、模型/schema、bridge URL、推荐/记下/请求状态逐项相同，仅忽略草稿根updatedAt，并把旧响应缺失的references按新增空字段归一。**未回滚用户在开发期间的改动**。真机入口11/0、五套布局+横向PointerEvent 10/0，零POST/PUT/DELETE和JS异常，不点击实际搜图/推荐/旧批次或摄像头。真正物理触摸/相机模型留用户体验；真Chrome已实际派发鼠标拖动。
- PC沿用原CLI路径/账号/配对目录启动，health.reference_search=true，模型仍DS4.1、生图仍hunyuan-image-alpha-edit。部署BUILD `5014f940e0149d38a3cde4e53064526a2e42b7d16f393add30de0a516f34f948`，启动记录PID22456；旧CLI无子任务且无queued/running参考任务才重启，凭据未输出。手机首次旧调试连接挂起，am start把已有应用带前台+新19224转发后恢复；系统拒绝KEYCODE_WAKEUP注入，未绕过系统输入权限。
- 自动审批拒绝“删除后重建purchase-reference.js”的补丁，担心现有依赖崩溃；删除未执行。安全方案为原文件原地保留接口的兼容转发，真实浏览器与APK验证确认依赖完整。没有剩余审批阻塞，没有stage/commit/reset。收尾关闭本轮8832/Chrome9374，保留实际8891中转。

证据在可视化目录：search-touch-app-report.json、search-touch-ui-gate/report.json、apk-search-touch-verification.json、search-touch-install-hashes.json、search-touch-data-verification.json、reference-bridge-deployment.json、phone-search-touch-entry-report.json、phone-search-touch-layout-report.json、white-phone-search-touch-install-{before,after}.json、purchase-phone-search-touch-install-{before,after}.json、workbuddy-reference-*-probe.json。预览HTML仍是独立示意，不是实际搜索命中截图。

## 本轮追加：搭配页衣物互相遮挡修复（2026-10-28，已覆盖安装，尚未提交）

用户反馈“卡片有遮挡”，明确在搭配页。本轮只改展示布局，主agent唯一写入，两位只读agent分布局/渲染交互审查，均无剩余P0/P1/P2。不要把下方历史“图片盒在卡片内”的验收当作“图片之间不相交”：旧版仅查边界，绝对定位的上衣、下装、鞋饰仍相交。

- `view-outfits.js`将所有单品划分为上衣/外套列、下装/连身列、独立鞋饰行，未知槽位也保留在鞋饰区；重复槽位自动增行、无图保留占位。`outfits.css`改正常流网格，说明独占一行，每个图片盒与链接点击区互不相交，contain完整显示；卡片自然增高，保留底栏滚动留白。原ID/图片、推荐请求、反馈、试衣快照不变。规格见ui-contract/aesthetic-outfits-design §30.1。
- 预览共享模板同样移除负偏移与绝对定位，outfit-live-preview.html及其别名已重建；两组3件的图片边界/互不相交检查通过。预览仍是设计示意，未连接真实视频AI。
- 验证：真实Chrome实际mount **61/0**（四宽度×七类3/4/5件、连身、重复/未知、无图；两两图片/链接不相交、边界/44px/末尾底栏、换套/详情/面板），既有隔离交互回归 **30/0**（摄像头仅合成视频），原三视口UI门禁通过，颜色15/0，针对性pytest **43 passed**。本轮不改后端逻辑，未重复全量1042项；上轮全量结果见历史。两位只读审查分别验证完整链接及28组布局。
- APK **19,660,945字节**，SHA256 `bde6f0889fbfb1723526b8fad00dc2d04c7bf6a9a31ca4a4996fc0e073ab21fc`，**53个web文件与源码逐字节一致**，SW **v43**。覆盖安装Success，手机Download及电脑dist同步；最终installed base.apk指纹另见overlap-install-hashes.json。旧v42安装包/本轮修改前源码在outfit-overlap-source-before保留。
- 真机Android12/WV96：入口/玻璃开合11/0，已有五套搭配逐套图片盒不相交、完整边界和触控8/0，零POST/PUT/DELETE与JS异常，没有点击摄像头或模型生成；最后在搭配首套。
- 安装前后 **28件衣物、65份引用图片、8条录入记录**、衣物属性/图片SHA/Blob/手填/草稿（仅忽略根updatedAt）、schema4、DS4.1和bridge配置全部相同，已记下列表也相同。**严格全部状态相同检查为false**：核对期间添置pending request ID、审美与新result发生变化（旧failed→另一新completed）；触发来源未核实。真机验收日志均零写API，本轮未修改添置逻辑；保留新结果，不能回滚或声称添置result逐项未变。差异仅以路径/哈希记于overlap-purchase-diff.json，未输出原始错误/凭证。总核对边界见overlap-final-verification.json。
- 缓存实测：首个v43仅替换同长度版本字符串，真机/sw.js no-store内容已v43，update返回ok但仍v42桶；响应mtime固定1980且ETag依赖时间+字节数，判断为同长度条件缓存误命中。追加版本说明改变脚本字节数后重建，v43正常激活。未删除WebView数据/缓存或草稿，未改缓存策略。今后仅改同长度VERSION时注意这个Android打包陷阱；本轮没有彻底改后端ETag策略。
- 未触碰WorkBuddy账号、DS4.1、生图模型、8891中转。没有stage/commit/reset。临时8831/Chrome9372仅用于隔离验收，收尾按进程路径核对后关闭。

证据在可视化目录：outfit-overlap-layout-report.json、overlap-regression-report.json、overlap-ui-gate/report.json、phone-overlap-entry-report.json、phone-overlap-layout-report.json、phone-outfit-card-fixed.png、apk-overlap-verification.json、overlap-final-verification.json、white-phone-overlap-{before,after}.json、purchase-phone-overlap-{before,after}.json。

## 本轮追加：真实添置参考、搭配构图和摄像头面板（2026-10-28，已覆盖安装，尚未提交）

用户确认purchase-reference-preview和outfit-live-preview，授权落实界面；明确插件方向是实时摄像头，实际视频模型/多件还原能力后续由用户测试。**正式应用已更新，实时AI视频服务尚未配置；不能宣称已接通AI实时换装，也不要退回照片试衣。** 主agent唯一写入，两位只读agent分摄像头/原生生命周期与参考图/搭配异步审查，发现的本轮P1/P2均已修复复核。规格见ui-contract §30、purchase-design §25.5和aesthetic-outfits-design §30。

- 添置移除SVG衣物轮廓，主卡和玻璃详情用本地真实白底单品照片。首批6张，来源/原始URL/哈希在tests/fixtures/s/fixtures/reference/sources.json；正向版型证据和冲突否决匹配受控品类/颜色，未知或不匹配留文字建议，不强行配错图、不使用模型图片URL。图库不限制AI可以建议的方向，无商品价格/店铺/购买按钮，详情有图片归因；已有搭配、记下和返回原API不变。未调用图像模型重生这些参考照片。
- 搭配每次突出一套实际单品，保留8审美、季节、3/5套、完整依据、反馈/历史、AI比较与失败保留。旧请求回页直接展示响应；试衣捕获deck身份，旧索引不能写新批。反馈按ID锁定，重建卡保留选中/禁用，迟到响应局部更新当前同ID卡；空成功响应禁止随后旧历史恢复，只在首次挂载恢复历史。各衣物图片盒保持卡片内，3件和额外外套四视口不裁边。
- 新tryon-camera.js和tryon-sheet.js：真实MediaStream视频、前后镜头切换、单品选择/换套、停止和玻璃开合。仅点击开启才请求audio:false，关闭/停止/路由/后台/销毁及时释放，迟到授权与play不复活。未连接视频AI时明确说明原始画面、AI按钮不可用，不装示意模特或假结果，不上传/存视频帧。
- Android只添加CAMERA（无RECORD_AUDIO），不在启动时申请；只批准当前前台本机127.0.0.1:8787页面的单独视频资源。started在onStart/onStop更新并在新/迟到权限回调复核；后台/销毁取消pending并通知前端停流。
- 验证：pytest **1042 passed/1 skipped**；Node永久摄像头取消/照片误配边界14/0（pytest包装1/0复核）；smoke83/0、对比度15/0。真实前端临时库+合成视频**30/0**；空成功/旧历史/旧面板和图片边界**13/0**。三视口原门禁无横溢、小触控、异常/console或看门狗误报，manifest200且SW受控。门禁仅在内存包装器指定截图裁区/活动状态，不改仓库判据；Windows两台无头Chrome并行绘制会停帧，交互与门禁需串行，不放宽动效结束判据。
- 最终APK **19,660,592字节**，SHA256 `4e4546f1065107b74f37da008da8352e91a5103230d3120bd5b68612f93f09e5`，**53个web文件逐字节一致**，6张参考和新模块均在SW预缓存/完整性双名单，**SW v42、schema4**。覆盖安装Success，手机base.apk/Download/电脑dist指纹一致；原v39安装包在style-implementation-before/installed-before.apk保留。
- 升级前后 **28件衣物、65份引用图片、8条录入记录**全部逐项对照保留：衣物属性/图片SHA、照片Blob、手填、草稿（仅忽略根updatedAt保存时刻）、添置建议/记下/请求、模型与bridge配置一致。8条录入均saved，安装前确认没有进行中的识别/搭配/添置。保留DS4.1/WorkBuddy账号/生图模型，未启动真实AI请求。
- 真机Android12/WV96只读入口 **11/0**，实际存在历史搭配，检查玻璃打开/返回保留、未点击时video无流、AI未连接说明、原建议读取、六张本地照片、v42缓存和安全摄像头API；零POST/PUT/DELETE和异常。**没有打开真机摄像头或测试实时AI效果**，权限状态仍granted=false，按用户要求留后续实测。手机最后在搭配页。
- 自动审批拒绝了向测试加入unsave动作的补丁，担心改动用户收藏；该动作未执行，也未通过其他方式重试。测试改为只读验证“记下”入口和当前状态。临时8830/Chrome9370/9371在收尾关闭，原8891中转保持；未stage/commit/reset。

证据位于可视化目录：apk-style-verification.json、style-install-verification.json、style-data-verification.json、style-app-report.json、style-empty-result-report.json、style-ui-gate/report.json、phone-style-entry-report.json、phone-style-outfits.png、phone-style-tryon.png、white-phone-style-{before,after}.json及purchase-phone-style-{before,after}.json。独立预览仍为设计示意，不是实时模型效果。

## 本轮追加：自动录入方案接入手机（2026-10-28，尚未提交）

用户明确确认auto-import-preview，授权实现。**右下角入口和多选自动准备已进入本轮APK**，下方历史条目的“仅预览”不是当前状态。主agent唯一写入，两位只读agent分别审查调度/安全与UI/生命周期，提出的本轮P1/P2均修复复核。

- 衣橱顶部文字入口改56px玻璃圆形＋，手机位于底栏上18px，徽标显示准备/总数，其他主页面不显示。打开录入隐藏FAB、关闭回焦点；末尾额外留白。面板顶部40px留白、进度条和48px缩略图、三字段/更多属性及正常流保存跳过；首屏常见尺寸完整露出按钮，图片绝对定位contain避免下摆裁切。照片入口更新为选择多张后自动准备；保留手填及商品文案功能。
- 新`web/js/features/import/queue.js`独立于draft.position，逐件识别→必要自动裁区→白底平铺，用户查看或修改前件时后件继续；收起不销毁。确认保存仍走稳定提交/冻结payload原接口，不自动入库或集中检查，无新增积分字段/原图裁剪选择。新CSS`import-batch.css`在headerless后加载，SW v39双清单共41个web文件。
- 新选择/追加只授权本次ID，实际持久化成功才发请求；旧草稿/刷新不恢复收费授权，已有图像任务只查原ID，识别中断留给显式重试。“继续自动准备”处理未开始项并先查询未知原任务，不混入已失败识别。单队列覆盖人工重试，出队与POST前复核batch/item/revision/attempt、pauseEpoch、授权、品类及持久化确认；超时/登录/网络/额度/未知图像故障暂停，缺品类留空继续其他项。
- 审查修复：旧草稿输入品类不能自动生图；已排队自动续链遇后件失败后不能越过暂停；慢持久化换件先锁并核对form.data-item，保存/跳过/撤销/追加均受切换锁保护；清pumping后通知解锁恢复按钮；AI冲突原因局部刷新；Fee前dispose/暂停/改源/品类变化/存储失败均不提交。保留字段/主动清空/IME节点和更多属性，不重绘当前表单。
- 官方CLI源码证据显示完整result后仍可能等待响应流与退出收尾。`desktop_bridge.py`超时可复用严格init来源/模型＋最后完整success、is_error=false、非空限长且非取消终态的结果；拒绝官方terminal_reason=aborted_streaming/aborted_tools。未改75秒或模型，未重发。日志仅本地request_id、阶段、耗时，无提示、图片、原始错误或凭证。电脑中转已在无node子任务时按路径验证后重启，build `d1fafb41e62569c78662adf9075ba55ff6a9f8fb9f3e6d3a6f6ac3500d5778b9`，启动记录PID28056（venv启动器，监听可能为子进程）。不宣称所有模型超时均解决，历史那次原因仍不能回溯。
- 验证：真实前端假识别/假生图**40/0**、批次四视口布局**19/0**、一般三视口门禁通过；永久`tests/frontend/import_queue_check.cjs`执行实际队列/生图代码12项内存请求边界，通过pytest包装进入全量。最终pytest **1041 passed/1 skipped**，smoke **83/0**、对比度 **15/0**。其中CLI退出超时恢复9+2组测试无真实CLI推理。两位只读审查无剩余P0/P1/P2。
- APK **18,834,302字节**、SHA256 `ff957b9fd5f33b311536567023713205ffdd5954dc90699be7839ce4bdfb0e49`，**41个web文件逐字节一致**，schema4/SW v39。覆盖安装Success，手机base.apk、Download、电脑分发包指纹一致；真实缓存shell-v39/img-v39。安装前后**22件衣物、47份引用图片、8条录入批次草稿**以及属性、全部图片SHA、照片Blob、手填、推荐与记下、模型/bridge配置均核对保留；本轮未触碰WorkBuddy账号或模型。手机仍`http://192.168.110.67:8891`、DS4.1。
- 自动审批拒绝原真机测试脚本中点击`[data-resume]`“继续上次录入”，理由为可能触发旧批次识别/生图并消耗积分，缺少真实付费测试授权；没有执行被拒动作，也未通过间接方式重试。安全替代只打开FAB查看入口和旧草稿卡片后关闭，**10/0、零写API/异常/模型调用**，旧批次items原样。批次恢复/自动准备/保存的完整行为在隔离环境验收，**未在真机启动旧批次或选择新图测试真实模型**。已在commentary向用户说明，final也应说明此测试边界。当前手机停衣橱，用户可自行体验新流程。

证据在可视化目录：`auto-batch-app-report.json`、`auto-batch-layout-report.json`、`auto-batch-ui-gate/report.json`、`auto-batch-app-ready-final.png`、`phone-auto-batch-entry-report.json`、`phone-auto-batch-{wardrobe,entry}.png`、`auto-batch-data-verification.json`、`apk-auto-batch-verification.json`、`phone-auto-batch-shell.json`、`white-phone-auto-batch-{before,after}.json`及`purchase-phone-auto-batch-{before,after}.json`。临时8825/Chrome9367收尾关闭，8891中转保持运行。未stage/commit/reset。

## 本轮追加：连接超时修复与自动录入预览（2026-10-28，尚未提交）

用户讨论将录入改为衣橱右下角玻璃图标、多选后自动识别和生成平铺、仍逐件确认；随后要求不加WorkBuddy积分字段，先修当前电脑识别等待超时，再出效果图。**右下角入口和自动批量功能本轮仅独立预览，尚未接入APP；不要把预览当已开发。** 已授权并安装的是连接修复与录入说明文字调整。

- 现场手机仍指向10.110.252.206:8891，PC当前Wi-Fi192.168.110.67、手机192.168.110.62，中转未监听。沿用原配对、账号、模型启动原中转，再通过手机PUT设置API验证新地址，HTTP200。手机现使用`http://192.168.110.67:8891`，DS4.1与图像模型不变；未重新提交用户识图。健康接口只证明连接，不证明CLI授权或模型识别输出。中转继续运行，不设置系统开机自启；换网络后仍须实际地址。
- `desktop.py`分离connect/pool4秒、read90/20/6、write20/6秒，ConnectTimeout归bridge_offline，提示核对地址变化/中转/同网；其他超时保持bridge_timeout。无自动重试或直付回退。`photo-import.js`同步离线提示；`import-assistant.js`去录入按钮caption的积分字样，任务动作不变。SW v38。两位只读agent审查通过。
- 新增6个合成连接/读取超时分类及预算、无重试/无敏感错误泄漏用例。专项85 passed；全量**1029 passed/1 skipped**、smoke**83/0**、对比度**15/0**。APK **18,827,138字节**、SHA256 `b64316378a5ee61be26fd40b44c0b9fb43ff2976d462313fc21adfaa1aee3d4f`；39个web文件字节一致且解包有ConnectTimeout特征。覆盖安装Success，电脑分发/手机Download/base.apk一致，真实shell-v38/img-v38启用。
- 安装前后**21件衣物、44份引用图片、8条录入批次草稿**保持；衣物属性、全部图片哈希、照片Blob、手填/批次草稿、推荐与记下、模型和连接配置均一致。地址修正发生于安装前，安装保留新地址。未清数据，未stage/commit/reset。
- 独立`auto-import-preview.html`自包含6张已有白底平铺图，不连接手机/中转/真实模型。3屏展示右下角56px玻璃＋、多选后自动串行准备、逐件人工保存；收起继续、暂停/恢复、输入保持及更多属性都可演示。无原图/裁剪切换、积分字段或集中检查页。主agent唯一写入、两位只读审查，预览P2（图片固有尺寸裁切、触控缩放、额外字段/重绘焦点）均修正复核。CDP**28/0**、320/390/768/1440无横溢/触控不足、零HTTP/异常/真实模型调用。截图`auto-import-overview.png`。用户尚未确认此预览，不自动将旧草稿转为收费批处理。

证据在可视化目录：`recognition-timeout-diagnosis.json`、`recognition-connection-restored.json`、`apk-timeout-verification.json`、`timeout-data-verification.json`、`white-phone-timeout-{before,after}.json`、`purchase-phone-timeout-{before,after}.json`、`phone-timeout-shell.json`、`auto-import-preview-report.json`。独立预览Chrome9366收尾关闭，中转8891保持运行。

## 本轮追加：添置结果返回初始页（2026-10-28，尚未提交）

用户确认`purchase-return-preview.html`并要求接入。已改前端导航/动画，未改模型、积分账号、后端、schema或衣物数据。规格见purchase-design §25.4与ui-contract §28；主agent唯一写入、两位只读agent并行审查，本轮提出的P1/P2均修复复核。

- 结果左上44px圆形玻璃返回，约300ms收起至添置初始；查看上次建议恢复缓存与滚动、不重新生成。显式退出后新切页进入保持初始，旧home/result/saved历史按条目及purchaseOrigin恢复。手机结果区内部滚动止于底栏之上，初始仍向下对齐。退出期间迟到推荐不拉回页面；卸载清理监听、动画和快照。
- 详情连续两次返回在关闭后同步最终历史。电脑设置独立消费返回，前进/刷新恢复原层、关闭回可见gear；慢首屏打开设置时保留顶层历史，不异步建重复结果层。回看、查询、切页没有真实模型调用。
- 隔离假模型主流程**56/0**、补充边界**6/0**；烟雾**83/0**、对比度**15/0**，390/768/1440门禁无横向溢出、小触控目标、底栏遮挡或运行异常。Android12/WV96触控验收**13/0**，没有写API请求或真实模型调用。真机测试最初读取SW时命中HTTP旧缓存，改no-store校验；另一次在tab动画防误触期间过早点回看导致假失败，等待page-turning结束后13项通过。中途手机曾切到其他应用，用户再次确认前台后继续测试。
- 最终APK **18,827,018字节**，SHA256 `eed0a28602a613a285543649a5b4be4ed07ea8996d8f2602d27f40001bb86078`；**39个web文件逐字节一致**，SW v37/schema4。覆盖安装Success，手机base.apk、Download和电脑分发包指纹一致。HTTP缓存刷新后真实CacheStorage为shell-v37/img-v37，停在#gaps初始页。
- 安装前手机最新资料为**20件衣物、41份引用图片、8条录入批次草稿（ready1/queued7）**，不沿用上一轮21件的旧统计。安装前后衣物属性、所有引用图片SHA、草稿/照片Blob、手填草稿、推荐与记下、模型及bridge配置核对一致。仍DS4.1，未触碰WorkBuddy登录。未stage/commit/reset。

证据在可视化目录：`purchase-return-app-report.json`、`purchase-return-edge-report.json`、`purchase-return-ui-gate/report.json`、`phone-purchase-return-report.json`、`phone-purchase-return-{result,home}.png`、`purchase-return-data-verification.json`、`apk-return-verification.json`、`phone-return-shell.json`。手机原资料快照为`white-phone-return-install-{before,after}.json`及`purchase-phone-{before,after}.json`。临时测试8824/Chrome9365收尾关闭，8891电脑中转保持运行。

## 本轮追加：白底卡片、旧图整理与积分账号切换（2026-10-27，尚未提交）

用户要求衣橱与添置的衣物图统一白底、加入一键识别生成，并授权处理旧图；随后再次明确**只更换WorkBuddy积分账号，模型保持不变**。规格见`flatlay-design.md` §23.3、`ui-contract.md` §27。主agent唯一写入，两位只读agent并行审查，提出的本轮P1/P2均修复或撤下并复核。

- 卡片、详情及飞行照片、添置已有衣物图片、搭配与录入照片容器统一surface白色。保留页面底色、玻璃层和轮廓示意装饰背景。中央图像提示明确电商纯白#FFFFFF、无灰蓝渐变及投影，保留原色、纹理和已有平铺形态；固定ImageGen的`revise=false`并由守卫校验，避免供应商自动改写提示。继续DS4.1＋`hunyuan-image-alpha-edit`，未改模型工厂或schema。新要求沿用原识别→生成路径，不新增自动调用。
- 现有21张平铺图先备份到`white-flatlays-20261027`：**17张灰底重新编辑并采用，4张已白保留**。每张检查周边68个3×3采样并完整对照原图；不以角点白替代视觉核查。17张均通过、21件最终均为白底。浅色阈值整幅刷白试验因会误改浅衣物而撤下，未打包，`flatlay-display.js`仍为原有局部留白处理。
- `presentation.attach`和正式POST新增可选`expected_display_path`，在同一BEGIN IMMEDIATE内核对来源及预期展示路径，冲突409保留用户期间更换的图。批处理先确认手机OpenAPI支持此参数才采用。稳定job ID持久化后提交；unknown只GET原ID，白底不合格停止队列，人工明确修正才创建新ID并保留原记录。新增服务/API冲突、匹配保留来源及revise固定值测试。
- 电脑个人CLI账号经官方网页授权，`/status`核实由「只只乌」切到**「zhao.」**。原账号已退出，再授权成功；未导出凭证、未改手机配对信息。用户明确允许仅信任新建的空目录`workbuddy-account-switch`，未信任上级目录。以后切号先用交互菜单确认`/login`/`/logout`命令存在再回车；不要把`/login`作为启动提问参数，也不要在`--tools ""`的界面里发送登录文字。
- 本轮真实图像工具共返回**20张结果、积分合计114.2**，其中17张采用、3张早期偏灰结果未采用并保留。另有1次`fetch failed / ECONNRESET`失败任务，在保留原ID及扣费不确定性后明确重试一次；该失败任务实际扣费未核对。切号时两次登录指令曾被当作普通文字请求，已停止，实际扣费也未核对；不能宣称本轮没有积分消耗，或把114.2当完整账单。请求模型始终保持原样。
- 最终pytest **1023 passed / 1 skipped**，smoke **83/0**、对比度 **15/0**；图像/桥接专项 **69 passed**。三视口门禁无横向溢出、小触控目标、console错误/异常或SW问题。6类实际媒体容器均白底；Android12/WV96真机21张衣橱卡片、3张添置已有衣物缩略图白底，截图`phone-white-purchase.png`。
- 最终APK **18,823,562字节**，SHA256 `d195206ea4dd4701d2517de7763d4f053d859df5baa9ee1b10bcaec5f4739eba`，**39个web文件逐字节一致**，解包确认路由/服务的expected_display_path特征；SW v36/schema4。覆盖安装Success，手机实际base.apk、电脑分发包与手机Download指纹一致。
- 升级安装前后资料全等；采用17张图片后`white-final-verification.json`再次核对：**21件衣物属性一致，22份识别来源/原截图字节一致，照片批次记录、手填草稿一致**，展示文件哈希、白底必要条件、人工核查及选用状态均通过。统计口径：照片批次有1条记录，其status=saved，**待处理项为0**；旧交接中“1件未完成”应按此口径理解，不能只凭draft.items长度判断未完成数量。
- 手机保留`http://10.110.252.206:8891`及DS4.1，最后回真实8787衣橱。电脑中转8891继续运行，build `a65240762b68e41ef691588cfac79a35fade25267d05c2aca5cc22b85a5ca2c4`；独立测试8823/Chrome9363和登录辅助窗口已关闭，新账号登录状态保留。未commit/stage/reset。

证据位于当前可视化目录：`white-final-verification.json`、`white-data-verification.json`、`white-phone-{before,after,after-batch}.json`、`phone-white-verification.json`、`white-install-verification.json`、`apk-white-verification.json`、`white-cards-ui-report.json`、`white-ui-gate/report.json`、`workbuddy-account-switch-report.json`及批次manifest/五张前后对照图。

## 本轮追加：添置结果衣物引用校验修复（2026-10-27，尚未提交）

用户在网络恢复后手动推荐，失败记录为`432537e5-1725-4394-b17b-dc9883b28575`，提示“AI 引用了衣橱中不存在的衣物，本次建议未采用。”此次已收到模型结果，失败发生在本地引用校验；旧分支将重复引用与未知编号合并报错，未持久保存原始输出，**无法追溯区分本次具体是哪一种**。不能据此断言模型一定编造了衣服。详见`purchase-design.md` §25.3。

- 主agent唯一写入`services/purchase.py`及测试、文档。每次生成的prompt和schema都带本次可用编号，新增动态owned枚举以及品类/主色约束；沿用DS4.1和既有模型工厂。电脑CLI将schema作为提示约束，并非供应商强制输出，最终仍由本地严格校验。
- 只对真实编号做去首尾空白、大小写归一和去重；未知编号、商品名称、数据库ID、近似拼写仍拒绝，保留角色冲突及全衣橱相似款校验。别名替换后的用户文案重新校验长度。失败新增安全code/计数，不存未知原文或完整模型输出；旧失败历史保留，幂等逻辑不变，不自动重发。
- 两位只读审查员按业务校验与桥接契约并行复核；提出的文案替换后长度P2已修复并补边界测试，最终无本轮新增P0/P1/P2。最终pytest **1017 passed / 1 skipped**，添置与电脑桥接专项 **72 passed**，smoke **83/0**。全部使用假模型，排查和安装期间未再次调用真实模型。
- clean构建APK **18,823,550字节**，SHA256 `eeac8d991511f8572863a57d2ccaeb66b7a79c77357229183aad0cfcddf1a6bc`。39个web文件逐字节一致，解包确认动态schema/allowed_owned/invalid_owned_references/invalid_plan_text/diagnostics字节码特征。前端未改，继续SW v35/schema4。手机覆盖安装返回**Success**，实际base.apk、电脑分发包和手机Download均为同一指纹。
- 升级对照 **21件衣物、43份图片、1件未完成录入草稿**，衣物属性、源图/原截图/平铺图、照片草稿及其图片、手填草稿全部一致，仅忽略照片草稿根updatedAt。数据SHA `9abde4df4764f53296a0cda5bfd09bbbb2b409fa20aa1d293effbdca34ff8005`。手机真实8787只读确认DS4.1/schema4与电脑地址`http://10.110.252.206:8891`保留，原失败request及result未改变，最后回到衣橱。
- **真实新推荐未验证**；需要用户显式点击“AI推荐添置”。没有自动重试或积分消耗。电脑中转8891继续保留，未commit/stage/reset，也未改系统防火墙。

证据在当前可视化目录：`apk-reference-verification.json`、`reference-phone-{before,after}.json`、`reference-data-verification.json`、`reference-phone-state-verification.json`、`reference-install-verification.json`及对应脚本。

## 运行连接修复：电脑地址变更（2026-10-27）

用户点击添置报“连不上电脑”。检查确认8891中转正常监听，手机未连接网络，且仍保存旧地址`http://10.168.1.180:8891`。用户将手机连到与电脑相同的「Xiaomi 14 Pro 6」后，手机IPv4为10.110.252.212，电脑为10.110.252.206。沿用本机配对信息，通过手机正式设置API验证电脑`/health`并更新为`http://10.110.252.206:8891`，HTTP200；刷新后地址、DS4.1、21件衣物和schema均保持正确。未修改应用源码或安装包，未重试收费推荐。`bridge-address-repair-20261027.json`记录健康检测结果，model_calls=0，不含配对码。原失败请求保留历史记录，用户可显式点击重新推荐。

## 本轮追加：三页无标题与添置主体下移（2026-10-27，尚未提交）

用户确认 `headerless-pages-preview-v2.html`：删除顶部品牌“AI 衣橱”和三页大标题，添置主体向下贴近底栏、删除“从已有衣橱出发”。规格见 `ui-contract.md` §26。主agent唯一写入，两位只读agent按壳层设置/生命周期与布局/视图拆分；本轮两项P2（设置关闭/保存后焦点、空已记下列表切换焦点）均修复并复核，无本轮遗留问题。

- index移除品牌DOM，新 `headerless.css` 手机隐藏原顶栏，桌面保留导航；三页操作行保留件数/录入、已记下、审美选择和圆形设置。main以aria-label表达页面身份，衣物名、审美名和模态标题继续使用。新共享serviceSettingsHtml，菜单事件委托和health刷新同步桌面/当前页面；连接设置显式返回可见summary，菜单重绘/旋转后寻找当前可见入口。
- 添置初始内容独立purchase-content，手机按视口/安全区/既有body底栏占位计算min-height，用自动上边距下移。短屏正常滚动，初始末尾距底栏16px；不固定裁切内容。空记下入口显示实际0，切入/返回均恢复焦点。搭配chooser移到首行，保留加载失败重试、完整8审美和其余原有操作。CSS使用class，圆形快照去ID不换排。
- pytest **1002 passed / 1 skipped**，smoke **83/0**，对比度 **15/0**。浏览器 **47/0**：布局/操作/焦点24、圆形切页17、旋转/320窄屏放大文字/衣物层背景6。844/640/480高度初始尾部避让分别16/16.21875/16.21875px；九审美展开和长结果尾部可达。三视口门禁无溢出、小触控目标、console错误/异常或看门狗误报，SW受控。此轮未改后端、schema或模型工厂。
- Android12/WV96真机 **12/0**，真实触摸三页切换、设置打开关闭/返回可见齿轮、0条已记下、9/8审美、模型/schema与v35保留；初始末尾距真实底栏**16px**，截图`phone-headerless-purchase.png`。初轮状态检查早于键盘收起且下一触点尚未稳定；只读确认弹层已关闭且summary聚焦后，测试补等待viewport稳定，12/0复核，未放宽判据。没有点击真实生成、保存配置或修改衣物/草稿。
- 最终 **SW v35**，新样式在index与SW预缓存/critical两名单。clean构建APK **18,822,862字节**，SHA256 `56979d1e4d128c8cbaa959ef970c70813b6df943da993dfa9728cb3c868b8a69`；**39个web文件逐字节一致**，并核对品牌/三页h1/旧引导删除、布局/focus特征。覆盖安装Success，原分发目录与手机Download同步。
- `headerless-data-verification.json`：升级前后 **21件、43份图片、1件未完成录入草稿**；全部衣物属性、源图/原截图/完整平铺字节指纹、照片草稿和图片、手填草稿、model/schema逐项一致，忽略草稿根updatedAt保存时间。数据SHA `9abde4df4764f53296a0cda5bfd09bbbb2b409fa20aa1d293effbdca34ff8005`。原始对照`headerless-phone-before.json`/`headerless-phone-after.json`。手机最后回到真实8787衣橱。本轮无真实模型调用或积分消耗。
- 独立测试服务8822/Chrome9362收尾关闭，WorkBuddy8891和用户原有预览保留，未commit/stage/reset。历史两项P2未扩大处理。

证据位于当前可视化目录：`headerless-{app,reveal,edge}-report.json`、`headerless-ui-gate/report.json`、`phone-headerless-report.json`、`apk-headerless-verification.json`、`headerless-data-verification.json`与对应脚本。预览v1/v2仍保留，实际应用以v35产物为准。

## 本轮追加：AI添置替换缺口页（2026-10-27，尚未提交）

用户确认 `purchase-advice-preview.html` 后授权落地。规格见 `purchase-design.md` §25。主agent唯一写入，两位只读agent分前后端并行审查；本轮提出的P1/P2均修复并复核，无本轮遗留项，历史两项P2未扩大处理。

- 导航「缺口」改为「添置」及购物袋图标，兼容#gaps与旧缺口API。新页面已有衣物拼图、蓝底白星芒、综合判断+八套完整审美、优先建议/其余方向、玻璃选购详情、记下列表/取消与换方向反馈、无需购买结果。不提供适用场合、价格或具体商品链接。候选衣物以受控SVG轮廓示意，真实搭配只用已有平铺图，不额外生图。
- `services/purchase.py`使用既有模型工厂；已配置电脑中转时继续DS4.1。200件角色均衡取样，另提供全量品类/颜色摘要，相似款校验遍历全衣橱；所有owned别名必须真实且角色无冲突，未知属性不补猜测。用户文案的内部代号统一清理，未知代号拒绝，空白和不完整结果明确报错，不伪装“无需购买”。
- 新GET/POST `/wardrobe/purchase`、PUT `/wardrobe/purchase/choice`；既有meta存结果、每次UUID墓碑与选择，不改schema/衣物。GET和刷新不调模型，POST先存状态、锁内二次核对；重复ID只查询，重启返回interrupted而不重发。失败保留旧结果。saved最多30方向，不喜欢保留20项供下次分析。过期指纹含模型上下文属性和名称。
- 关闭建议使用同hash历史状态与原位收回，背景DOM/滚动/aria及焦点恢复。app路由先清理建议层再打开衣物详情，关闭起步解除焦点锁；防多次返回。旧多方向保存组全部展示并可单独取消。读取失败仍能查看旧结果，epoch阻止旧GET覆盖新生成/选择。未知配饰使用中性轮廓，合法长属性/提醒可换行。
- 最终 pytest **1002 passed / 1 skipped**（添置27项），smoke **83/0**，对比度 **15/0**。实际浏览器 **24/0**，竞态与焦点专项 **10/0**，覆盖选择审美/显式调用/双击/失败保留/切页复用/反馈/不买/三视口/反序查询/外部#item/展开返回和旋转/多方向取消/长文本。三视口门禁无横向溢出、小触控目标、console错误/异常或看门狗误报，SW受控。
- Android12/WV96真机 **12/0**：真实手机后端新GET、DS4.1/schema4保留、真实衣橱未自动分析、九个选项实际触摸、玻璃开合/记下/返回/布局。读原衣橱仅GET；写交互在独立8821假模型临时库。首次两项触摸检查读取早于合成click，检查坐标与elementFromPoint后补等待；后台WebView暂停时切回前台后继续验证，未放宽判据。没有真实模型调用或积分消耗。
- 最终 **SW v34**，新CSS/两个JS及view-gaps在预缓存/critical双名单。APK **18,820,678字节**，SHA256 `9fa59a4b51ef9e69795f3e462cd6fa8b6efb1c4ee0c2d17ed8ccf02b9dfa330f`，**38个web文件逐字节一致**；后端字节码核对全量摘要/sanitize/请求恢复特征。增量包出现559KB旧空间后clean重打。覆盖安装Success，分发目录和手机Download同步。
- 升级前后 **21件、43份图片、1件未完成录入草稿**。`purchase-data-verification.json`的所有衣物属性/图片指纹/草稿图片/手填草稿/模型/schema均一致；草稿仅忽略根updatedAt保存时间。归一数据SHA `9abde4df4764f53296a0cda5bfd09bbbb2b409fa20aa1d293effbdca34ff8005`。原始快照`purchase-phone-before.json`与`purchase-phone-after.json`。手机最后回到真实8787衣橱。本轮测试8821/Chrome9360及reverse8821收尾关闭，WorkBuddy8891和用户既有预览保留。未commit/stage/reset。

证据位于当前可视化目录：`purchase-{app,edge}-report.json`、`purchase-ui-gate/report.json`、`phone-purchase-report.json`、`apk-purchase-verification.json`、`purchase-data-verification.json`，以及对应检查脚本。独立设计预览仍是 `purchase-advice-preview.html`，不与应用测试页混淆。

## 本轮追加：B悬浮玻璃详情、只平铺与视觉还原（2026-10-26，尚未提交）

用户指出上一轮仅接流程、视觉未还原，确认取消手动裁剪和所有裁剪图选项，必须平铺生成后入库；另确认B卡片展开/缩回的玻璃详情预览并授权落地。正式规格见 `card-detail-design.md` §24，覆盖§23.2的源图比较/采用/失败可用源图保存规则。

- 新`item-sheet.js`管理真实位置的玻璃壳/衣物图片开合、焦点/背景恢复；`item-detail.js`先展示大图/简短属性/穿着及常穿，编辑按需展开。`view-item.js`导出新模块。`app.js`保留衣橱/搭配/缺口背景DOM与滚动，录入和详情串行切换，深链接建立衣橱返回基线。非衣橱背景局部更新受影响链接，不额外调用模型重算搭配。
- 录入与编辑增加局部compact字段模式：名称/品类/颜色横排，季节移入更多；圆角照片、蓝底白字星芒、细进度条、正常流保存。照片无轻点裁剪，旧手动控件从DOM移除；没有同源平铺文件不能保存，失败仅重试/查询/保留草稿/跳过，原图和自动提取图后台留存供识别去重。
- presentationUrl只返回display，现有display_selected=false也不回退源图；没有平铺显示占位。详情无直接换源入口，避免先换源清掉已有平铺的半成品。新生成自动应用，应用失败保留结果、仅重试保存；关闭期间不执行自动apply，重新打开查询原任务。
- 两位只读审查所报P1/P2均修复并复核：关闭阶段mutation与自动apply共同锁；深链返回；保存后的晚输入/IME/光标；旧pending已入库补图的同ID属性更新、固定旧source hash CAS防并发错挂、补图成功属性失败刷新仍保留冻结操作；慢读取可见关闭/加载、反向Tab、内部滚动时关闭不跳整图。保留旧pending原提交指纹，不新建重复衣物。
- 浏览器专项 **41/0**：核心21、边界10、竞态6、焦点4。包括只平铺门槛与源hash、星芒实际SVG、横排与折叠、字段/选区、往返和滚动、快返/慢请求/深链/保存锁、旧pending恢复、并发来源409、closing生成完成、失败后刷新继续、IME与caret。脚本/报告位于可视化目录：`check_glass_application.cjs`、`check_glass_edges.cjs`、`check_glass_races.cjs`、`check_glass_focus.cjs`及对应glass-*-report.json。
- 后端 **975 passed / 1 skipped**；smoke **83/0**；色彩 **15/0**。最终三视口门禁 `glass-final-ui-gate-report.json` 无溢出、小触控目标、console错误/异常或看门狗误报，SW活跃。
- 真机Android12/WV96最终 **14/0**：真实轻点开合、返回、静态照片不裁剪、输入法、仅平铺保存、更多属性正常流、蓝底白字/星芒与照片框边界。初版v32演练发现旧ai浅蓝样式和图片网格固有尺寸越界，修成明确样式与absolute+contain后发v33复核。所有写操作均在独立8820来源+系统临时库，不向真实8787数据写入，不调真实模型、不消耗积分。
- 最终 **SW v33**，新增CSS及两个JS在预缓存/critical两名单。APK **18,795,910字节**，SHA256 `c6a6ff6cca3438bab4242d5da4dc3bacea48eaeb34154f337dc985e15284a811`；**35个web文件逐字节一致**，`apk-glass-v33-verification.json`包含新规则/closing/CAS特征核对。覆盖安装Success，电脑dist和手机Download同步。
- `glass-data-verification.json`：任务开始手机已有**21件**（用户此前已增加），全部元数据、**43份**源图/原截图/完整平铺文件及1批当前录入草稿内容/草稿图片、手填草稿、模型和schema逐项保持。草稿比较仅忽略根updatedAt保存时间。整体数据SHA `56817ea01a34e7c5095668fa5a06b8ad16837836ad96a81c56910959d7f468aa`；原始快照`glass-phone-before.json`、最终`glass-phone-after.json`。最后手机在真实8787衣橱。临时服务/测试Chrome及8820反向端口收尾关闭；WorkBuddy8891继续保留。前轮两项历史P2未扩大处理。

## 本轮追加：录入一键识别并生成平铺图（2026-10-26，尚未提交）

用户讨论后确认 `unified-import-preview.html`：录入仅保留“识别”入口，先识别属性再接着生图，默认选用结果，最后逐件由用户确认保存。正式规格为 `flatlay-design.md` §23.2。主模型仍DS4.1，图像工具仍官方ImageGen；本轮没有实际调用模型或消耗生成积分。

- `photo-import.js`新增单件编排锁；新`import-assistant.js`统一呈现入口、识别/生图状态、失败重试、比较和采用。原详情独立生成行为保持。选图、下一件、重裁和恢复不自动创建收费任务；现有同源图重识别时复用。手填字段含主动清空均受保护，状态更新不替换表单/IME。源图和完整生成图保留，本机角落整理继续接入。
- 识别失败不接生图；生图失败仅补生图或先保存裁剪图；品类未定时等待用户选择再继续。超时/刷新只查已有ID，识别时刷新显示中断。生图中品类变化取消默认采用，并提示核对。
- 两位只读审查发现并修复2项P1：旧版手选品类独立生图失败草稿，重试按钮必须显式generationOnly，不能额外识别；撤销跳过须与识别/生成共享锁，finally按本轮所有权清锁，避免切件卡死。复核无新增P0/P1/P2。
- 验证：隔离临时库+假模型浏览器 **32/0**，包含串行衔接/双击、失败和未知结果、同源复用、手填/DOM/选区、自动裁剪双图上下文与最终源hash、类别待定/中途改类、存储失败阻止生图、旧草稿视觉离线重试、撤销锁、刷新不重启、三种屏宽。真机Android12/WV96独立8820来源 **11/0**，覆盖真实触摸、IME组字、收起恢复、图像比较与角落整理、正常流按钮和触控尺寸。未向真实8787数据发送写操作。
- pytest **975 passed / 1 skipped**；smoke **83/0**；对比度 **15/0**；三视口UI门禁无溢出、小触控目标、console错误或异常，SW正常。首次受限执行因系统临时目录子目录写入被拒，正常系统临时目录重跑上述门禁通过，未更改测试标准。前端测试脚本在可视化目录 `check_unified_import.cjs`、`serve_unified_import_tests.py`、`check_phone_unified.cjs`；报告分别 `unified-import-report.json`、`phone-unified-import-report.json`、`unified-ui-gate-report.json`。
- SW **v31**，新模块在预缓存/critical两名单。APK **18,795,258字节**，SHA256 `4f66918349ae0bcbe4eaaea6d56283ffbb9ae9d5b1bc3fa7c68351f5a4620167`；**32个web文件逐字节一致**，双名单和两项P1修复特征已解包核对。覆盖安装Success，电脑dist及手机Download同步。
- `unified-import-final-verification.json`：20件衣物、来源图片、元数据、采用数量、手填草稿和模型配置一致。照片录入草稿完整hash有变化，经离线重算匹配安装前hash，确认**只有updatedAt保存时间变化，其余JSON逐字一致**；原保存时间1793020588293，现1793022449817。没有清除用户当前一件待录入草稿。手机最后在真实8787衣橱页；本轮临时8820服务/浏览器/反向端口收尾后关闭。前轮两项保留P2未扩大处理。

## 本轮追加：自动整理平铺展示角落（2026-10-26，尚未提交）

用户看到WorkBuddy角标后，确认把本机处理写进逻辑。正式规格见 `flatlay-design.md` §23.1。新 `flatlay-display.js` 统一应用于录入/详情/衣橱/搭配；只生成浏览器展示副本，完整生成文件、识别来源和数据库均不改写，不发模型请求，主模型仍DS4.1。

- 取消最初整条底边裁切方案：实际裤脚和衣袖已靠近底边。改为仅识别右下角平台双行角标，小文字连通组件与64×32角标形状位图匹配后，再逐原像素检查局部框和保护区，仅在确认的留白内延续邻近背景。不是通用去水印或语义分割，未知角标/主体接近时保留完整图。
- 录入和详情增加“完整平铺图”标签；source/full/display切换不采用、不再次生成，不重绘属性表单。节点状态与来源绑定防迟到覆盖。已有卡片与新结果共用逻辑，原生成文件仍可查看。
- 双任务队列，32份Blob缓存；卡片靠近可视区域才处理，移除节点取消尚未开始的任务、撤销节点专属URL；临时解码失败可重试。新模块进入SW v30预缓存/critical双名单。
- 两位只读审查按算法/资源和UI集成拆分。修复低对比白衣被当留白、256缩采样漏细线/透明像素、绕过懒加载等P1/P2；补已知角标形状核对，原白衣4亮点反例现返回unsafe。最终复核无新增P0/P1/P2。模板仅包含角标文字形状，无衣物/账号数据。
- 验收：pytest **975 passed / 1 skipped**，smoke **83/0**，对比度 **15/0**；图像保护 **12/0**（含原图恢复、已知/未知文字、白衣、亮点、细线、透明和15张框外RGBA全量一致），展示集成 **10/0**（含三视口、未提交输入、源图、懒加载、URL回收），原平铺生成全流程 **17/0**；三视口UI门禁无溢出/小触控目标/console错误或异常，SW正常。
- 对现有20图，**15整理、4无可见角标不改、1保守保留**。第17件米白外套的袖口阴影接近角标范围，保留完整图并可见提示，不为消除所有标记而放宽保护。15张处理图框外全部RGBA一致。不得宣称20张全部已安全去除标记。
- 真机Android12/WV96 **8/0**：确认相同15/4/1结果、真实触摸完整图/整理图/源图切换、保守提示、布局和触控尺寸。手机最后在衣橱，截图 `phone-flatlay-footer.png`。
- 最终APK **18,791,842字节**，SHA256 `54fb865504eb0f375893fbb5fb2b14148f50585674117d164450009e12fe5783`；31个web文件解包逐字节一致，v30双名单/形状核验/完整图特征核对。覆盖安装Success，电脑dist和手机Download同步。
- `flatlay-footer-final-verification.json` 确认20件真实来源、完整生成文件、衣物元数据及两类录入草稿均保持原样；来源聚合SHA仍 `4fb90b2519b389e63ad447f67e40d507dbe768638de337462bf795ba32786f25`，元数据SHA仍 `badb5e5843d84fc090346a21a51b6578fe1c9bc782febfd7df007a20a7346873`，照片/手填草稿SHA分别仍为 `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b` / `d5b16a68659d8c0da75f01c65ef139bd5d9e9cd46f30add737deb2c578172da4`。本轮无真实生成或识别调用，额外生图积分0。上轮两项保留P2未扩大处理。

## 本轮追加：现有20件服饰采用平铺图（2026-10-26，尚未提交）

用户在功能接入后明确要求“先把现在的衣服用这个方式改一下”，授权对现有衣物生成并采用，不再沿用上一轮“只验证、不修改20件”的范围。没有自动扩展到以后新增衣物。

- 一次操作批次位于可视化目录 `existing-flatlays-20261026/`；`manifest.json` 保留原20件完整元数据、裁剪源图全量SHA256、逐件任务ID、生成图、核查与采用记录。原20件独立original_image_path均为空；实际保留并备份了所有可用真实来源图，不能声称恢复了历史上不存在的完整截图。
- 19张新生成 + 复用先前同源米白外套1张。第一轮01出现正反双面、02鞋垫编字、04/05补成松紧腰头；人工检查后分别定向修正一次，旧结果保留在批次目录，没有自动重试失败请求。两位agent只读审查操作脚本/视觉，主agent唯一写入。最终20件都核查并采用，人物与购物界面文字去除；工具自身AI生成标记保留。
- 本轮23次图像调用（19新生成+4修正）工具各返回5.71，合计 **131.33**。这个合计不包含上一轮两次试验，也不代表包含主模型调用的完整账户账单。未用其他付费接口；主模型仍DS4.1，出图工具仍hunyuan-image-alpha-edit。
- 为防批量GET后用户同时采用新展示图被覆盖，presentation.attach新增可选only_if_empty，默认False保留普通详情替换行为。批量传True，在同一BEGIN IMMEDIATE事务检查display仍为空；源hash变化或新展示图冲突返回409并跳过。新增service/API两项测试，最终 **975 passed / 1 skipped**。未改前端文件，仍SW v29。
- 操作脚本POST前保存稳定任务ID，断点只查询原任务；采用响应丢失时读取手机展示字节确认，不重复安装。最终验证拆分data_preserved和batch_complete，生成未采用/未知/待采用不允许假绿，并用坏状态自证。prepare允许中断恢复并备份独立原截图（如有）。本轮审查所提P1/P2全部修复并复核。
- 手机APK补丁 **18,786,142字节**，SHA256 `b53a4d5b369b0d9f176084991c4846450d5cb091962c4d5247143675d2a0eca5`。30个web文件解包逐字节一致，items/presentation字节码含only_if_empty，手机OpenAPI参数核对通过；覆盖安装Success，电脑dist和手机Download同步。
- `existing-flatlays-20261026/verification.json`：**20/20采用**，20件真实来源和全部属性逐件保持，20张展示字节与核查结果一致；data_preserved/batch_complete/passed均true。updated_at与展示字段按设计变化。所有原图聚合SHA仍 `4fb90b2519b389e63ad447f67e40d507dbe768638de337462bf795ba32786f25`；照片草稿SHA仍 `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`；手填草稿SHA仍 `d5b16a68659d8c0da75f01c65ef139bd5d9e9cd46f30add737deb2c578172da4`。
- 真机刷新后 `phone-display-verification.json` 确认20个卡片全部使用已采用display路径、20图解码加载、无横向溢出；实际截图 `phone-wardrobe-after.png`。手机最后在衣橱，用户可在详情恢复裁剪图。原有P2（键盘旋转、本机独立预览回收）未在本轮扩大处理。

## 本轮追加：WorkBuddy 衣物平铺图（2026-10-26，尚未提交）

用户要求去掉模特，只留单独衣物平铺图，优先继续 WorkBuddy 积分，并确认主模型仍用 DeepSeek V4.1 Flash。实现规格见 `flatlay-design.md` §23。主 agent 唯一写入，两位只读审查分前后端；本轮发现的 P1 与 P2 均修复后复核。

- 主模型固定 `deepseek-v4.1-flash`，识别、属性与搭配沿用。图像生成由 DS 调用官方 `ImageGen`，图生图模型 `hunyuan-image-alpha-edit`。不是 DS 直接输出图片，也不回退 GLM 或其他直付 API。
- 官方 npm CLI `@tencent-ai/codebuddy-code@2.173.0` 安装于可视化输出目录 `workbuddy-image-cli-runtime`，使用 Node，官方个人登录来源 `copilot.tencent.com`。旧 native CLI 被系统应用控制阻止；未修改系统安全策略。桌面精简版独立调用不继承登录，与 npm 个人 CLI 区分。
- schema v4 增量补原截图、生成图、来源指纹、模型和采用标记。真实裁剪图继续作为 image_path/image_hash，识别和去重不读生成图。列表/详情明确输出只读 image_hash，供采用时匹配来源。旧 20 件不自动生成或改变展示。
- 录入和单品详情都能手动生成、对比和采用/恢复。生成完成仅预览，属性 DOM 不重建。原图、裁剪图、采用的展示图原子入库；重裁保留完整原截图并使旧生成图失效。详情保存/裁剪/上传/识别/删除/生成/采用共用 gate，属性输入仍可编辑。载入本机预览和恢复任务也纳入 gate；服务设置变化后按最新状态恢复识别按钮。
- 任务请求 ID 持久化且幂等；刷新/断线只查原 ID。电脑重启的未确认任务不自动重跑。24 小时缓存通过后续请求懒清理；过期 GET 返回 expired，保留小指纹墓碑，旧 ID 不重新扣费。电脑输入图生成后清理。PC 图像和识别任务共用容量锁。
- 工具前置钩子用同一 Node，无第三方依赖；严格固定来源、输出目录和一次调用，异常退出 2 阻止执行。官方 HookExecutor 使用 bash，命令须用 shlex.join，不能用 Windows list2cmdline；实测完整 CLI 钩子链路成功。新模块进入 SW v29 预缓存/critical 双名单。

验证证据在当前可视化输出目录：

- 两次显式真实图生图：`workbuddy-flatlay-live-20261025/report.json` 和 `flatlay-production-runner-20261025/report.json`；均去除模特，分别约 20.7/20.2 秒，图像工具各返回 5.71 积分。工具返回合计 11.42，不代表账户完整账单或固定单张报价。其余单测、浏览器与真机交互用合成图/假工具，不再调用真实生成。
- pytest **973 passed / 1 skipped**；smoke **83/0**；颜色对比度 **15/0**；合成图片完整交互 **17/0**；Android12/WV96 只读及内存合成兼容 **7/0**。三视口无横向溢出，相关触控目标 >=44px，无 console 错误或异常，SW 正常。真机未录入测试衣物，也未改用户草稿。
- 两位审查修复：过期 ID 重扣费、并发清图、原图第二次 rename 回滚、钩子 shell 转义/异常放行、自动裁剪遗留旧派生、设置后按钮刷新、忙碌切换按钮、详情采用与保存竞态、载入草稿丢恢复、服务状态被 disabled 旧快照覆盖。最终复核无新增 P0/P1/P2。保留上轮键盘旋转 P2。
- 最终 APK **18,785,934 字节**，SHA256 `0968388ba42b555155ec3425a51454bdc81b3af35e629b0ca62c51324dece5fb`，30 个 web 文件解包逐字节一致，schema v4/图片接口与事务字节码特征核对；覆盖安装 Success，电脑 `_build/dist` 和手机 Download 同步。
- 升级前后均 **20 件**；所有真实来源图聚合指纹 `4fb90b2519b389e63ad447f67e40d507dbe768638de337462bf795ba32786f25`，衣物资料指纹 `182a027dd2f6bfd4873ed3d2811cf8bb29158a3c8f7b58ceed6de608f2af8be2`，照片草稿指纹 `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`，手填草稿指纹 `d5b16a68659d8c0da75f01c65ef139bd5d9e9cd46f30add737deb2c578172da4` 均一致。未采用任何现有衣物生成图。
- 当前电脑网络已变，手机旧地址 `192.168.110.51` 更新为同网段 `10.168.1.180:8891`，沿用原配对码，保存连接 200。电脑端最终 build `d3188fa6b68f76fc207576c780ef167a47a7a6b658b18b7bb4b36549f956da89`。`phone-flatlay-transport.json` 验证真实手机后端→配对电脑→合成缓存结果 200，不冒称该传输测试调用了真实模型。端口8891和用户预览8842保留，独立测试服务8819/Chrome9353收尾停止。

保留本轮 P2：详情独立预览 `draft/visual:<itemId>` 为便于返回恢复而持久保存，当前删除服务端衣物不主动回收这份本机预览，可能占用额外本机空间。暂缓批量扫描回收，避免误删仍使用中的其他条目/批次；后续应在删除成功后按确切 ID 清理，不使用全库 clear。服务器衣物图片删除已覆盖 source/original/display。

## 本轮追加：衣橱玻璃录入面板（2026-10-24，尚未提交）

用户确认九个状态的 `glass-import-preview.html` 并要求继续。实际接入见 `ui-contract.md` §22：衣橱标题右侧玻璃「＋ 录入」按钮，底栏改为衣橱/缺口/搭配三项；450ms底部浮起，背景渐模糊8px，九成屏玻璃面板，内部滚动。新 `import-sheet.js` body级浮层驻留真实录入DOM；#add为隐藏衣橱子路由，直达先建立衣橱历史。原三页圆形展开继续使用，录入打开不混用展开动画。

- 收起/返回/背景/顶部下拖统一检查点，保留本次内容和图片；隐藏时不消费全局图片粘贴/拖放，同条目迟到识别仍归原条目。裁剪和确认框优先退出；Escape由document冒泡到window，不会漏确认框。WV96不用inert，背景aria及tabindex还原，关闭动画时浮层aria-hidden。
- 手填草稿独立存draft/manual，与批量draft/current和photos隔离。恢复按字段保护先输入的内容，文案和折叠状态单独保护；checkpoint和手填提交等待manualReady。仅新批次首件明确继承时消费手填字段，文案保留；恢复/追加旧批次不删除独立手填。
- 普通保存、文案批量及选图/恢复准备过程同步锁住旧表单，照片保存沿用稳定提交标识；检查点结束再次检查保存状态，阻止等待期间新发保存被收起。保存成功后单纯折叠属性不解除重复提交保护。草稿写入失败明确提示保持应用打开，驻留DOM和内存图片保留；flush不冒充持久化成功。
- 跳过/保存按钮与撤销在表单正常流中，更多属性会推下操作区，toast提升到浮层上。窗口/键盘只调整visualViewport，不重挂载。顶部非交互区单指拖动，第二指、取消、键盘打开时停用拖动。
- 只有发生入库才刷新衣橱；刷新保留已加载分页和网格，原位重取。渲染前取当前滚动值，仅仍连接的当前衣橱根恢复，迟到请求不滚动新页面。未改后端/schema/模型/原生壳。SW v28，新模块进入预缓存与critical双名单。

验收：pytest **942 passed / 1 skipped**，smoke **83/0**，对比度 **15/0**。录入主流程 **27/0**、受控慢恢复/直接保存/准备锁/存储失败/拖动/长列表及迟到刷新 **14/0**；原整页展开（三tab） **17/0**，八套审美 **28/0**。390/768/1440无横向溢出，四手机子页无小触控目标、无底栏/面板末尾遮挡、无console错误或异常，SW正常受控。部分旧测试修正为等待真实表单显示、亚像素坐标容差，并隔离残留合成草稿/重复图片；未以放宽功能判据消除失败。

两位只读审查员按交互/路由视觉拆分，修复并复核：恢复期间空覆盖、恢复旧批次误删manual、缺省fillForm清空提前编辑、选图准备期间并发保存、确认框返回、列表塌缩及迟到滚动等P1/P2；无遗留P0/P1。主agent为唯一写入方。

保留P2：静态审查发现键盘已打开时再旋转屏幕，宽度改变会重置可视高度基线，此时可能允许顶部拖动收起。该组合未做真机复现；本轮暂缓方向切换下的键盘基线重建，原因是收起仍保留DOM/草稿，关闭按钮和返回可用，不影响本轮常用竖屏录入链路。后续处理方向与键盘组合时补专项。同方向819→498→819已实测修复，键盘开启禁拖、收起恢复拖动，14项真机验收包括此项。

最终Android12/WV96真机 **14/0**，含实际触摸、玻璃与8px模糊、底部保存可达、更多属性正常流、键盘、收起继续、返回、顶部下拖、原扩散及八套方案。真实键盘visualViewport由819.27缩为498.18 CSS px，面板底边498.18，与可视底边一致。真机截图 `phone-glass-import.png` 来自native screencap，截图前临时搜索无匹配以排除商品照片，随后恢复并回到衣橱；未识别、生成或录入真实衣物。

最终APK **18,766,914字节**，SHA256 `f4afa8232bddac12b2f165e339162f3fd6100f4e654ce406ea8a5f5cd8efcbf1`。28个web文件解包逐字节一致，SW v28与两个新模块清单、既有DS4.1/八套审美后端字节码特征核对。覆盖安装Success，`_build/dist`和手机Download同步。升级前后 **20件、无未完成照片批次**，该照片草稿哈希均为 `74234e98afe7498fb5daf1f36ac2d78acc339464f950703b8c019892f982b90b`；新增空手填草稿与折叠UI状态不是照片批次，不将其混入该哈希。没有导出用户照片。手机最后停在衣橱。

证据在本会话可视化目录：`glass-import-{app,races}-report.json`、`glass-import-ui-gate.json`、`page-reveal-app-report.json`、`expanded-aesthetic-ui-report.json`、`phone-glass-import-report.json`、`phone-glass-import.png`、`apk-glass-import-verification.json`、`preview-upgrade-glass-{before,after}.json`。测试8819/9353及其独立Chrome结束后停止；电脑中转8891与用户预览服务保留。工作区已有大量尚未提交的历史改动，未commit/stage/reset。

---

## 历史追加：底栏触点整页圆形展开（2026-10-23，尚未提交）

用户确认`page-reveal-preview.html`：新页面从手指按下位置向外展开至全屏，圆外保留旧页。规格见`ui-contract.md` §21。实际代码使用pointerdown落点，在有效轻点成立后导航；600ms、4px柔边，滑动/取消/长按不复用触点。

- 新`web/js/components/page-transition.js`：固定旧页可视DOM克隆，前缀radial-gradient透明洞逐帧扩大至最远角+6px。复制内部滚动与选项值，删除ID/autofocus、设置可聚焦节点tabindex=-1、aria-hidden，不依赖WV96不支持的inert。去ID前冻结add-submit flex、add-hint flex-basis、o-message margin-bottom，避免圆外画面换排。
- `app.js`每次render先连接独立新main、断开旧main，再mount；序号保护catch/finally。动画不等待模型/历史请求。root仍在body原位置，不包裹实际页面，保留fixed/sticky和撤销条兄弟选择器。展开期间新main不可误触/取得焦点，底栏可排队最后一次目标；点回当前页清除旧排队。返回/外部hash、尺寸变化、后台、低动态变化、超时/异常统一清理。
- 逐件录入、弹窗、裁剪使用原导航守卫并直接切换；守卫拒绝时不换root、不复位滚动、不展示克隆，后续确认不复用旧触点。键盘/桌面/低动态/不支持遮罩直接普通导航。同页不重挂载。
- CSS增加快照与交互锁，无后端/schema/模型/原生改动。SW v27，新模块同时进入SHELL_ASSETS与critical。

验收：**942 passed / 1 skipped**；smoke **83/0**，对比度 **15/0**。整页展开浏览器专项 **20/0**，含落点、圆外保留、去ID/焦点、误触阻止、快切/点回当前页/返回、resize、低动态、键盘/移动/长按、慢历史/迟到挂载、内部滚动、提示间距、草稿取消及确认保留。原审美交互 **28/0**；三视口390/768/1440与四个手机子页门禁无溢出、小触控目标、底栏遮挡、console错误/异常，SW正常受控。

两路只读审查提出的“点回当前页仍跳旧队列”P1及“去ID后布局漂移”P2均修复并复核消除，无遗留P0/P1/P2。真机Android12/WV96 **12/0**：真实手指坐标（按小于1CSS像素容差比较，不能直接比较格式化浮点字符串）、中间帧/结束锁、快切/返回、八套方案、布局、DS4.1及数据保留。自然RAF采样正常结束约600ms；这不是正式GPU帧率基准。

**真机截图陷阱**：WV96的CDP `Page.captureScreenshot`未反映radial-mask合成，中间帧看起来仍是旧整页；`adb -s ef473680 exec-out screencap -p`真实屏幕能看到正确圆形展开与柔边。验收以`phone-reveal-native-middle.png`为视觉证据。该帧仅录入空表单→缺口页，不含用户商品照片。中间帧通过测试侧临时控制RAF固定280ms，随后恢复；单独自然动画采样不截图。未修改用户模型/生成/反馈，未导出用户图片。

覆盖安装前后 **20件衣服、无未完成草稿**，草稿哈希一致。APK **18,760,854字节**，SHA256 `f0148fa7e718c6663c75dd7bdd68419355be67bebcdd8cf519384f2eb12ab980`；27个web文件逐字节一致，遮罩模块、600ms常量、SW双清单及v27已解包核对。覆盖安装Success，分发目录与手机Download同步；电脑中转8891保持运行。

证据在本会话可视化目录：`page-reveal-app-report.json`、`reveal-ui-gate-report.json`、`expanded-aesthetic-ui-report.json`、`phone-reveal-report.json`、`phone-reveal-native-middle.png`、`apk-reveal-verification.json`、`preview-upgrade-reveal-{before,after}.json`。下方八套方案为上一轮历史。

---

## 历史追加：八套审美方案（2026-10-23，尚未提交）

用户要求“多几个审美方案”。原自然松弛/利落清爽v1保留，新增同色层次tonal、复古工装heritage、运动混搭sport_mix、街头廓形street、柔和轻盈soft、撞色焦点color_pop，均v1，规格见`aesthetic-outfits-design.md` §20.1及`ui-contract.md` §20.1。

- `aesthetics.py`每套显式配置版型/风格/图案/品类/颜色召回、配色与图案策略、层次/整套关系及权重。旧两套legacy数值不漂移，基线分数仍为0.785/0.845，召回顺序0,1,2与1,0,2。
- 运动混搭奖励运动线索与其他基础款跨角色共存；街头比较宽大衣服与球鞋/靴的衔接；复古工装比较相关品类覆盖。撞色比较基础色与共同重点色，不按颜色数量加分。材质继续unknown不评分、颜色/比例不做实物断言；HOME_LIKE/季节/白名单/候选预算保持。
- 后端只读审查发现同色/撞色唯一目标可能落召回探索空档；已把兼容角色颜色关系纳入两者召回，并去重排序颜色上下文、增加特定空档反例测试。双路复核无残留P0/P1/P2。
- `view-outfits.js`原生details选择器默认收起，当前名称与简介可见，展开手机1列/平板2列/桌面4列，最高55vh内滚动；选中收起并聚焦summary。五维说明和生成/历史快照、异步锁保持。SW v26，无新增模块，无schema迁移。

验收：最终 **942 passed / 1 skipped**（审美专项42项）；smoke **83/0**，对比度 **15/0**；浏览器审美交互 **28/0**，包含八套实际点击、展开可达、焦点归位、三视口、双击/失败/改选/跨页/慢历史。标准390/768/1440与四手机子页门禁无溢出、小触控目标、底栏遮挡、console错误/异常，SW受控。新方案未新增付费真实模型试验；复用既有通用方法提示链路，合成/临时库回归不能证明主观审美准确率。

真机Android12/WebView96 **21/0**：八套逐一真实触摸、收起焦点、五维说明和布局、schema3/DS4.1中转保留。覆盖安装前后 **20件衣服、无未完成草稿**，草稿哈希相同。首轮紧接安装的前两项触摸未触发，稳定后复测21/0；推断与已有SW首次接管刷新时序有关，本轮未更改SW接管机制。未生成或反馈用户真实搭配，未导出照片。

APK **18,756,806字节**，SHA256 `05d23eb4b49cee09b813d249aa481c0f2c32e0946df091e35d19b56c7741d6ae`。26个web文件逐字节一致，后端八套/颜色上下文、快照/理由过滤、原生外链特征核对通过。`adb -s ef473680 install -r`返回Success，已同步`_build/dist`及手机Download安装包。电脑中转8891保持运行，不停止用户原型服务。

证据在本会话可视化目录：`expanded-aesthetic-ui-report.json`、`expanded-aesthetic-ui-gate-report.json`、`phone-expanded-aesthetic-report.json`、`apk-expanded-aesthetic-verification.json`、`preview-upgrade-expanded-{before,after}.json`。下方两套方案节为本轮之前的历史验收。

---

## 历史追加：完整审美方案驱动搭配（2026-10-23，尚未提交）

用户确认整套审美同时安排配色/轮廓/材质/图案/层次，然后去衣橱找衣服，并明确不要适用场合。规格`aesthetic-outfits-design.md` §20与`ui-contract.md` §20。首版两套完整方案：自然松弛、利落清爽；是公开方法的编辑整理，不代表用户已偏好，也不是模型训练。

- 新`services/aesthetics.py`用同一方案参与单品召回和整套关系评分；每角色12件含探索，基础上装+下装+鞋或连身+鞋，有界加入最多一件外套与一件明确配饰，排除内衣/未知配饰。原缺口分析和组合数公式不改。候选短名单按核心衣服多样化，不再要求颜色/正式度硬不同。
- `recommend.py`旧scene参数保留兼容，新链路强制None，不参与偏好/提示/新记录；occasion/formality偏好索引被排除。模型收到五维目标、JSON单品受控属性及候选五维状态，只能选实际候选编号；编号越界本地补齐，无模型仍应用完整方案。材质成分不猜质感；未知可评分线索按0.5中性进入固定分母，避免缺数据反而高匹配。
- v3迁移仅新增`outfits.aesthetic_snapshot TEXT`，保存方法id/版本/完整目标/评估，旧行NULL不回填；`OutfitOut.aesthetic`同步，推荐/列表/详情一致。schema/models共享改动统一由主agent写入。
- 前端两套审美选择、完整说明和每套五维依据；移除场合和置信度百分比；失败保留旧结果，参数冻结/挂载身份/全局inFlight防跨页串结果和重复请求，历史异步读取不拖生成锁。反馈按钮“换一套”改为准确的“先不选”。来源HTTPS经原生ACTION_VIEW交系统浏览器，返回衣橱仍在原页。SW v25。
- 实际DS样本理由偶有O1代号，追加严格字符串理由与代号过滤，含中文、英文复合代号均回归覆盖；丢弃该条理由不影响候选排序与本地五维依据。

验收：最终 **917 passed / 1 skipped**；smoke **83/0**；对比度 **15/0**；审美浏览器交互 **19/0**（含失败保留/双击/改选/跨页/慢历史锁）；390/768/1440及子页门禁无溢出、44px违规、底栏遮挡、console错误或异常。合成衣橱真实DS4.1通过个人电脑中转 **15.49秒返回3套**，候选都属真实合成衣橱；该试验不证明用户主观审美准确率。模型测试未发送用户真实衣服。

真机8/0：schema3、两套方案/五维展开、无场合、控件与布局、轻点来源转com.android.browser、返回原URL且选择仍在。安装前用户最新状态为 **20件衣服、无未完成草稿**，覆盖安装前后核对相同；不是上轮12件/20草稿的旧基线。v2升级前在手机应用私有目录备份`files/backups/pre-aesthetic-v3-20261023/wardrobe.db`，未导出用户数据库。

最终APK **18,748,510字节**，SHA256 `2a6d1377ee465a639869d7af217b2bbd1410ad4bf9fbef50ba0447bb34b214ed`，26个web文件逐字节一致，审美/schema快照/理由过滤字节码及原生外链dex特征已核对；同签名安装Success，已更新_build/dist及手机Download安装包。电脑中转及账号配置保留，无GLM回退。

证据在本会话可视化目录：`aesthetic-ui-report.json`、`aesthetic-ui-gate-report.json`、`aesthetic-live-report.json`、`phone-aesthetic-report.json`、`apk-aesthetic-verification.json`、`preview-upgrade-aesthetic-{before,after}.json`。两路只读审查发现的未知评分抬高、all季节标签、模型评估输入、生成锁/慢历史、外链及代号问题均已修复；剩余边界是材质/上身比例等原始数据不足，不将其伪装成已知。

---

## 历史追加：个人账号电脑中转（2026-10-23，尚未提交）

用户授权「接入吧」。手机现已切到电脑中转，固定个人CLI的 `deepseek-v4.1-flash`，不回退GLM或直付API。规格见 `desktop-bridge-design.md` §19；原图+裁剪图+选区、词表、冲突留空和逐件确认保持既有契约。

- `scripts/bridge/desktop_bridge.py`：随机配对码认证；固定模型、空工具、严格空MCP、空项目设置、无会话保存；清除子进程API密钥环境；空临时工作目录；双图仅通过stdin传递，不主动落图片或原始模型日志。核验官方登录来源及实际模型。单任务繁忙立即429，收包10秒、CLI75秒上限，Windows命令行长度前置检查。
- `app/services/llm/desktop.py`：手机独立原子配置 `DATA_DIR/desktop-bridge.json`，地址限制局域网IPv4/回环，禁代理和重定向。配置损坏、配对失败、登录失效、积分不足、离线和超时返回安全提示；工厂不回退。文本和视觉调用均走中转，既有直付配置不参与已配对设备的调用。
- `meta.py`：本机Host与Origin校验后才允许连接配置；health不返回配对码。测试连接不发推理、不证明CLI登录仍有效。前端「服务状态→电脑识别设置」，保存只更新状态和识别按钮，不重画编辑表单。旧额度暂停仍需显式点「重新检查服务」，成功识别后解除。
- `api.js` 的识别、图片上传和搭配生成请求统一100秒，覆盖手机中转90秒等待。SW v24，新模块同时进入预缓存与critical，缺模块时保留旧桶。Android原生壳未新增逻辑。

验证：最终全量 **900 passed / 1 skipped**；smoke **83/0**；对比度 **15/0**；设置弹层/草稿保留/SW缺模块自证 **15/0**；三视口390/768/1440及手机子页无横向溢出、触控不达标、底栏遮挡、console errors或exceptions。两路只读审查发现的Host重绑定、请求超时、SW关键清单、焦点、详情按钮刷新、繁忙读取、argv长度及停滞上传问题均修复并复审消除，无待留P2。

真实验证仅使用合成蓝色T恤：正式双图+TAG_SCHEMA识别端到端6.71秒；最终运行版本的手机→Wi-Fi→电脑→个人CLI→DS4.1→手机预览 **6.106秒**，返回 `tshirt/blue`，`applied:false`。无USB reverse，未发送用户商品原图。具体积分扣减数字仍未核定，不能把美元费用0说成免费。

APK **18,732,402字节**，SHA256 `d3d82f11c7eda8512027267e877d92d6e68c63cd04895af59d7e10f5d574b9a0`，26个web文件逐字节一致，desktop/client/meta后端字节码特征验证通过。`adb -s ef473680 install -r` 返回Success；已复制到 `_build/dist/ai-wardrobe-debug.apk` 及手机Download。覆盖安装前后 **12件衣服 / 20张草稿**，草稿/当前原图/裁剪图SHA256均相同。

本机服务：`192.168.110.51:8891`（手机同网）；个人CLI在本会话可视化目录 `workbuddy-vision-probe/personal-native/codebuddy.exe`，凭证由官方正常登录管理，不进APK。配对码仅在该目录 `desktop-bridge-runtime/pairing.json` 和手机应用私有配置中；勿输出到报告或提交。中转启动入口为可视化目录 `启动电脑识别.ps1`，检查源码build并复用同版本已在线服务，隐藏启动服务子进程；当前保持运行，不设开机自启。电脑关机/睡眠/换网会影响识别，衣橱本地操作仍可用。

证据位于同一可视化目录：`desktop-bridge-live-report.json`、`phone-bridge-report.json`、`bridge-ui-report.json`、`bridge-ui-gate-report.json`、`apk-bridge-verification.json`、`preview-upgrade-bridge-{before,after}.json`。新机器需使用自己的官方CLI正常登录和重新配对。

---

## 历史追加：模型选定 DeepSeek V4.1 Flash（2026-10-23）

> 历史记录：本节是接入前的选型。当前部署状态以其上方「个人账号电脑中转」为准。

用户明确「就用ds4.1吧，不用glm了」。后续模型选型固定DeepSeek V4.1 Flash，不配置GLM备用或自动回退。当前项目 `.env` 的文本与视觉模型均为 `deepseek-flash`，供应商为DeepSeek官方；该官方模型名与CodeBuddy CLI的 `deepseek-v4.1-flash` 是不同接入入口的名称，不能混填。

个人账号路线：WorkBuddy内置lite CLI单独调用未取得认证；其后从官方国内安装器所指CDN下载并校验个人CLI **2.173.0**，在会话隔离目录运行，用户通过正常国内账号网页授权。合成蓝色上衣通过个人CLI的DeepSeek V4.1 Flash识图成功，返回 `tshirt/blue`；单次模型处理 **2.434秒**、端到端 **5.47秒**。同图GLM历史对照也正确，不能据1张合成图宣称真实截图准确率排名。旧GLM报告仅作为历史证据，后续试验默认参数与登录入口已改为DeepSeek，不再调用GLM。

用户确认登录昵称及看到了相关记录，但未提供明确积分扣减数字；精确扣分数、单张成本及积分包来源仍未核定，接口美元费用0不等于积分免费。未将用户贴出的疑似密钥写入代码或用于请求；凭证交由官方CLI正常登录管理。

范围：本轮统一模型选择和试验入口，未部署电脑中转服务，手机仍走此前的直接API调用；电脑CLI登录成功并不自动把手机切到WorkBuddy积分。正式接入仍需电脑转发、原图+裁剪图与坐标保留、稳定JSON归一化及真机端到端验证。证据在会话可视化目录 `workbuddy-vision-probe/{personal-install-report,deepseek-probe-report,model-comparison}.json`。未动真实衣橱、手机草稿或API密钥。

---

## 本轮追加：单击裁剪、拖框取景与服务错误（2026-10-23，尚未提交）

用户确认可交互预览后授权接入正式应用，规格见 `ui-contract.md` §18，覆盖前文相关裁剪入口和手势约定。

- 逐件录入单击照片直接裁剪，移除重复裁剪按钮；识别中也能进入，sequence隔离迟到结果。原图与框外遮罩常显，单指拖框、四角调整；松手保持位置。双指按距离围绕起始中点缩放，同向移动不平移图片，抬一指不接拖。保留原图坐标、1600px输出、取消/焦点/ARIA/路由/转屏收尾，新增±与重置及方向键操作。
- LLMError携带安全code/retryable；识别预览仍返回503，typed detail区分额度、网络、超时、鉴权、限流与服务异常，legacy错误仍为字符串。402在能力探测即停止，不缓存为不支持结构化输出。API层区分请求网络与超时，不改变保存的status语义。
- 额度不足暂停整批自动识别并随草稿保存；可裁剪、手填、保存，显式「重新检查服务」成功后恢复。旧402仅在缺少暂停字段时迁移，避免成功恢复被历史错误重新暂停。同图识别失败保留已有AI/手填；成功或实际换裁剪才撤回旧AI，clearAI同步DOM避免异步持久化时旧值回灌；输入法组字时保护当前字段。SW **v23**。
- 两位只读agent按前端几何/服务与导入并行审查，2项P1（恢复后旧错误再次暂停、清理AI时旧DOM回灌）和2项P2（长图遮罩不足、边缘角柄触摸被裁）均修复并复核，无遗留审查缺陷。

验证：**870 passed / 1 skipped**；烟雾 **83通过/0失败**；配色 **15组/0不达标**；隔离临时库+合成照片+假模型的逐件回归 **44/44**、防晒衣留空确认 **20/20**、新裁剪真实触摸与服务暂停/恢复 **23/23**；390/768/1440及手机子页UI门禁无横向溢出、小触控或底栏遮挡，SW/manifest正常，console/exceptions为空。模拟HTTP覆盖402探测只发一次、能力缓存恢复、503错误响应和额度/鉴权/限流/网络/超时分类；未重试真实付费模型，不代表真实额度已经恢复。

干净构建 **BUILD SUCCESSFUL in 34s**、签名验证通过。APK **18,722,766字节**，SHA-256 `3d1fd7d61f132e9d99ec219c6c2cd070637fb9ded8b367f5a34e37c35c0363f4`；包内及手机运行 **25个前端文件/0差异**，已装base.apk与本地产物指纹相同。新机 **ef473680 / Android12 / WebView96.0.4664.104** 同签名覆盖安装 **Success**，产物同步 `_build/dist/ai-wardrobe-debug.apk` 与手机Download同名文件。

真机验收 **20/20**：轻点进入、单指跟手/松手保持、双指同移不平移/1.5倍缩放框不动/残留单指隔离、取消、更多属性后正常流按钮及44px目标。衣橱 **12→12件**，当前 **20张** 草稿的ID/位置/状态/属性/选区/pending摘要与当前原图、裁剪图SHA256在升级与交互前后均一致；未确认裁剪、未新增/保存/跳过用户衣物，模型调用 **0次**。未导出真实截图或图片。手机系统物理返回键仍未自动实按（MIUI拒绝INJECT_EVENTS），本轮未改Android返回逻辑。

证据位于会话可视化目录：`check_crop_service.cjs` / `crop-service-report.json`、`check_photo_import_crop.cjs`、`check_sun_import.cjs`、`phone-crop-report.json`、`apk-crop-verification.json`、`preview-upgrade-crop-{before,after}.json`。交互预览保留 `http://127.0.0.1:8842/`；开发及审查说明不涉及真实账户充值或密钥变更。

---

## 本轮追加：防晒衣独立分类，拿不准留空（2026-10-23，尚未提交）

用户经多agent讨论确认「拿不准就留空」。规格见 `ui-contract.md` §17；本次按已确认方案修改受保护 `taxonomy.py`，未改schema/models/config/main，未迁移旧记录或改写pending快照。

- 外套新增 `sun_protection_jacket / 防晒衣 / outer`，明确别名防晒服、防晒外套；保留夹克/冲锋衣/皮衣，泛化防晒、UPF不作为新类别词。前端品类与精确筛选沿用taxonomy动态生成，基础组合数保持外套可选层，单件搭配可叠加此件。
- 识别规则明确先对应选区中的商品标题/参数，排除其他商品与广告；对应标题明确防晒衣且基本形态相容时选新类别，不能仅因拉链/连帽改判夹克。防晒T恤/帽/裤按实际品类，不猜UPF等级或具体成分。
- type仍为必有字符串键，但拿不准返回空串及conflicts.type原因；后端对缺失/空白/未知品类补待确认原因，屏蔽type/派生slot/length，其他有效属性保留。当前件显示中性「以下属性需确认」，用户改该字段后收起对应原因；不重绘表单干扰输入，手填品类优先。SW **v22**。
- 两位只读agent按后端词表/识别与前端交互并行审查。1项前端P2（手选后迟到AI重新显示品类提示）已修复并复核；真实浏览器覆盖识别中手选与识别前手选两条路径，无遗留P0/P1/P2。

验证：全量pytest **860 passed / 1 skipped**（新增27项分类/未知/CRUD筛选/外套组合与兼容回归）；smoke **83项/0失败**；对比度 **15组/0不达标**；新识别交互 **20/20**；逐件录入回归 **44/44**；三视口及四个手机子页UI门禁无溢出/小触控/遮挡，console/exceptions为空、SW与看门狗正常。自动回归均用系统临时库/合成照片/假响应。

干净构建 **BUILD SUCCESSFUL in 12s**，签名通过；APK **18,733,262字节**，SHA-256 `d729eab5b6972673f598c357545367a2e7cfda97fcef660160f7fba09ecc44c8`。包内/手机运行 **25个前端文件/0差异**；Python pyc含新类别与提示特征，手机 `/taxonomy` 提供防晒衣outer。新机ef473680同签名覆盖安装 **Success**，已装APK指纹相同，安装包同步 `_build/dist/ai-wardrobe-debug.apk` 和新机Download同名文件。

真机Android12/WebView96只读检查 **17/17**，覆盖新类别、现有裁剪双指1.5倍/取消、按钮下移、触控与草稿。另用用户当前件在本机保存的原图+裁剪图，对现有只读 `/tagging/preview` 做1次真实模型验证，返回 **type=raw_type=sun_protection_jacket**、无品类待确认，模型自报confidence 0.95。该数值不是独立准确率指标；仅验证了这一个真实样本。

升级、只读交互与真实识别后，衣橱 **0→0件**，用户20张草稿全部元数据摘要与当前原图/裁剪图指纹保持一致；未导出照片或把模型结果自动写回草稿。当前草稿type仍是旧AI的jacket，`aiOwnsType=true`，没有pending；用户点「重新识别」会撤回旧AI补值再填新结果，也可手选防晒衣。证据：可视化目录 `sun-import-report.json`、`phone-sun-report.json`、`real-sun-recognition-report.json`、`sun-ui-gate-report.json`、`photo-import-no-preview-report.json`、`apk-sun-verification.json`、`preview-upgrade-sun-{before,after}.json`。

---

## 本轮追加：取消独立大图，保留裁剪（2026-10-23，尚未提交）

用户认为「轻点查看大图」与「裁剪商品图」重叠，要求取消前者。当前规格为 `ui-contract.md` §16，下方v20独立预览记录仅作历史。

- 逐件录入恢复静态img，删除提示/按钮/事件、`image-preview.js`、专属CSS及生命周期/导航守卫。Android移除预览专用JS返回事件及500ms回调，恢复原有直接历史回退/退出；保留debug标志判断。SW **v21** 的assets/critical不再引用已删模块，激活清除v20旧桶。
- 「裁剪商品图」、双指缩放、原图+裁剪图识别和手填保护保留；保存/跳过仍跟在更多属性后普通流，撤销条/toast避让保留。审查发现原有queued读取恢复后取消裁剪仍可能识别的P2，crop()用本次确认成功标记限制重识别，取消不改照片/选区/属性、不调用模型。
- 两个只读agent按壳/缓存与前端业务/样式并行审查；P2修复后复核，无遗留P0/P1/P2。构建门禁正确拒绝已删模块在快照中的残留；仅清除 `C:/wbuild/aiw/app/src/main/python/web/js/image-preview.js` 后重建。

验证：隔离临时库/合成照片/假模型 **44/44**，包括单击双击不打开大图、取消裁剪无副作用、queued读取恢复后取消不识别、真实双指确认裁剪、逐件保存/重试/撤销/刷新恢复；三视口及手机子页UI门禁通过，console/exceptions为空、SW与看门狗正常；对比度 **15组/0不达标**。本轮未改Python业务，最近基线仍为 **833 passed / 1 skipped**。

干净构建 **BUILD SUCCESSFUL in 25s**，签名验证通过，APK **18,732,462字节**，SHA-256 `e65c56f5703dd6508021b9d5190c829cdc3aead291b06a15131f1d6740dffaac`。包内及手机运行的 **25个前端文件/0差异**，包内无预览模块、DEX无预览返回事件。新机ef473680同签名覆盖安装 **Success**；产物同步 `_build/dist/ai-wardrobe-debug.apk` 及新机Download同名文件。

新手机Android12/WebView96只读验收 **14/14**：v21激活/v20清理、无大图入口及轻点行为、裁剪打开/真实双指1.5倍/取消、表单按钮随更多属性下移、44px触控、0次识别与无异常。升级及交互前后衣橱 **0→0件**、当前 **20张** 草稿的全部元数据摘要及当前原图/裁剪图指纹一致；保留用户当前录入页，未保存/跳过用户衣物、未导出真实图片。证据见可视化目录 `photo-import-no-preview-report.json`、`phone-no-preview-report.json`、`no-preview-ui-gate-report.json`、`apk-no-preview-verification.json`、`preview-upgrade-nopreview-before.json`。

---

## 本轮追加：轻点大图与操作随表单下移（2026-10-23，尚未提交）

用户确认效果稿并授权开发。规格见 `ui-contract.md` §16；沿用A版极简/蓝色玻璃导航。

- 新增 `web/js/image-preview.js`，逐件录入的商品缩略图改为可轻点/键盘开启的按钮。全屏只读预览提供商品图/原图切换、双击还原/放大、双指以中点缩放、拖动边界、滚轮与±/还原按钮。独立本机Blob URL，不调用模型、不改图片或属性。关闭恢复ARIA/焦点/滚动；普通AI属性重绘保留浮层，实际自动换裁剪图前关闭旧预览。最大5倍，残留单指不误拖，切图/转屏/取消收尾手势。
- 手机端移除固定 `.import-actions` 和120px额外留白，跳过/保存跟在更多属性后普通流；撤销提示避开玻璃导航，toast在撤销条存在时上移44px+间距，显式状态类兼容WV96。识别状态文字不拦截图片轻点。
- Android返回先分发可取消 `wardrobe-back`；无预览沿用历史/退出。500ms兜底、同步异常处理及单次标记防JS无响应或迟到回调重复返回。新模块加入SW预缓存与critical清单，版本 **v20**。
- 按协作文档，两位只读agent并行审查、修后复核。1项P1（原生返回依赖JS）、2项P2（迟到自动裁剪的旧预览、toast遮挡撤销）均修复，无遗留审查问题。

验收：临时库/合成图片/假模型的预览交互 **37/37**；缺少structuredClone的逐件录入回归 **40/40**；三视口及手机子页UI门禁无溢出/小触控/遮挡，console/exceptions为空、SW激活、看门狗正常；文字对比度 **15组/0不达标**。新手机 **ef473680 / Android12 / WebView96.0.4664.104** 的现有草稿只读检查 **17/17**，覆盖真实CDP双指1.75倍、残留手指、双击、切图、返回取消事件、操作下移、焦点与草稿一致，识别请求0次。本轮未改Python业务，最近业务回归基线仍为 **833 passed / 1 skipped**。

干净构建 **BUILD SUCCESSFUL in 24s**，签名验证通过。APK **18,737,458字节**，SHA-256 `37a54ab996b3edb2c9d9609aa41c5c1b13f79015b99448759b6c93430cb141fb`；包内及手机服务 **26个前端文件/0差异**，DEX含返回事件；`adb -s ef473680 install -r`返回 **Success**，手机已装APK指纹相同。产物同步 `_build/dist/ai-wardrobe-debug.apk` 和新机Download同名文件。

升级前后衣橱 **0→0件**，用户当前批次 **20张** 的草稿ID、位置、全部属性/选区/状态/待定提交元数据摘要一致；当前原图和裁剪图SHA-256均一致。真机交互后再次核对相同；未新增/跳过/保存任何用户衣物。保持在用户当前录入页。证据：可视化目录的 `image-preview-report.json`、`phone-preview-report.json`、`preview-upgrade-{before,after}.json`、`preview-ui-gate-report.json`、`photo-import-browser-report.json`、`apk-verification.json`。真机未导出含个人信息的原图截图。

限制：MIUI拒绝shell注入系统按键（`INJECT_EVENTS`），**系统物理返回键未自动实按**；同一取消事件的WebView消费、原生分发/超时路径已静态复核并编译验证。防晒衣归为夹克的品类词表问题没有在本轮修改。

---

## 本轮追加：新手机安装与旧WebView兼容（2026-10-23，尚未提交）

用户更换手机并要求安装。当前新机 **M2101K9C / ef473680 / Android12 / arm64 / WebView96.0.4664.104**。首次USB安装返回 `INSTALL_FAILED_USER_RESTRICTED`，传APK到Downloads并打开文件管理后，用户明确回复「我已手动安装」。之后同签名覆盖安装返回 **Success**。

- 真机确认 `typeof structuredClone === "undefined"`、`:has` 不支持。将JSON草稿/属性拷贝集中为`import-store.copyData`；File仍独立存IndexedDB，不经JSON。`photo-import`复用该拷贝，不再依赖structuredClone。
- 显式按`ApplicationInfo.FLAG_DEBUGGABLE`开启/关闭WebView调试，旧组件debug包可验收，正式包关闭。复选标签加兼容类维持44px；toast使用main的现有import-view类与兄弟选择器，避开保存栏。SW **v19**。触控门禁按真实控件找最近label，去除自身对`:has`的依赖。
- 两个只读agent按原生/前端复核；2项旧组件CSS P2（toast遮挡、复选触控）均修复复核，无剩余P0/P1/P2。

验证：缺少structuredClone的浏览器逐件流程 **40/40**；新机实际WebView **15/15**（多图/手填隔离/撤销/刷新恢复File/提示避让/展开筛选与搭配复选触控/坏样本门禁自证/无异常）。测试用合成照片和假识别，未调用外部模型，合成草稿已清理，衣橱 **0→0件**。常规三视口、手机子页、SW、看门狗门禁通过；文字对比度 **15组/0不达标**。本轮不改Python业务，最近业务回归仍为下节 **833 passed / 1 skipped**。

干净构建 **BUILD SUCCESSFUL in 11s**，APK **18,732,122字节**，SHA-256 `205584288d578a9bb04e5b062cc0361993ed2128fd4370dde6c7639786f85d26`。签名通过、包内/手机服务的 **25个前端文件/0差异**，手机已装文件指纹一致。兼容包已同步到 `_build/dist/ai-wardrobe-debug.apk` 及新手机 `Download/ai-wardrobe-debug.apk`。新机393×819布局、72px玻璃导航通过，缓存v19；最终截图`phone-new-wardrobe.png`，兼容能力及15项检查见`phone-import-report.json`，桌面门禁`legacy-ui-gate-report.json`。真实商品识别准确率与原生选择器实际返回多URI仍未专项验证。

---

## 本轮追加：多选照片、逐件录入（2026-10-23，尚未提交）

用户确认交互稿：一次多选照片/商品截图，每张一件；直接录第1件，逐件确认保存后才切下一件，不设集中检查页。规格见 `ui-contract.md` §14。

- `photo-import.js` 接管照片选择，保留原文案批量和无图手填入口。每批最多20张，可追加；每件独立保存原图、裁剪图、归一化选区、手填属性及AI补值归属。当前件自动识别/建议裁剪，手工重裁仍支持双指缩放与双图识别；保存只提交确认后的图片和属性，不再调用视觉模型。
- IndexedDB 的 metadata/photos 分开存储，表单修改排队持久化；支持暂停、切页、刷新恢复。图片事务失败后重放 put/remove/clear，避免后续只写属性就误报草稿已存。不可用时明确提示并保留内存队列。
- 单件 POST `/items/import-photo` 在 `BEGIN IMMEDIATE` 中创建记录并安装图片，稳定token核对重试；GET `/items/import-photo/{token}` 只查状态。未知结果冻结提交快照，重试/刷新可查到已保存项；重复照片明确拒绝，不留空行。每次尝试使用 `<id>.import-<32位hex>.<ext>` 独立图片名，旧尝试回滚清理不会删并发重试的新图；换图与删除兼容清理。不改schema和共享模型。
- 跳过可在6秒内撤销，最后一件的结束页也保留撤销入口与照片；到期/明确离开后释放剩余照片。完成态停止接收粘贴/拖入。路由入口显式调用守卫，保存期间阻止离开；AI返回时恢复焦点/光标，中文组合输入期间延后整页重绘。SW **v18** 缓存两新模块并纳入激活完整性检查。
- 两个只读agent按后端/前端审查：4项P1（回滚清理竞态、草稿图片补存、最后跳过撤销、完成态追加）与2项P2（焦点/IME、空hash返回）均修复复核；真浏览器另发现并修正hashchange监听顺序。无剩余审查项。

实测：`pytest -q` **833 passed / 1 skipped**（Windows符号链接既有跳过）；smoke **83项 / 0失败**；对比度 **15组 / 0不达标**。新增单件提交测试 **16/16**，含并发幂等、回滚释放锁后重试与旧清理交错、磁盘失败、重复图、换图/删除清理。Chrome逐件流程 **40/40**，含390/768/1440录入布局、手机全展开字段无保存栏遮挡、双指、人工保护、跳过撤销、丢响应刷新核对、草稿失败补存、中文组合输入与完成态清理；常规三视口UI/子页/看门狗/SW门禁通过，console和exceptions为空。

干净构建 **BUILD SUCCESSFUL in 11s**；APK **18,731,814字节**，SHA-256 `aa3336af072b96dc2eed3f2c963b345efd09c6e7129a3bc8cf7253567b28a81a`。签名通过，包内与手机运行的 **25个前端文件 / 0差异**，后端新服务特征已核对；新包同步到 `_build/dist/ai-wardrobe-debug.apk`。小米12S Ultra覆盖安装 **Success**，安装文件指纹一致，缓存v18；视口412×876、72px玻璃导航、左右16px/底12px和触控门禁通过，照片input的multiple为true。

手机WebView合成照片验收 **9/9**：双文件逐件、手填隔离、撤销、刷新恢复真实IndexedDB中的File、触控、清理及无异常。假识别未调用外部模型，测试草稿已清理，真实衣橱 **0件→0件**。原生 `GET_CONTENT` 选择器实际启动到 `PhotoPickerGetContentActivity` 后取消返回，未选择个人相册照片；**原生选择器实际返回多URI的物理选取与真实商品识别准确率未验证**。

证据在本次可视化目录的 `photo-import-browser-report.json`、`import-ui-gate-report.json`、`phone-import-report.json`、`native-picker-report.json`、`apk-verification.json`；8809预览服务已用新版后端重启，仍为隔离示例衣橱（未配置真实模型）。

---

## 本轮追加：C 款上衣 Logo 定稿与装机（2026-10-22，尚未提交）

用户选择 **C 上衣**，确认保留原配色（亮蓝 `#0066cc` / 柔白 `#f8fbff` / 深蓝 `#11345f`），采用新形态（完整肩袖、稍宽衣身、6° 倾斜、右下探出），规格见 `ui-contract.md` §13。

- SVG 母版：`web/icons/logo.svg`。`scripts/assets/generate_icons.js`（Node ≥22 + Chrome CDP）从同一母版导出 15 张 PNG、Android 彩色 / 单色矢量、26+ / 33+ 自适应图标；无外部图像依赖。单色版通过 evenOdd 镂空眼睛和嘴巴。
- 首页和微信绑定页使用相同 SVG / PNG favicon，PWA 与 Apple touch 图标同步替换。SW **v17** 将所有图标预缓存，并在图标不完整时保留旧桶。
- Manifest 绑定普通与圆形 launcher icon；构建脚本同步整个 `res/` 并逐字节核对。108dp 自适应前景以中央72dp区域映射母版，预览圆角不烘焙进原生前景。
- 两个只读 agent 按原生构建 / 网页视觉拆分审查，4 项 P2（Node 声明、清理异常覆盖、图标缓存完整性、绑定页旧 favicon）均修复并复核，无剩余 P0/P1/P2。

本轮实测：图标 **13/13**（PNG 尺寸、3 主色、满幅背景、五官安全区、单色镂空、母版路径一致、favicon 与离线读取）；缓存激活 **9/9**（缺任一图标 / manifest / JS 保留旧桶，完整后清理旧桶）；三视口及子页 UI 门禁无溢出、小触控或遮挡，console / exceptions 为空；对比度 **15 组 / 0 不达标**。本轮未改 Python 业务，最近业务回归基线仍见下节 **817 passed / 1 skipped**。

`sh android/build_apk.sh clean --offline --console=plain` 输出 **BUILD SUCCESSFUL in 15s**；APK **18,714,454 字节**，SHA-256 `9a3d91859f89f29b71f7bae0a58a937cf713dc6b89b346e827ca5759cc327d83`。签名验证通过，APK 包含 16 个 launcher 图层/图标资源，aapt2 证实 application icon 指向自适应资源；包内及手机运行的 **23 个前端文件 / 0 差异**。

小米 12S Ultra `adb install -r` 返回 **Success**，安装文件指纹一致，系统应用信息页实拍显示新上衣图标。安装前即时健康检查为 **0 件**，安装后仍 **0 件**，本轮未调用增删单品接口；服务 healthy，LLM / vision 配置均可用。运行缓存 v17，412×876 下布局与触控门禁通过。新版同步到 `_build/dist/ai-wardrobe-debug.apk`，临时 ADB 转发已移除。

---

## 本轮追加：双图识别与双指裁剪（2026-10-22，尚未提交）

用户授权将裁剪商品图与原始截图共同用于识别，并增加手机双指缩放；约定见 `ui-contract.md` §12。

- 录入页本次会话保留原始 File 和原图选区；预览、保存均把当前裁剪图作为图1、原图作为图2。参考原图写系统临时目录，请求结束后清理；衣橱只保存当前图，不改 schema。
- 自动建议裁剪在用户确认后重新以双图识别，再填属性。换图/重裁撤回未编辑的 AI 补值，保留手填字段。模型报告的冲突字段不自动填入，录入页、保存结果与详情页均显示原因。
- 双指以中点缩放图片，屏幕选框保持固定；残留单指不继续拖动。确认时冻结选区，避免异步编码后图片与选区元数据错位；离开录入页释放原图及预览 URL。
- 历史单品没有原始截图时继续单图识别；详情页再次裁剪只能使用目前保存的照片，无法恢复首次录入的原截图。
- 两个只读 agent 按前后端并行审查，三项后端 P1、一项裁剪 P1 与两项前端 P2 均修复并复核；回归用例已改用 `jeans + nine_tenth` 真正覆盖品类冲突的裤长依赖。
- 三款 SVG 图标草稿及人格化细化稿已给用户效果图；用户随后选择 C 原配色与新形态，定稿与装机记录见上节。

验证输出：`pytest -q` **817 passed / 1 skipped**；smoke **83 项 / 0 失败**；对比度 **15 组 / 0 不达标**；三视口 UI 门禁无溢出、小触控或遮挡，console / exceptions 为空。双图与真实 CDP 触摸流程 **21 passed / 0 failed**，覆盖图像字节顺序、目标坐标、仅持久化裁剪图、手填保护、冲突展示、二次异常响应、离页清理与导出冻结。以上模型输出使用假模型；**真实商品的识别准确率未验证**。

构建输出 **BUILD SUCCESSFUL in 12s**；新版 APK **18,612,282 字节**，SHA-256 `503d6b61532eb062939e4b7cbdaab2345c9bff9d23682e6480ff81285b195a3f`。`adb install -r` 返回 **Success**，手机安装文件指纹一致；APK 及运行服务的 **21 个前端文件 / 0 差异**，SW **v16**。签名验证通过，新包同步到 `_build/dist/ai-wardrobe-debug.apk`。

小米 12S Ultra 实际 WebView 的临时画布检查 **6/6**：1.5 倍双指缩放、屏幕选框固定、残留单指不跳、输出选区一致、240×320 JPEG 有效、衣橱仍 **1 件 → 1 件**。此测试通过 CDP 派发触摸事件，没有上传测试图片、调用模型或保存测试单品。手机旧进程曾健康检查超时，重启旧应用后恢复，再进行覆盖安装；后台长时间运行稳定性未专项验证。

---

## 本轮工作区：A 版前端（2026-10-22，尚未提交）

用户选择「清冷科技感」并授权实施，视觉约定已写入 `ui-contract.md` §11。

- 冷白 / 黑灰 / 蓝色；手机底栏改为 72px 悬浮磨砂玻璃胶囊，图标配文字，保留四入口顺序。
- 衣橱照片卡片简化，搜索常驻，完整筛选可展开；卡片使用可键盘访问的链接。
- 照片录入优先；商品文案、更多属性和推荐选项按需展开，原有字段与接口保留。
- 搭配展示真实单品照片，缺图用占位；顶部模型信息收进状态入口。SW 已升到 **v15**。
- 主对话写入，两个只读 agent 按 CSS / 交互拆分审查；发现的分页 toast 导入遗漏、summary 触控尺寸、反馈选中样式均已修并复核。

本轮实测：`pytest -q` **794 passed / 1 skipped**，smoke **83 项 / 0 失败**，对比度 **15 组 / 0 不达标**；全部前端 JS `node --check` 通过。
`cdp_ui_check.js` 的 390 / 768 / 1440 三视口、手机子页触控与末尾遮挡、SW、看门狗均通过，console / exceptions 为空。
隔离临时库的额外浏览器交互验收 **57 passed / 0 failed**，含 320–1440 全页面溢出、分页失败恢复、折叠字段保值、原生选图、取消/确认裁剪、PNG 保存、再录一件、推荐与反馈。玻璃导航透过纯黑底的文字对比度实测 **7.53:1**。

**范围**：上述交互用例在桌面 Chrome 的隔离临时库里验证，没有改真实衣橱，没有调用外部模型。

### A 版装机验收（2026-10-22）

用户授权后，执行 `sh android/build_apk.sh clean --offline --console=plain`，输出 **BUILD SUCCESSFUL in 12s**。首次增量包有约 0.4MB 旧资产空隙，clean 重打后间隙合计 **0 字节**。

- APK **18,605,734 字节**；SHA-256：`947b3de90f4f66d500793b668b1e0a39b37d0fb21a38f4ee41dd1bd6027e13bb`。
- 解包 `assets/chaquopy/app.imy`，前端 **21 个文件逐字节一致，0 差异**；签名验证通过。
- 对小米 12S Ultra（`3b502e83`）执行 `adb install -r`，返回 **Success**；更新后重启应用。
- 真机健康状态 `healthy`，衣橱 **1 件 → 1 件**，`llm_ready / vision_ready` 均为 true。
- 手机实际安装的 APK 指纹与产物一致；运行中服务提供的 **21 个前端文件指纹一致**，缓存桶为 `wzbbti-shell-v15` / `wzbbti-img-v15`。
- WebView 实际视口 **412×876**；底栏 **72px**、左右 **16px**、距底部 **12px**，背景模糊为 `saturate(1.5) blur(24px)`。
- 真机衣橱、录入页均无横向溢出，触控目标无小于 44px，页面末尾遮挡 **0px**。已留真机截图；文件选择器的物理点击仍需用户手动确认，本轮未调用识别或写入真实衣橱。
- 新包同步到既有 `_build/dist/ai-wardrobe-debug.apk`；下方历史装机记录属于上一版。

---

## 一、现在在哪（已完成、已推送）

| 能力 | 状态 | 关键文件 |
|---|---|---|
| 衣橱 / 搭配 / 缺口 / 录入 四个页面 | ✅ | `web/js/view-*.js` |
| 单品详情页（改属性 / 换图 / 裁剪 / 删除） | ✅ | `web/js/view-item.js` |
| 前端设计系统（Apple 风 + token 化） | ✅ | `docs/design/ui-contract.md` · `web/css/base/tokens.css` |
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
.venv/Scripts/python scripts/checks/check_contrast.py                    # 15 组 0 不达标
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
| ③ HTTP 缓存 | 覆盖安装后手机上**长期跑旧 js**：`networkFirst` 的 `fetch()` 走浏览器 HTTP 磁盘缓存，普通刷新、甚至 SW 的 network-first 都拿副本 | 实测：清缓存前页面里 `window.__wz` 不存在，而 `fetch('/js/core/app.js')` 返回新版；`Network.clearBrowserCache` 后立刻正常 |

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
| **删除必须连图一起删** | 常规图片按 `<item_id>.<ext>`，§14原子录入使用 `<item_id>.import-<32位hex>.<ext>` 独立尝试名，`images.remove_item_images`兼容清理。新增删除路径必须经过 `items.delete()` |
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
