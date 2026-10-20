#!/data/data/com.termux/files/usr/bin/sh
# ── 手机端同步脚本（在电脑上生成，推到手机执行）────────────────────────────
#
# 用法（在 Termux 里）：
#   sh sync-termux.sh                    # 用 /sdcard/Download/wzbsync.zip
#   sh sync-termux.sh /path/to/x.zip     # 指定包
#
# 设计要点（都是踩过的）：
# ◆ **重启要优先走 `sv restart`**。用手写 `pkill + nohup` 会绕过 termux-services
#   托管：sv 发现自己托管的进程没了会立刻重启一份，你再 nohup 起一份 → 端口冲突，
#   新的起不来 → 实际跑的还是**解压前**那份旧代码，而界面看起来一切正常。
# ◆ 只解压、**从不删除**：`.env`、`data/`（含数据库）、`deploy/` 都不在包里，原样保留。
# ◆ 最后必须**验前端版本**：只查 `/health` 的 `schema_version` 不够 ——
#   两个不同版本的代码可能有同一个 schema 常量，区分不出来。
#   要挑一个"只有新版才有的标识"来查（见下面 grep 那一行）。
set -e

REPO="${HOME:-/data/data/com.termux/files/home}/wzbbti"
ZIP="${1:-/sdcard/Download/wzbsync.zip}"
MARK="bindClipboardOnce"   # ← 每次发版改这一个标记，就能确认"真的换了"

cd "$REPO" 2>/dev/null || { echo "✗ 找不到 $REPO"; exit 1; }

say() { echo ""; echo "── $* ──"; }

say "1/5 检查包"
[ -f "$ZIP" ] || { echo "✗ 找不到 $ZIP"; exit 1; }
echo "  $ZIP ($(wc -c < "$ZIP") 字节)"

say "2/5 解压覆盖（不动 .env / data）"
python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall('.')" "$ZIP" \
  || { echo "✗ 解压失败（/sdcard 可能没权限）"; exit 1; }
echo "  ✓ 已解压"

say "3/5 确认文件是新的"
if grep -q "$MARK" web/js/view-add.js; then
  echo "  ✓ 前端含新标记 $MARK"
else
  echo "  ✗ 前端没有 $MARK —— 包是旧的？"
  exit 1
fi
python -c "import app.schema as s; print('  SCHEMA_VERSION =', s.SCHEMA_VERSION)"

say "4/5 重启服务"
# ★ 优先 sv（保持托管关系）；没有就退回手动
if command -v sv >/dev/null 2>&1 && sv status wzbbti 2>/dev/null | grep -q "run:"; then
  sv restart wzbbti
  sleep 3
  echo "  ✓ 已用 sv restart（托管未被打断）"
  sv status wzbbti || true
else
  echo "  · 没有 sv 托管，改用手动方式"
  pkill -f "python run.py" 2>/dev/null || true
  sleep 1
  nohup python run.py >> wzbbti-run.log 2>&1 &
  sleep 6
  echo "  ✓ 已启动（日志 wzbbti-run.log）"
fi

say "5/5 健康检查"
python - <<'PY' || { echo "  ✗ 服务没起来，看 wzbbti-run.log"; exit 1; }
import json, urllib.request
h = json.load(urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=10))
print("  status      =", h["status"])
print("  schema      =", h["schema_version"])
print("  衣橱        =", h["item_count"], "件")
print("  LLM / 视觉  =", h["llm_ready"], "/", h["vision_ready"])
PY

# ★ 关键：**问服务要一份前端资源**，确认服务返回的也是新版。
#   只看文件没用 —— 文件新、跑着的进程旧，是最常见的坑。
python - <<'PY'
import urllib.request
src = urllib.request.urlopen("http://127.0.0.1:8787/js/view-add.js", timeout=10).read().decode()
print("  服务返回的前端含新标记:", "bindClipboardOnce" in src)
print("  （这一项才是\"真的换了版本\"的证据）")
PY

echo ""
echo "── 完成 ──"
