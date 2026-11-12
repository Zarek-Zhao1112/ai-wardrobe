#!/bin/bash
# Shared safe-delete implementation for rm / unlink / rmdir wrappers.

set -o pipefail

REAL_RM="${SAFE_DELETE_REAL_RM:-/bin/rm}"
[ -x "$REAL_RM" ] || REAL_RM="/usr/bin/rm"
REAL_UNLINK="${SAFE_DELETE_REAL_UNLINK:-/usr/bin/unlink}"
[ -x "$REAL_UNLINK" ] || REAL_UNLINK="/bin/unlink"
REAL_RMDIR="${SAFE_DELETE_REAL_RMDIR:-/bin/rmdir}"
[ -x "$REAL_RMDIR" ] || REAL_RMDIR="/usr/bin/rmdir"

OS="$(uname -s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%S)"
SAFE_DELETE_BIN_DIR="$(CDPATH= cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_safe_delete_one_line() {
    printf '%s' "$1" | tr '\n\r' '  '
}

_safe_delete_diag() {
    echo "[safe-delete][diag] $*" >&2
}

_safe_delete_run_node() {
    local node_bin="$1"
    shift
    if [ "${CODEBUDDY_NODE_RUN_AS_NODE:-}" = "1" ]; then
        ELECTRON_RUN_AS_NODE=1 env -u NODE_OPTIONS "$node_bin" "$@"
    else
        env -u NODE_OPTIONS "$node_bin" "$@"
    fi
}

safe_delete_json_string() {
    safe_delete_json_string_var "$1"
    printf '%s' "$SAFE_DELETE_JSON"
}

# 结果写入 SAFE_DELETE_JSON。
# 纯 bash JSON 字符串转义：避免 python（Windows Store stub 每次调用 1-4 秒且 stdout 可能空，
# 写出非法 JSON 导致 readSafeDeleteReport 静默丢弃"已移入回收站"提示）和 sed（fork 开销）。
# 只转义 JSON 必需字符：\ 和 "。路径里控制字符极罕见，不处理（与原 sed fallback 一致）。
safe_delete_json_string_var() {
    local escaped="${1//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    SAFE_DELETE_JSON="\"$escaped\""
}

_safe_delete_record_report() {
    local operation="$1" p="$2"
    [ -n "${CODEBUDDY_SAFE_DELETE_REPORT_PATH:-}" ] || return 0
    local timestamp="${EPOCHSECONDS:-}"
    safe_delete_json_string_var "$p"
    [ -n "$timestamp" ] || timestamp="$(date +%s 2>/dev/null || printf '0')"
    printf '{"operation":"%s","runtime":"bash","path":%s,"timestamp":%s}\n' \
        "$operation" "$SAFE_DELETE_JSON" "$timestamp" >> "$CODEBUDDY_SAFE_DELETE_REPORT_PATH" 2>/dev/null || true
}

safe_delete_record_trash() {
    _safe_delete_record_report trash "$1"
}

safe_delete_bulk_guard_check() {
    [ "$#" -gt 0 ] || return 0
    [ -n "${CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR:-}" ] || return 0
    [ -n "${CODEBUDDY_TOOL_CALL_ID:-}" ] || return 0
    if [ -z "${CODEBUDDY_NODE_BIN:-}" ] || [ -z "${CODEBUDDY_SAFE_DELETE_BULK_GUARD:-}" ] || [ ! -f "$CODEBUDDY_SAFE_DELETE_BULK_GUARD" ]; then
        echo "[safe-delete][SAFE_DELETE_BULK_GUARD_ERROR] msg=helper-unavailable guard=${CODEBUDDY_SAFE_DELETE_BULK_GUARD:-unset}" >&2
        return 1
    fi

    local args=()
    local p
    for p in "$@"; do
        safe_delete_abs_path_var "$p"
        args+=(--target "$SAFE_DELETE_ABS")
    done
    _safe_delete_run_node "$CODEBUDDY_NODE_BIN" \
        "$CODEBUDDY_SAFE_DELETE_BULK_GUARD" check "${args[@]}"
}

safe_delete_was_deleted_before_bulk_replay() {
    local replay_file="${CODEBUDDY_SAFE_DELETE_BULK_REPLAY_FILE:-}"
    local node_bin="${CODEBUDDY_NODE_BIN:-}"
    [ -n "$replay_file" ] && [ -f "$replay_file" ] && [ -n "$node_bin" ] || return 1

    local abs
    safe_delete_abs_path_var "$1"
    abs="$SAFE_DELETE_ABS"
    _safe_delete_run_node "$node_bin" -e '
        const fs = require("fs");
        const path = require("path");
        const normalize = value => {
            // 报告里是 MSYS 路径 /c/...，而 MSYS 传给 node 的 argv 已转成 C:/...。
            if (process.platform === "win32") {
                value = value.replace(/^\/([A-Za-z])(?=\/|$)/, "$1:");
            }
            let resolved = path.resolve(value);
            try {
                resolved = fs.realpathSync.native
                    ? fs.realpathSync.native(resolved)
                    : fs.realpathSync(resolved);
            } catch (_) {}
            return process.platform === "win32" ? resolved.toLowerCase() : resolved;
        };
        try {
            const paths = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
            process.exit(Array.isArray(paths) && paths.some(value =>
                typeof value === "string" && normalize(value) === normalize(process.argv[2])
            ) ? 0 : 1);
        } catch (_) {
            process.exit(1);
        }
    ' "$replay_file" "$abs" >/dev/null 2>&1
}

is_dir_empty() {
    local p="$1"
    [ -z "$(ls -A "$p" 2>/dev/null)" ]
}

safe_delete_validate_targets() {
    case "$OS" in
        MINGW*|MSYS*|CYGWIN*) ;;
        *) return 0 ;;
    esac
    local p
    for p in "$@"; do
        p="${p//\\//}"
        case "$p" in
            '//?/'[A-Za-z]:/*) p="${p:4}" ;;
        esac
        # 内嵌盘符可能在 native-trash 重组路径时替换父目录。
        case "$p" in
            */[A-Za-z]:*)
                echo "[safe-delete][SAFE_DELETE_INVALID_PATH] embedded drive prefix is not allowed" >&2
                return 64
                ;;
        esac
    done
    return 0
}

safe_delete_to_unix_path_if_possible() {
    local p="$1"
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$p" 2>/dev/null && return 0
    fi
    printf '%s' "$p"
}

# 结果写入 SAFE_DELETE_UNIX_PATH。沙箱内每次子进程约 0.3s：单斜杠开头的 Unix 路径
# cygpath -u 原样返回，直接跳过；UNC 与盘符路径仍交给 cygpath。
safe_delete_to_unix_path_var() {
    case "$1" in
        //*) ;;
        /*) SAFE_DELETE_UNIX_PATH="$1"; return 0 ;;
    esac
    SAFE_DELETE_UNIX_PATH="$(safe_delete_to_unix_path_if_possible "$1")"
}

# 结果写入 SAFE_DELETE_ABS。
safe_delete_abs_path_var() {
    local p="$1"
    local abs dir base old_pwd old_oldpwd
    # 纯 bash fallback：cd -P 解析物理路径（含符号链接），cygpath 转 Unix 风格。
    # 不依赖 python：Windows Store python3 stub 每次调用 1-4 秒且 stdout 为空，
    # WorkBuddy 沙箱自带 python 启动也可能慢/卡，fallback 更快更可靠。
    case "$p" in
        /*|[A-Za-z]:/*) abs="$p" ;;
        # ${PWD%/} 去掉 $PWD 尾部斜杠：$PWD=/ 时裸拼接会产出 //foo，
        # 被 Windows 解释为 UNC \\foo 触发网络解析卡死 120s。
        *)              abs="${PWD%/}/$p" ;;
    esac
    # 用 bash 内置参数展开替代 dirname/basename 外部命令，
    # 避免 Git Bash 每次 fork 子 shell ~200ms 的开销。
    case "$abs" in
        */*) dir="${abs%/*}"; [ -z "$dir" ] && dir="/" ;;
        *)    dir="." ;;
    esac
    base="${abs##*/}"
    if [ "$dir" != "." ] && [ "$dir" != "$abs" ]; then
        old_pwd="$PWD"
        case "$old_pwd" in
            /*) [ -d "$old_pwd" ] || old_pwd="" ;;
            *) old_pwd="" ;;
        esac
        if [ -z "$old_pwd" ]; then
            # 当前目录已不存在时无法切回，退回子 shell。
            abs="$(CDPATH= cd -P -- "$dir" 2>/dev/null && printf '%s/%s' "${PWD%/}" "$base" || printf '%s' "$abs")"
        else
            old_oldpwd="${OLDPWD-}"
            if CDPATH= builtin cd -P -- "$dir" 2>/dev/null; then
                abs="${PWD%/}/$base"
                if ! builtin cd -- "$old_pwd" 2>/dev/null; then
                    echo "[safe-delete][SAFE_DELETE_CWD_RESTORE_FAILED] cwd=$old_pwd" >&2
                    exit 1
                fi
            fi
            OLDPWD="$old_oldpwd"
        fi
    fi
    # 出口兜底：cd -P -- // 在部分 shell 下 $PWD 会保留为 //（POSIX 允许实现选择
    # // 开头的根），上面两处 ${PWD%/} 只剥一层，//foo 这类输入仍可能漏网。
    # 在这里统一收口：规范化单组件 // 前缀为 /，保留 //server/share 多组件 UNC 形态。
    case "$abs" in
        //*/*) ;;
        //*) abs="/${abs#//}" ;;
    esac
    safe_delete_to_unix_path_var "$abs"
    SAFE_DELETE_ABS="$SAFE_DELETE_UNIX_PATH"
}

safe_delete_tmp_root_allowed() {
    local root="$1"
    [ -n "$root" ] || return 1
    [ "$root" != "/" ] || return 1
    [ -d "$root" ] || return 1
    return 0
}

safe_delete_build_os_tmp_dirs() {
    local seen="|" candidate root
    safe_delete_to_unix_path_var "${TMPDIR:-${TMP:-${TEMP:-/tmp}}}"
    for candidate in \
        "$SAFE_DELETE_UNIX_PATH" \
        "/tmp" \
        "/private/tmp"
    do
        [ -n "$candidate" ] || continue
        safe_delete_abs_path_var "$candidate"
        root="$SAFE_DELETE_ABS"
        while [ "$root" != "/" ] && [ "${root%/}" != "$root" ]; do root="${root%/}"; done
        safe_delete_tmp_root_allowed "$root" || continue
        case "$seen" in
            *"|$root|"*) ;;
            *)
                seen="${seen}${root}|"
                printf '%s\n' "$root"
                ;;
        esac
    done
}

SAFE_DELETE_OS_TMP_DIRS="$(safe_delete_build_os_tmp_dirs)"

safe_delete_is_under_os_tmp_dir() {
    local abs
    safe_delete_abs_path_var "$1"
    abs="$SAFE_DELETE_ABS"
    while [ "$abs" != "/" ] && [ "${abs%/}" != "$abs" ]; do abs="${abs%/}"; done

    # 用数组分片而不是 heredoc/here-string 遍历 tmp 目录列表：heredoc 在 bash 内部
    # 会落一个临时文件再 unlink（/tmp/sh-thd-*），在沙箱 broker 场景下这次 unlink
    # 会被 Seatbelt 的 file-write-unlink 监控误判为一次“受保护文件修改”拦截，
    # 抢在批量删除确认信号之前触发提权 UI。纯数组分片不产生任何文件系统操作。
    local tmp_dir
    local __sdiotd_old_ifs="$IFS"
    IFS=$'\n'
    set -f
    local -a __sdiotd_tmp_dirs=($SAFE_DELETE_OS_TMP_DIRS)
    set +f
    IFS="$__sdiotd_old_ifs"

    for tmp_dir in "${__sdiotd_tmp_dirs[@]}"; do
        [ -n "$tmp_dir" ] || continue
        case "$OS" in
            MINGW*|MSYS*|CYGWIN*)
                local abs_lower tmp_lower
                # 用 bash 4+ 内置小写转换 ${var,,} 替代 printf|tr，避免 fork 子 shell。
                abs_lower="${abs,,}"
                tmp_lower="${tmp_dir,,}"
                case "$abs_lower" in
                    "$tmp_lower"/*) return 0 ;;
                esac
                continue
                ;;
        esac
        case "$abs" in
            "$tmp_dir"/*) return 0 ;;
        esac
    done
    return 1
}

safe_delete_bulk_guard_non_tmp_check() {
    local targets=()
    local p
    for p in "$@"; do
        safe_delete_is_under_os_tmp_dir "$p" && continue
        targets+=("$p")
    done
    [ ${#targets[@]} -eq 0 ] && return 0
    safe_delete_bulk_guard_check "${targets[@]}"
}

# ---- platform-specific trash helpers -----------------------------------------

trash_darwin() {
    local p="$1"

    # macOS fallback: use CoreServices API via python ctypes.
    # This avoids AppleScript/Finder automation permissions in sandbox.
    local py_out
    py_out="$(python3 - "$p" 2>&1 <<'PY'
import os
import sys
from ctypes import Structure, byref, c_char, c_char_p, cdll
from ctypes.util import find_library

path = sys.argv[1]
foundation_path = find_library("Foundation")
coreservices_path = find_library("CoreServices")
if not foundation_path or not coreservices_path:
    print("Foundation/CoreServices unavailable", file=sys.stderr)
    sys.exit(1)

Foundation = cdll.LoadLibrary(foundation_path)
CoreServices = cdll.LoadLibrary(coreservices_path)
GetMacOSStatusCommentString = Foundation.GetMacOSStatusCommentString
GetMacOSStatusCommentString.restype = c_char_p
FSPathMakeRefWithOptions = CoreServices.FSPathMakeRefWithOptions
FSMoveObjectToTrashSync = CoreServices.FSMoveObjectToTrashSync

class FSRef(Structure):
    _fields_ = [("hidden", c_char * 80)]

def status_to_message(code: int) -> str:
    raw = GetMacOSStatusCommentString(code)
    if not raw:
        return f"OSStatus {code}"
    return raw.decode("utf-8", errors="replace")

path_b = os.fsencode(path)
fp = FSRef()
rc = FSPathMakeRefWithOptions(path_b, 0x01, byref(fp), None)
if rc:
    print(status_to_message(rc), file=sys.stderr)
    sys.exit(1)

rc = FSMoveObjectToTrashSync(byref(fp), None, 0)
if rc:
    print(status_to_message(rc), file=sys.stderr)
    sys.exit(1)
PY
)"
    local py_rc=$?
    if [ $py_rc -ne 0 ]; then
        _safe_delete_diag "python ctypes fallback failed: exit=$py_rc path=$p stderr=$(_safe_delete_one_line "$py_out")"
        return 1
    fi

    # API 调用成功后追加存在性校验：路径仍在原处 = 失败，拒绝删除（fail-closed）
    if [ -e "$p" ] || [ -L "$p" ]; then
        _safe_delete_diag "python ctypes fallback reported success but file still exists: path=$p"
        return 1
    fi
    _safe_delete_diag "python ctypes fallback succeeded: path=$p"
    return 0
}

trash_linux() {
    local p="$1"
    local d="${XDG_DATA_HOME:-$HOME/.local/share}/Trash"
    mkdir -p "$d/files" "$d/info" 2>/dev/null || return 1

    local b="${p##*/}"
    local tf="$d/files/$b" ti="$d/info/$b.trashinfo"
    local n=1
    while [ -e "$tf" ] || [ -e "$ti" ]; do
        n=$((n + 1))
        [ $n -gt 1000 ] && return 1
        tf="$d/files/$b.$n"
        ti="$d/info/$b.$n.trashinfo"
    done

    # freedesktop spec 要求 Path 字段百分比编码非 ASCII 字符。
    # 已知限制：此处不编码，含中文/Unicode 文件名与 Node.js/Python 版本的
    # trashinfo 格式不一致。bash 下无法可靠实现 UTF-8 字节级编码，暂且接受。
    printf '[Trash Info]\nPath=%s\nDeletionDate=%s\n' "$p" "$NOW" > "$ti" || return 1
    chmod 600 "$ti" 2>/dev/null

    if mv "$p" "$tf" 2>/dev/null; then
        return 0
    fi

    # EXDEV: cross-device — fall back to copy + delete
    # 使用 $REAL_RM 而非裸 rm：PATH 中 safe-bin/ 已前置，裸 rm 会递归回自身
    if [ -d "$p" ] && [ ! -L "$p" ]; then
        cp -R "$p" "$tf" 2>/dev/null || { "$REAL_RM" -f "$ti" 2>/dev/null; return 1; }
        "$REAL_RM" -rf "$p" 2>/dev/null || { "$REAL_RM" -f "$ti" 2>/dev/null; "$REAL_RM" -rf "$tf" 2>/dev/null; return 1; }
    else
        cp "$p" "$tf" 2>/dev/null || { "$REAL_RM" -f "$ti" 2>/dev/null; return 1; }
        "$REAL_RM" -f "$p" 2>/dev/null || { "$REAL_RM" -f "$ti" 2>/dev/null; "$REAL_RM" -f "$tf" 2>/dev/null; return 1; }
    fi
}

# ---- 解析 genie-trash binary 路径 ----

_gt_arch() {
    case "$(uname -m)" in
        arm64|aarch64) echo arm64 ;;
        x86_64|amd64)  echo x64 ;;
        *)             echo "$(uname -m)" ;;
    esac
}

# 将 Git Bash / MSYS2 / Cygwin 的 uname -s 输出映射为 win32，
# 使其与 genie-trash binary 文件名（win32-x64.exe）和 process.platform 一致。
_gt_platform() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) echo "win32" ;;
        *)                     echo "$(uname -s | tr 'A-Z' 'a-z')" ;;
    esac
}

GT_PLATFORM="$(_gt_platform)"
GT_ARCH="$(_gt_arch)"
GT_EXT=""
[ "$GT_PLATFORM" = "win32" ] && GT_EXT=".exe"

    # 将路径转换为 Windows 风格绝对路径，供 genie-trash binary 使用。
# 不依赖 cygpath（沙箱 PATH 可能不含 PortableGit/usr/bin）。
# 失败时返回非 0，由调用方 fail-closed。
safe_delete_to_windows_path() {
    safe_delete_to_windows_path_var "$1" || return 1
    printf '%s' "$SAFE_DELETE_WINDOWS_PATH"
}

# 结果写入 SAFE_DELETE_WINDOWS_PATH。
safe_delete_to_windows_path_var() {
    local p="$1"
    local out=""
    SAFE_DELETE_WINDOWS_PATH=""

    case "$p" in
        /[A-Za-z]/*)
            local drive="${p:1:1}"
            local rest="${p:3}"
            printf -v out '%s:\\%s' "$drive" "${rest//\//\\}"
            ;;
        [A-Za-z]:/*)
            out="${p//\//\\}"
            ;;
        //*)
            local rest="${p:2}"
            printf -v out '\\\\%s' "${rest//\//\\}"
            ;;
        *)
            if command -v cygpath >/dev/null 2>&1; then
                out="$(cygpath -w "$p" 2>/dev/null)"
            fi
            ;;
    esac

    # 契约：返回非 0 表示转换失败。校验输出是合法 Windows 绝对路径（盘符或 UNC），
    # 避免 cygpath fallback 对相对路径静默通过导致 genie-trash exit 2 才暴露问题。
    case "$out" in
        [A-Za-z]:[\\]*|\\\\*) SAFE_DELETE_WINDOWS_PATH="$out"; return 0 ;;
        *) return 1 ;;
    esac
}

if [ -n "${GENIE_TRASH_DIR:-}" ]; then
    TRASH_BIN="${GENIE_TRASH_DIR}/${GT_PLATFORM}-${GT_ARCH}${GT_EXT}"
else
    TRASH_BIN="${SAFE_DELETE_BIN_DIR}/../../genie-trash/${GT_PLATFORM}-${GT_ARCH}${GT_EXT}"
fi

trash_windows() {
    local p="$1"
    # 转为 Windows 风格路径（powershell 不接受 / 分隔的路径）
    local wp
    wp="$(safe_delete_to_windows_path "$p")" || {
        _safe_delete_diag "cannot convert path to Windows absolute path: path=$p"
        return 1
    }
    # 转义路径中的单引号：' → ''（PowerShell 单引号字符串内的转义方式）
    local wp_escaped="${wp//\'/\'\'}"

    # 用 powershell COM API 移入回收站，与 Node.js shim 的 trashOnWindows 一致
    local ps_out
    ps_out="$(powershell -NoProfile -NonInteractive -Command "
        try {
            \$targetPath = '${wp_escaped}'
            if (Test-Path -LiteralPath \$targetPath -PathType Container) {
                Add-Type -AssemblyName Microsoft.VisualBasic
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory(\$targetPath,
                    [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
                    [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)
            } else {
                Add-Type -AssemblyName Microsoft.VisualBasic
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(\$targetPath,
                    [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs,
                    [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin)
            }
            Write-Output 'OK'
        } catch {
            Write-Error \$_.Exception.Message
        }
    " 2>&1)"
    local ps_rc=$?

    if [ $ps_rc -eq 0 ] && echo "$ps_out" | grep -q '^OK$'; then
        # API 调用成功后追加存在性校验（fail-closed）
        if [ -e "$p" ]; then
            _safe_delete_diag "powershell COM reported success but file still exists: path=$p"
            return 1
        fi
        _safe_delete_diag "powershell COM succeeded: path=$p"
        return 0
    fi
    _safe_delete_diag "powershell COM fallback failed: exit=$ps_rc path=$p stderr=$(_safe_delete_one_line "$ps_out")"
    return 1
}

trash_one() {
    local p
    local native_failed=0
    # 转为绝对路径：genie-trash binary 拒绝相对路径。
    # 复用 safe_delete_abs_path_var（内含 // → / 规范化）而不是重复写一份绝对化逻辑，
    # 避免两处路径解析出现分叉、其中一处漏掉 UNC 卡死修复。
    safe_delete_abs_path_var "$1"
    p="$SAFE_DELETE_ABS"
    # 优先 native binary
    if [ -x "$TRASH_BIN" ]; then
        local native_path="$p"
        if [ "$GT_PLATFORM" = "win32" ]; then
            safe_delete_to_windows_path_var "$p" || {
                _safe_delete_diag "cannot convert path to Windows absolute path: path=$p"
                return 1
            }
            native_path="$SAFE_DELETE_WINDOWS_PATH"
        fi
        safe_delete_validate_targets "$native_path" || return $?
        local native_out existed=1
        [ -e "$p" ] || [ -L "$p" ] || existed=0
        native_out="$("$TRASH_BIN" "$native_path" 2>&1)"
        local native_rc=$?
        if [ $native_rc -eq 0 ]; then
            return 0
        fi
        # Windows 上 trash crate 的 IFileOperation::GetAnyOperationsAborted 会误报 true，
        # 但 PerformOperations 实际已删除文件。以文件系统事实为准：调用前在、调用后没了 = 删除成功。
        # 仅 Windows 启用：根因是 Win32 独有 API，非 Windows 上失败原因未知，不应静默吞掉。
        # 参考 https://github.com/maxkagamine/wsl-tools 的 recycle_bin.rs
        # 保持静默：与 node-safe-delete-shim.cjs / sitecustomize.py 一致，避免 stderr 里的
        # exit code / native ERROR 文本被上层 Bash 工具当作失败信号喂给模型。
        if [ "$GT_PLATFORM" = "win32" ] && [ $existed -eq 1 ] && [ ! -e "$p" ] && [ ! -L "$p" ]; then
            return 0
        fi
        _safe_delete_diag "genie-trash failed: exit=$native_rc bin=$TRASH_BIN path=$p stderr=$(_safe_delete_one_line "$native_out")"
        native_failed=1
    else
        _safe_delete_diag "genie-trash unavailable: bin-not-executable path=$TRASH_BIN"
    fi
    # Windows 下 native helper 已存在但失败时，必须 fail-closed。
    # 不能降级到 powershell COM 删除，否则会出现“回收站失败后仍被删除”的风险。
    if [ "$GT_PLATFORM" = "win32" ] && [ $native_failed -eq 1 ]; then
        return 1
    fi
    # ---- 降级：当前平台实现 ----
    case "$OS" in
        Darwin)               trash_darwin "$p" ;;
        MINGW*|MSYS*|CYGWIN*) trash_windows "$p" ;;
        Linux)                trash_linux "$p" ;;
        *)                    return 1 ;;
    esac
}

safe_delete_check_program_policy() {
    local program="$1"
    [ "$GT_PLATFORM" = "darwin" ] || return 0
    [ -n "${CODEBUDDY_SANDBOX_PROGRAM_POLICY_COMMAND:-}" ] || return 0

    local helper="${SAFE_DELETE_BIN_DIR%/safe-bin}/broker-program-policy-check.cjs"
    local broker_client="${SAFE_DELETE_BIN_DIR%/safe-bin}/broker-ipc-client.cjs"
    local node_bin="${CODEBUDDY_NODE_BIN:-node}"
    if [ -z "${CODEBUDDY_SANDBOX_BROKER_IPC_ADDRESS:-}" ] \
        || [ -z "${CODEBUDDY_SANDBOX_BROKER_SESSION_ID:-}" ] \
        || [ ! -f "$broker_client" ] \
        || [ ! -f "$helper" ]; then
        printf '%s' 'Brokered program policy check unavailable' >&2
        return 13
    fi

    _safe_delete_run_node "$node_bin" "$helper" "$program" safe-delete "$broker_client"
}

try_broker_delete() {
    local abs="$1"
    local recursive="${2:-false}"
    local force="${3:-false}"
    [ "$GT_PLATFORM" = "darwin" ] || return 2
    [ -n "${CODEBUDDY_SANDBOX_BROKER_IPC_ADDRESS:-}" ] || return 2
    [ -n "${CODEBUDDY_SANDBOX_BROKER_SESSION_ID:-}" ] || return 2
    [ -n "${CODEBUDDY_SANDBOX_HOST_FILE_OPERATION_COMMAND:-}" ] || return 2

    local helper="${CODEBUDDY_SAFE_DELETE_BROKER_DELETE:-}"
    local node_bin="${CODEBUDDY_NODE_BIN:-}"
    [ -n "$helper" ] && [ -f "$helper" ] && [ -n "$node_bin" ] || return 2

    local broker_out broker_rc
    local broker_args=()
    [ "$recursive" = "true" ] && broker_args+=("--recursive")
    [ "$force" = "true" ] && broker_args+=("--force")
    broker_out="$(
        _safe_delete_run_node "$node_bin" "$helper" "${broker_args[@]}" -- "$abs" 2>&1 >/dev/null
    )"
    broker_rc=$?

    if [ $broker_rc -eq 0 ]; then
        return 0
    fi
    if [ $broker_rc -eq 2 ]; then
        _safe_delete_diag "broker unavailable, falling back to local trash: path=$abs"
        return 2
    fi
    _safe_delete_diag "broker denied delete: path=$abs stderr=$(_safe_delete_one_line "$broker_out")"
    return 1
}

try_trash() {
    local p="$1"
    local recursive="${2:-false}"
    local force="${3:-false}"
    local abs
    safe_delete_abs_path_var "$p"
    abs="$SAFE_DELETE_ABS"

    safe_delete_validate_targets "$abs" || return $?
    try_broker_delete "$abs" "$recursive" "$force"
    local broker_rc=$?
    if [ $broker_rc -eq 0 ]; then
        return 0
    fi
    if [ $broker_rc -eq 1 ]; then
        local target_json
        target_json="$(safe_delete_json_string "$p")"
        echo "[safe-delete][SAFE_DELETE_FAIL_CLOSED] {\"target\":$target_json,\"reason\":\"broker-denied\"}" >&2
        return 1
    fi

    # Windows 沙箱且开启文件备份：普通文件交给 tsbx+Center 决定是否进回收站（本轮新建的
    # 直接删）。原生删除失败（如 Center 拒绝）时继续走 genie-trash，保留审批与 fail-closed。
    if [ "$GT_PLATFORM" = "win32" ] \
        && [ "${CODEBUDDY_SAFE_DELETE_SANDBOX:-}" = "1" ] \
        && [ "${CODEBUDDY_FILE_BACKUP_ENABLED:-}" = "1" ] \
        && [ -f "$p" ] && [ ! -L "$p" ] \
        && "$REAL_RM" -f -- "$p" 2>/dev/null; then
        _safe_delete_record_report native-delete "$abs"
        return 0
    fi

    if trash_one "$p"; then
        safe_delete_record_trash "$abs"
        return 0
    else
        local trash_rc=$?
        # 路径硬拒绝不能转成可审批重试的回收站失败。
        [ "$trash_rc" -eq 64 ] && return "$trash_rc"
    fi
    local target_json trash_bin_json genie_trash_dir_json
    target_json="$(safe_delete_json_string "$p")"
    trash_bin_json="$(safe_delete_json_string "${TRASH_BIN:-unset}")"
    genie_trash_dir_json="$(safe_delete_json_string "${GENIE_TRASH_DIR:-unset}")"
    echo "[safe-delete][SAFE_DELETE_FAIL_CLOSED] {\"target\":$target_json,\"reason\":\"trash-failed\",\"trashBin\":$trash_bin_json,\"genieTrashDir\":$genie_trash_dir_json}" >&2
    return 1
}

# ---- command mode dispatch ----------------------------------------------------

safe_delete_rm() {
    local targets=()
    local force=false
    local recursive=false
    local allow_dir=false
    local interactive=false
    local after_double_dash=false
    local a

    for a in "$@"; do
        if $after_double_dash; then
            targets+=("$a")
            continue
        fi
        if [ "$a" = "--" ]; then
            after_double_dash=true
            continue
        fi
        case "$a" in
            -f|--force) force=true ;;
            -r|-R|--recursive) recursive=true ;;
            -d|--dir|--directory) allow_dir=true ;;
            -i|-I|--interactive|--interactive=*) interactive=true ;;
            -[!-]*)
                # 仅解析短选项组合（如 -rf/-fr/-di）；不把长选项
                # --preserve-root 误判为包含 -r。
                [[ "$a" == *f* ]] && force=true
                [[ "$a" == *r* || "$a" == *R* ]] && recursive=true
                [[ "$a" == *d* ]] && allow_dir=true
                [[ "$a" == *i* || "$a" == *I* ]] && interactive=true
                ;;
        esac
        case "$a" in
            -*) : ;;
            *)  targets+=("$a") ;;
        esac
    done

    # No targets (e.g. "rm --help") → let real rm handle
    if [ ${#targets[@]} -eq 0 ]; then
        exec "$REAL_RM" "$@"
    fi

    # rm -i / --interactive 有 prompt 语义；不能回退真删，只能 fail-closed。
    if $interactive; then
        echo "rm: interactive mode is not supported by safe-delete" >&2
        return 1
    fi

    safe_delete_validate_targets "${targets[@]}" || return $?
    local _exit=0
    local trash_targets=()
    local p
    for p in "${targets[@]}"; do
        if [ -e "$p" ] || [ -L "$p" ]; then
            if [ -d "$p" ] && [ ! -L "$p" ]; then
                if ! $recursive && ! $allow_dir; then
                    echo "rm: $p: is a directory" >&2
                    _exit=1
                    continue
                fi
                if ! $recursive && $allow_dir && ! is_dir_empty "$p"; then
                    echo "rm: $p: Directory not empty" >&2
                    _exit=1
                    continue
                fi
            fi
            trash_targets+=("$p")
        elif safe_delete_was_deleted_before_bulk_replay "$p"; then
            :
        elif $force; then
            # -f: silently skip non-existent files (match real rm behavior)
            :
        else
            # No -f: report error for non-existent files (match real rm behavior)
            echo "rm: $p: No such file or directory" >&2
            _exit=1
        fi
    done
    if ! safe_delete_bulk_guard_non_tmp_check "${trash_targets[@]}"; then
        return 1
    fi
    for p in "${trash_targets[@]}"; do
        if safe_delete_is_under_os_tmp_dir "$p"; then
            local native_args=()
            $force && native_args+=("-f")
            $recursive && native_args+=("-r")
            $allow_dir && native_args+=("-d")
            if ! "$REAL_RM" "${native_args[@]}" -- "$p"; then
                _exit=1
            fi
        elif ! try_trash "$p" "$recursive" "$force"; then
            _exit=1
        fi
    done
    return $_exit
}

safe_delete_unlink() {
    local target=""

    if [ "$#" -eq 0 ]; then
        exec "$REAL_UNLINK" "$@"
    fi

    if [ "$1" = "--" ]; then
        if [ "$#" -ne 2 ]; then
            exec "$REAL_UNLINK" "$@"
        fi
        target="$2"
    else
        case "$1" in
            -*) exec "$REAL_UNLINK" "$@" ;;
        esac
        if [ "$#" -ne 1 ]; then
            exec "$REAL_UNLINK" "$@"
        fi
        target="$1"
    fi

    safe_delete_validate_targets "$target" || return $?
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
        if safe_delete_was_deleted_before_bulk_replay "$target"; then
            return 0
        fi
        echo "unlink: $target: No such file or directory" >&2
        return 1
    fi
    if [ -d "$target" ] && [ ! -L "$target" ]; then
        echo "unlink: $target: Is a directory" >&2
        return 1
    fi

    if ! safe_delete_bulk_guard_non_tmp_check "$target"; then
        return 1
    fi
    if safe_delete_is_under_os_tmp_dir "$target"; then
        "$REAL_UNLINK" -- "$target"
        return $?
    fi

    try_trash "$target"
    return $?
}

safe_delete_rmdir() {
    local targets=()
    local after_double_dash=false
    local has_option=false
    local a

    for a in "$@"; do
        if $after_double_dash; then
            targets+=("$a")
            continue
        fi
        if [ "$a" = "--" ]; then
            after_double_dash=true
            continue
        fi
        case "$a" in
            -*) has_option=true ;;
            *)  targets+=("$a") ;;
        esac
    done

    # No targets (e.g. "rmdir --help") → let real rmdir handle
    if [ ${#targets[@]} -eq 0 ]; then
        exec "$REAL_RMDIR" "$@"
    fi
    # rmdir 的选项（-p/--ignore-fail-on-non-empty 等）会改变删除语义；
    # 不能回退真删，只能 fail-closed。
    if $has_option; then
        echo "rmdir: options are not supported by safe-delete" >&2
        return 1
    fi

    safe_delete_validate_targets "${targets[@]}" || return $?
    local _exit=0
    local trash_targets=()
    local p
    for p in "${targets[@]}"; do
        if [ ! -e "$p" ] && [ ! -L "$p" ]; then
            if safe_delete_was_deleted_before_bulk_replay "$p"; then
                continue
            fi
            echo "rmdir: $p: No such file or directory" >&2
            _exit=1
            continue
        fi
        if [ -L "$p" ] || [ ! -d "$p" ]; then
            echo "rmdir: $p: Not a directory" >&2
            _exit=1
            continue
        fi
        if ! is_dir_empty "$p"; then
            echo "rmdir: $p: Directory not empty" >&2
            _exit=1
            continue
        fi
        trash_targets+=("$p")
    done
    if ! safe_delete_bulk_guard_non_tmp_check "${trash_targets[@]}"; then
        return 1
    fi
    for p in "${trash_targets[@]}"; do
        if safe_delete_is_under_os_tmp_dir "$p"; then
            if ! "$REAL_RMDIR" -- "$p"; then
                _exit=1
            fi
        elif ! try_trash "$p"; then
            _exit=1
        fi
    done
    return $_exit
}

safe_delete_main() {
    local mode="$1"
    shift
    safe_delete_check_program_policy "$mode"
    local program_policy_rc=$?
    [ $program_policy_rc -eq 0 ] || return $program_policy_rc
    case "$mode" in
        rm)     safe_delete_rm "$@"; return $? ;;
        unlink) safe_delete_unlink "$@"; return $? ;;
        rmdir)  safe_delete_rmdir "$@"; return $? ;;
        *)
            echo "[safe-delete] unknown mode: $mode" >&2
            return 2
            ;;
    esac
}
