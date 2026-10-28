#!/usr/bin/env sh
# ── AI 穿搭助手 · Termux 一键部署 ───────────────────────────────────────────
# 在 Android 手机的 Termux 里跑这个脚本，把后端装到手机上，
# 这样电脑就不用一直开着。
#
# 用法（在 Termux 里）：
#     pkg install -y git
#     git clone --depth 1 https://github.com/Zarek-Zhao1112/ai-wardrobe.git ~/wzbbti
#     sh ~/wzbbti/deploy/legacy-termux/termux-setup.sh
#
# 这个脚本**可以反复跑**：中断了、或者隔一阵要更新，再跑一次就行。
#
# ─────────────────────────────────────────────────────────────────────────
# 安卓上跑这个项目的几个坑（脚本里都处理了，注释里写了原因，改之前先看）：
#
#   ① pydantic-core 是 Rust 扩展。PyPI 上的 aarch64 wheel 是 manylinux(glibc)，
#      Termux 是 bionic libc → 装不上。社区有预编译（TUR），但**包本体在
#      GitHub Releases，国内手机上直连拉不动**（实测 90 秒没下完 2MB）。
#      → 由 `deploy/legacy-termux/fetch_android_wheel.py` 走代理取回来再装。
#
#   ② requirements.txt 锁的是 `pydantic==2.13.4`（要 pydantic-core 2.46.4），
#      而 TUR 只有 2.50.1 → 锁死的版本在安卓上永远装不上。
#      → 把 pydantic 的锁去掉，用约束文件钉住已装好的 pydantic-core，
#        让 pip 自己反解出匹配的 pydantic。
#
#   ③ 国内 PyPI 镜像**不一定都能用**。实测（2026-10，pip 26.x）：
#        阿里云 ✓  腾讯云 ✓  官方 ✓  **清华 ✗**（索引页正常返回，pip 却报
#        "Could not find a version ... (from versions: none)"）
#      → 所以脚本会**先探测**再选用哪个镜像，而不是写死一个。
#
#   ④ uvicorn 千万别装 [standard] —— 它会拉 uvloop，在安卓上编译失败。
#      本项目 requirements.txt 里固定的是裸 uvicorn，正好避开。
# ─────────────────────────────────────────────────────────────────────────

set -e

HERE=$(cd "$(dirname "$0")" && pwd)
CACHE="$HOME/.cache/wzbbti"

# 按顺序探测，用第一个能用的。官方放最后兜底。
MIRRORS="
https://mirrors.aliyun.com/pypi/simple/
https://mirrors.cloud.tencent.com/pypi/simple
https://pypi.tuna.tsinghua.edu.cn/simple
https://pypi.org/simple
"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

mkdir -p "$CACHE"

# ── 0/7 选一个能用的 PyPI 索引 ────────────────────────────────────────────
# 参考 ③：写死任何一个镜像都可能踩坑，所以当场试。
say "[0/7] 挑一个可用的 PyPI 镜像"
INDEX=""
if pip index versions pip >/dev/null 2>&1 || pip index versions pip -i https://pypi.org/simple >/dev/null 2>&1; then
  for m in $MIRRORS; do
    printf '  试 %-52s ' "$m"
    # `pip index versions` 只查索引、不下载，几秒出结果，正好当探针
    if pip index versions pip -i "$m" >/dev/null 2>&1; then
      INDEX="$m"; echo "可用 ✓"
      break
    fi
    echo "不可用"
  done
else
  warn "  这个 pip 太老，没有 \`index\` 子命令 → 跳过探测，直接用阿里云"
  INDEX="https://mirrors.aliyun.com/pypi/simple/"
fi
if [ -z "$INDEX" ]; then
  warn "  四个镜像都探测失败 —— 先用阿里云硬着头皮往下走（可能是网络问题）"
  INDEX="https://mirrors.aliyun.com/pypi/simple/"
fi
echo "  → 用：$INDEX"

# ── 1/7 系统包 ────────────────────────────────────────────────────────────
say "[1/7] 安装 Python 与 git"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python git
# termux-services 提供 sv（进程托管）。装不上就跳过，不影响服务本身能跑。
pkg install -y termux-services >/dev/null 2>&1 || warn "  termux-services 没装上，常驻那步会跳过"

# ── 2/7 取代码 ────────────────────────────────────────────────────────────
say "[2/7] 准备代码"
if [ -f "$HERE/../app/main.py" ]; then
  REPO=$(cd "$HERE/.." && pwd)
  echo "  本脚本就在仓库里，直接用它：$REPO"
else
  REPO="$HOME/wzbbti"
  if [ -d "$REPO/.git" ]; then
    echo "  已有仓库，拉最新"
    git -C "$REPO" pull --ff-only || warn "  pull 失败（网络？），继续用现有代码"
  else
    git clone --depth 1 https://github.com/Zarek-Zhao1112/ai-wardrobe.git "$REPO"
  fi
fi
cd "$REPO"

# ── 3/7 pydantic-core（安卓预编译包）──────────────────────────────────────
say "[3/7] 装 pydantic-core"
if WHEEL=$(python "$HERE/fetch_android_wheel.py" --dest "$CACHE"); then
  echo "  用本地 wheel 安装：$(basename "$WHEEL")"
  # ★ 别加 --no-deps：pydantic-core 依赖 typing_extensions，而它是个纯 Python 包，
  #   走镜像装即可。踩过：加了 --no-deps 之后 `import pydantic_core` 直接
  #   ModuleNotFoundError: typing_extensions。
  pip install -i "$INDEX" "$WHEEL"
else
  warn "  取预编译包失败 → 现场编译 Rust（要装 100+MB 工具链，十几分钟，很吃内存）"
  warn "  想避开的话：在电脑上跑 deploy/legacy-termux/fetch_android_wheel.py，把下到的 wheel 传进手机再 pip install"
  pkg install -y rust clang cmake make binutils
  CARGO_BUILD_JOBS=2 pip install -i "$INDEX" pydantic-core
fi

# ── 4/7 其余依赖 ──────────────────────────────────────────────────────────
say "[4/7] 装其余依赖"
# 版本号从 wheel 文件名里取（`pydantic_core-2.50.1-cp314-...whl`），
# 而不是 import 后取 —— 少一个"必须先装对"的隐藏前提。
CORE_VER=$(basename "$WHEEL" | sed 's/^pydantic_core-\([^-]*\)-.*/\1/')
if [ -n "$CORE_VER" ]; then
  echo "  已装 pydantic-core $CORE_VER → 用约束钉住它，让 pip 反解匹配的 pydantic"
  awk '!/^pydantic(==|>=|~=|>|<)/ { print }' requirements.txt > "$CACHE/req-android.txt"
  echo "pydantic" >> "$CACHE/req-android.txt"
  printf 'pydantic-core==%s\n' "$CORE_VER" > "$CACHE/constr.txt"
  pip install -i "$INDEX" -r "$CACHE/req-android.txt" -c "$CACHE/constr.txt"
else
  warn "  没推出 pydantic-core 版本，按已有版本装"
  pip install -i "$INDEX" -r requirements.txt
fi

# ── 5/7 自检 ──────────────────────────────────────────────────────────────
say "[5/7] 自检"
python - <<'PYEOF'
import pydantic, pydantic_core
import fastapi, starlette, uvicorn, httpx, segno
print(f"  fastapi {fastapi.__version__} | starlette {starlette.__version__} | "
      f"pydantic {pydantic.VERSION} | pydantic_core {pydantic_core.__version__}")
print(f"  uvicorn {uvicorn.__version__} | httpx {httpx.__version__} | segno {segno.__version__}")
PYEOF

# ── 6/7 常驻（termux-services 托管）──────────────────────────────────────
# 只写服务定义 + 启用。★ 已实测的坑：装完 termux-services **必须重启 Termux**
# 才有 runsvdir（它是 Termux 启动时由 profile 拉起的），否则 sv-enable 会报
# "unable to change to service directory"。所以这步做不成就让用户关掉重开再跑一次
# —— 脚本是幂等的，第二次跑会接着往下走。
say "[6/7] 配常驻（termux-services）"
SVDIR="$PREFIX/var/service/wzbbti"
if [ -d "$PREFIX/var/service" ]; then
  mkdir -p "$SVDIR/log"
  cat > "$SVDIR/run" <<EOF
#!$PREFIX/bin/sh
# 由 deploy/legacy-termux/termux-setup.sh 生成 —— 重跑脚本会覆盖它。
termux-wake-lock                      # 别让 CPU 深睡把服务掐了
export APP_HOST=0.0.0.0               # 让同一 WiFi 下的电脑也能访问
cd $REPO && exec python run.py
EOF
  chmod +x "$SVDIR/run"
  cat > "$SVDIR/log/run" <<EOF
#!$PREFIX/bin/sh
mkdir -p $REPO/data/sv
exec svlogd -tt $REPO/data/sv
EOF
  chmod +x "$SVDIR/log/run"

  if sv-enable wzbbti 2>/dev/null && sv up wzbbti 2>/dev/null; then
    sleep 3
    sv status wzbbti || true
  else
    warn "  服务定义已写好，但 runsvdir 还没起来（刚装 termux-services 会这样）。"
    warn "  → 把 Termux 完全关掉再打开一次（从最近任务里划掉），然后**重跑这个脚本**。"
  fi
else
  warn "  没有 $PREFIX/var/service（termux-services 没装成），跳过常驻。"
fi

# ── 7/7 收尾 ──────────────────────────────────────────────────────────────
say "[7/7] 完成 ✅"
cat <<EOF

启动后端：
    cd $REPO && python run.py

然后手机浏览器打开 http://127.0.0.1:8787 ，
菜单里选「添加到主屏幕」，桌面就有图标了。

⚠️ 只有 http://127.0.0.1:8787 能装成 App —— 用局域网 IP（http://192.168.x.x:8787）
   打开**装不了**（浏览器要求"安全上下文"，这是规矩不是 bug）。

保活、手机重启后怎么用、以及"为什么加了主屏幕但看起来还是网页"，见 deploy/README.md。
EOF
