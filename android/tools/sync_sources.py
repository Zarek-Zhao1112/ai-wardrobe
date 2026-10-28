"""只同步构建目录的app/web，移除过期副本并排除宿主字节码。"""
from pathlib import Path
import shutil
import sys


def validate_build(repo: Path, build: Path) -> tuple[Path, Path]:
    repo, build = repo.resolve(), build.resolve()
    if repo == build or repo.is_relative_to(build) or build.is_relative_to(repo):
        raise ValueError("构建目录必须独立于源码目录")
    base = build / "app/src/main/python"
    if not base.resolve().is_relative_to(build):
        raise ValueError("构建副本路径越界")
    # 在shell复制任何骨架前检查目标树，防止res/java/server.py经链接写到外部。
    for target in [build / "app/src/main", build / "wheels", *[build / name for name in
                   ("settings.gradle", "build.gradle", "gradle.properties", "local.properties", "app/build.gradle")]]:
        paths = [target, *target.rglob("*")] if target.is_dir() else [target]
        if any(not path.resolve().is_relative_to(build) for path in paths):
            raise ValueError("构建骨架包含目录外链接")
    return repo, build


def sync_sources(repo: Path, build: Path) -> None:
    repo, build = validate_build(repo, build)
    base = build / "app/src/main/python"
    for name in ("app", "web"):
        source, target = repo / name, base / name
        if not source.is_dir():
            raise ValueError(f"缺少源码目录 {name}")
        if not target.resolve().is_relative_to(base.resolve()):
            raise ValueError("目标目录不能链接到构建目录外")
        files = {p.relative_to(source): p for p in source.rglob("*") if p.is_file()
                 and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
        target.mkdir(parents=True, exist_ok=True)
        for path in target.rglob("*"):
            if not path.resolve().is_relative_to(target.resolve()):
                raise ValueError("构建副本含外部链接")
            if path.is_file() and path.relative_to(target) not in files:
                path.unlink()
        for rel, path in files.items():
            if path.is_symlink():
                raise ValueError("源码文件不能是外部链接")
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            if path.read_bytes() != dest.read_bytes():
                raise RuntimeError(f"同步后不一致：{name}/{rel}")
        for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[3] == "--check":
        validate_build(Path(sys.argv[1]), Path(sys.argv[2]))
        print("构建目标路径预检通过")
    elif len(sys.argv) > 3 and sys.argv[3] == "--clear-env":
        _, build = validate_build(Path(sys.argv[1]), Path(sys.argv[2]))
        path = build / "app/src/main/python/.env"
        if path.is_file():
            path.unlink()
    else:
        sync_sources(Path(sys.argv[1]), Path(sys.argv[2]))
        print("app/web源码副本精确同步，过期文件与宿主字节码已排除")
