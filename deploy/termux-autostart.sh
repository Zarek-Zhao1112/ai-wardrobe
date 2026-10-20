#!/data/data/com.termux/files/usr/bin/sh
# ── 让手机开机就自动把服务跑起来（配一次，之后点 PWA 图标就能用）────────────
#
# 为什么需要它：`termux-setup.sh` 已经把服务注册成 sv 托管（崩了自动重启），
# 但**Termux 自己不会开机自启** —— 手机重启后 Termux 没起来，sv 也就没起来，
# 那时点 PWA 图标只能看到浏览器报错（或者 SW 回落的旧缓存）。
#
# 这个脚本写一个 `~/.termux/boot/` 钩子，由 Termux:Boot 在开机时执行。
#
# 前置（**只能手动做，脚本做不了**）：
#   1. 装 Termux:Boot（同一来源，F-Droid 或 GitHub）
#   2. **打开一次 Termux:Boot**，点「允许自启」—— 没打开过一次就不会生效
#   3. 设置 → 应用 → Termux → 电池 → 选「不限制」（否则系统会杀后台）
#
# 幂等：可以反复跑。
set -e

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
REPO="${HOME:-/data/data/com.termux/files/home}/wzbbti"
BOOTDIR="$HOME/.termux/boot"

say() { echo ""; echo "── $* ──"; }

say "1/4 检查前置"
if [ -d "$REPO" ]; then echo "  ✓ 仓库在 $REPO"; else echo "  ✗ 没有 $REPO"; exit 1; fi
if command -v termux-wake-lock >/dev/null 2>&1; then
  echo "  ✓ termux-tools 在（wake-lock 可用）"
else
  echo "  · 没有 termux-wake-lock，跳过防休眠（服务仍能跑，只是可能被系统掐）"
fi

say "2/4 写开机钩子"
mkdir -p "$BOOTDIR"
HOOK="$BOOTDIR/wzbbti.sh"
cat > "$HOOK" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
# 由 deploy/termux-autostart.sh 生成（2026-10-20）
# 开机时执行：防止 CPU 深睡 + 确保 sv 托管在跑。
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock

cd "$REPO" || exit 1

# sv 托管通常已经起来了；没起来就拉一下（sv 需要 runsvdir，Termux 启动时才有）
if command -v sv >/dev/null 2>&1; then
  sv up wzbbti 2>/dev/null || true
fi

# 兜底：既没有 sv 也没有在跑的进程时，手动起一个
sleep 5
if ! pgrep -f "python run.py" >/dev/null 2>&1; then
  nohup python run.py >> "$REPO/data/boot-run.log" 2>&1 &
fi
EOF
chmod +x "$HOOK"
echo "  ✓ 已写 $HOOK"

say "3/4 确认 sv 托管状态"
if command -v sv >/dev/null 2>&1; then
  if sv status wzbbti 2>/dev/null | grep -q "run:"; then
    echo "  ✓ sv 正在托管 wzbbti（崩了会自动重启）"
  else
    echo "  · sv 没在托管。可能是 termux-services 没装，或者 Termux 重启后 runsvdir 没起来。"
    echo "    → 跑一次 deploy/termux-setup.sh 的第 6 步，或直接重跑那个脚本"
  fi
else
  echo "  · 没有 sv（termux-services 没装）→ 服务靠上面那个兜底手动起"
fi

say "4/4 自检"
echo "  钩子内容："
sed 's/^/    /' "$HOOK"

echo ""
echo "── 完成 ──"
echo ""
echo "还差两步**只能你自己在手机上做**："
echo "  1. 打开一次 Termux:Boot，点「允许自启」"
echo "  2. 设置 → 应用 → Termux → 电池 → 选「不限制」"
echo ""
echo "做完之后：手机关机重启 → 直接点 PWA 图标就能用，不用再点 Termux。"
