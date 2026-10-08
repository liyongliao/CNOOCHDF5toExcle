"""Build on the target OS; Windows releases are built by GitHub Actions."""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import sys


def copy_licenses(destination):
    """Keep dependency license notices in one readable distribution file."""
    notices, seen = [], set()
    for name in ("numpy", "h5py", "openpyxl", "et-xmlfile", "customtkinter",
                 "darkdetect", "packaging", "six", "pyinstaller"):
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        paths = [path for path in distribution.files or []
                 if path.name.lower().startswith(("license", "copying", "notice"))
                 and path.suffix.lower() not in (".py", ".pyc")]
        texts = [distribution.locate_file(path).read_text(encoding="utf-8", errors="replace")
                 for path in paths if distribution.locate_file(path).is_file()]
        if not texts:
            texts = [distribution.read_text("METADATA") or ""]
        for notice in texts:
            if notice and notice not in seen:
                notices.append("{} {}\n{}\n{}".format(name, distribution.version, "=" * 60, notice))
                seen.add(notice)
    for path in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if path.is_file():
            notices.append("Python\n" + path.read_text(encoding="utf-8", errors="replace"))
            break
    (destination / "第三方许可.txt").write_text("\n\n".join(notices), encoding="utf-8")


def build_executable(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="构建免安装桌面软件")
    parser.add_argument("--onefile", action="store_true", help="单文件模式（启动时需要解压，较慢）")
    parser.add_argument("--console", action="store_true", help="保留调试控制台")
    arguments = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    import PyInstaller

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--noupx",
               "--onefile" if arguments.onefile else "--onedir", "--name=H5ToExcelConverter",
               "--optimize=1", "--log-level=WARN", "--collect-binaries=h5py",
               "--collect-data=customtkinter"]
    separator = ";" if sys.platform == "win32" else ":"
    for source, target in (("config.default.json", "."),):
        command.append("--add-data={}{}{}".format(root / source, separator, target))
    # The desktop entry never imports the optional web interface. Exclusions
    # also prevent optional scientific/image integrations from growing the app.
    for excluded in ("app", "fastapi", "pydantic", "pydantic_core", "uvicorn",
                     "starlette", "anyio", "h11", "httpx", "PIL", "ssl", "_ssl", "_hashlib",
                     "pandas", "matplotlib", "scipy", "IPython", "notebook", "pytest"):
        command.append("--exclude-module=" + excluded)
    if sys.platform == "win32":
        command.append("--version-file=" + str(root / "windows-version.txt"))
        if not arguments.console:
            command.append("--windowed")
    command.append(str(root / "desktop_entry.py"))
    subprocess.check_call(command, cwd=str(root))
    destination = root / "dist" if arguments.onefile else root / "dist" / "H5ToExcelConverter"
    shutil.copyfile(root / "README.md", destination / "使用说明.md")
    shutil.copyfile(root / "软件文件说明.txt", destination / "软件文件说明.txt")
    copy_licenses(destination)
    print("构建完成：{}（PyInstaller {}）".format(destination, PyInstaller.__version__))
    return 0


if __name__ == "__main__":
    sys.exit(build_executable())
