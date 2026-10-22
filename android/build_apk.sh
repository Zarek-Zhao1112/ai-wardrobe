#!/bin/sh
# 重新构建 AI 穿搭助手的 debug APK。
#
# 用法：sh android/build_apk.sh
#       sh android/build_apk.sh --info          # 额外参数原样透传给 gradle
#
# ## 这个脚本解决什么问题
#
# 构建目录（默认 C:/wbuild/aiw）**不是源码**，而是「每次从仓库铺一遍」的产物区。
# 铺之前它是空的也无所谓 —— 所有东西都能从仓库重建。这解决了两类事故：
#
#   ① 源码改了、装上去还是旧界面。APK 里是**拷贝**不是链接，漏一次同步就会
#      打出旧包，而且**构建日志一切正常**，唯一判据是解包对比。
#      → 同步写进脚本，并在打包前断言关键改动确实进了快照（2026-10-22 P0）。
#
#   ② shell 侧手工维护、仓库里没有，换台机器就重建不出来。
#      → Gradle 工程骨架、自制的 pydantic_core wheel、server.py 全部由本脚本
#        从仓库 android/ 铺过去。
#
# ## 为什么不在仓库目录里直接跑 Gradle
#
# 仓库路径含中文（C:/Users/MCXS/工作流打通/ai衣橱/ai-wardrobe）。Gradle 和
# Chaquopy 在 Windows 上处理中文路径会出转义问题，所以统一铺到纯 ASCII 短路径
# C:/wbuild/aiw 去构建。想看别的目录：AIW_BUILD_DIR=D:/x sh android/build_apk.sh
set -e

HERE=$(cd "$(dirname "$0")" && pwd)          # <repo>/android
REPO=$(cd "$HERE/.." && pwd)                 # <repo>
PROJ="${AIW_BUILD_DIR:-C:/wbuild/aiw}"       # 纯 ASCII 短路径，见上
PY="$PROJ/app/src/main/python"

# 本机工具链：不在仓库里（jdk / android-sdk / gradle / local-maven 合计几百 MB）
TOOLS="${AIW_TOOLCHAIN:-C:/Users/MCXS/工作流打通/ai衣橱/_build}"
JDK="$TOOLS/jdk"
GRADLE="$TOOLS/gradle/bin/gradle.bat"
# android-sdk 实际在中文路径下，C:/wbuild/sdk 是指向它的 ASCII 符号链接。
# 用 ASCII 那个：local.properties 里也是它，两处必须一致。
SDK="C:/wbuild/sdk"

for p in "$JDK" "$GRADLE" "$SDK"; do
  [ -e "$p" ] || { echo "  ✗ 工具链缺失：$p"; echo "    见 android/README.md「工具链」一节"; exit 1; }
done

echo "=== 1/4 铺 Gradle 工程骨架（仓库 android/ → $PROJ） ==="
mkdir -p "$PROJ/app/src/main/java/com/zarek/wardrobe" \
         "$PROJ/app/src/main/res/values" \
         "$PROJ/wheels" "$PY"
cp "$HERE/settings.gradle" "$HERE/build.gradle" "$HERE/gradle.properties" "$PROJ/"
cp "$HERE/app/build.gradle" "$PROJ/app/"
cp "$HERE/app/src/main/AndroidManifest.xml" "$PROJ/app/src/main/"
cp "$HERE/app/src/main/java/com/zarek/wardrobe/"*.java \
   "$PROJ/app/src/main/java/com/zarek/wardrobe/"
cp "$HERE/app/src/main/res/values/strings.xml" "$PROJ/app/src/main/res/values/"
cp "$HERE/app/src/main/python/server.py" "$PY/"
cp -r "$HERE/wheels/." "$PROJ/wheels/"

# local.properties 不随仓库走（含本机绝对路径，Android 标准做法也是 gitignore）。
# SDK 路径用 ASCII 符号链接，避开中文路径转义问题。
printf 'sdk.dir=C\\:\\\\wbuild\\\\sdk\n' > "$PROJ/local.properties"

echo "=== 2/4 同步 web/ 与 app/ ==="
# ⚠️ 不要 `rm -rf` 整个目录：宿主的批量删除保护会拦（98 个文件 > 阈值 50），
#    而且在构建目录里做整目录删除本身也没必要 —— 覆盖写就够了。
#    源里**删掉**的文件不会被这条清掉，但下面的 `diff -r` 会把它报成
#    "只在目标存在" → 脚本停下，由人决定是删还是留。宁可停一次，也别静默带上废弃文件。
mkdir -p "$PY/web" "$PY/app"
cp -r "$REPO/web/." "$PY/web/"
cp -r "$REPO/app/." "$PY/app/"
# 清字节码：`cp -r` 会把宿主机上一轮的 .pyc 一起带进包，而 Python 只要发现
# 同目录有 magic number 匹配的 pyc 就**直接用旧的**，源码改了也不生效。
# 这一步必须做，且只删 pyc/__pycache__（构建产物，不是源码）。
find "$PY" -name "*.pyc" -delete 2>/dev/null || true
find "$PY" -name "__pycache__" -type d -empty -delete 2>/dev/null || true

# .env：APK 里也要有，否则视觉打标和 LLM 排序全哑，而且**报错很不像配置问题**。
# 这一步之前是手工 cp 的 —— 构建目录一丢就打不出能用的包，所以写进脚本。
# ⚠️ 这会把 API key 打进 APK。自己的手机自己用可以接受；
#    要给别人装，先想清楚这件事（见 android/README.md）。
if [ -f "$REPO/.env" ]; then
  cp "$REPO/.env" "$PY/.env"
else
  echo "  ! 仓库根没有 .env —— 打出来的包不会有模型配置"
fi

echo "=== 3/4 校验快照（改了没同步，就在这里停） ==="
if ! diff -r "$REPO/web" "$PY/web" >/dev/null; then
  echo "  ✗ web/ 与快照不一致："
  diff -r "$REPO/web" "$PY/web" | head -20
  exit 1
fi
echo "  ✓ web/ 逐字节一致"
if ! diff -r --exclude=__pycache__ "$REPO/app" "$PY/app" >/dev/null; then
  echo "  ✗ app/ 与快照不一致："
  diff -r --exclude=__pycache__ "$REPO/app" "$PY/app" | head -20
  exit 1
fi
echo "  ✓ app/ 逐字节一致"

# Java / Gradle 骨架也要确认真的铺过去了（cp 成功但内容旧 = 同样的坑）
for f in \
  app/src/main/java/com/zarek/wardrobe/MainActivity.java \
  app/src/main/java/com/zarek/wardrobe/WardrobeApp.java \
  app/src/main/java/com/zarek/wardrobe/WardrobeService.java \
  app/src/main/AndroidManifest.xml \
  app/src/main/python/server.py \
  settings.gradle build.gradle gradle.properties
do
  cmp -s "$HERE/$f" "$PROJ/$f" || { echo "  ✗ 骨架未同步：$f"; exit 1; }
done
echo "  ✓ 骨架逐字节一致"

# wheels 单独比一次：仓库里**删掉**某个 wheel（比如换版本）时，构建目录会留着旧的，
# 而 Chaquopy 从 --find-links 里挑，可能挑中那个废弃版本 —— 打出来的包依赖版本不对。
if ! diff -r "$HERE/wheels" "$PROJ/wheels" >/dev/null; then
  echo "  ✗ wheels 与仓库不一致（仓库里删过 wheel？构建目录还留着旧的）："
  diff -r "$HERE/wheels" "$PROJ/wheels" | head -10
  echo "    → 删掉构建目录里多出来的那个 .whl 再跑"
  exit 1
fi
echo "  ✓ wheels 一致"

echo "=== 4/4 特征串（挡「同步了一份本身就不是新版的东西」） ==="
grep -q 'window.__wz' "$PY/web/js/app.js" \
  || { echo "  ✗ app.js 里没有 window.__wz（看门狗状态协议）—— 前端不是新版"; exit 1; }
grep -q 'wd-hard' "$PY/web/index.html" \
  || { echo "  ✗ index.html 里没有 wd-hard（清缓存重载）—— 前端不是新版"; exit 1; }
grep -q 'onConsoleMessage' "$PROJ/app/src/main/java/com/zarek/wardrobe/MainActivity.java" \
  || { echo "  ✗ 壳里没有 onConsoleMessage（JS 日志转发）—— Java 不是新版"; exit 1; }
# 文件选择器：没有 onShowFileChooser 的壳，input[type=file] 点了**完全无反应**，
# 而且前端侧一点报错都没有 —— 这个洞踩过两次，必须钉住。
grep -q 'onShowFileChooser' "$PROJ/app/src/main/java/com/zarek/wardrobe/MainActivity.java" \
  || { echo "  ✗ 壳里没有 onShowFileChooser —— 选照片按钮会点不动"; exit 1; }
echo "  ✓ 特征串齐全"

export JAVA_HOME="$JDK"
export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"
unset PYTHONPATH
unset PYTHONHOME

cd "$PROJ"
# `clean` 只是为了让增量打包留下的死空间消失，不是常规操作。
# ★ 什么时候需要：APK **莫名其妙变大**，而内容没变多。
#   实测（2026-10-22）：.env 变了 157 字节 → Chaquopy 的 requirements-common.imy
#   跟着变 → AGP 做增量打包时把新版本追加到别处，**旧版本 5.1 MB 变成死空间**
#   留在包里（19.0 MB → 24.1 MB）。解包看条目一模一样，只有条目之间的偏移能看出来
#   —— 判据：`python android/tools/apk_diff.py old.apk new.apk` 报出「间隙 > 1KB」。
#   一次 clean 就干净了。
if [ "$1" = "clean" ]; then
  shift
  "$GRADLE" clean assembleDebug "$@"
else
  "$GRADLE" assembleDebug "$@"
fi

echo
echo "=== 产物 ==="
ls -la "$PROJ/app/build/outputs/apk/debug/app-debug.apk"
echo
echo "装机：adb install -r \"$PROJ/app/build/outputs/apk/debug/app-debug.apk\""
echo "      ⚠️ 参数必须是本地路径；写成 /sdcard/... 会报 failed to stat（Windows 版 adb 当成本地文件）"
echo "      （同签名覆盖安装能直接 Success，MIUI 不拦；全新装才需要手点 APK）"
