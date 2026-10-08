"""Build on the target OS; Windows releases are built by GitHub Actions."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


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
               "--optimize=1", "--log-level=WARN", "--collect-binaries=h5py"]
    separator = ";" if sys.platform == "win32" else ":"
    for source, target in (("static", "static"), ("config.default.json", ".")):
        command.append("--add-data={}{}{}".format(root / source, separator, target))
    for excluded in ("pandas", "matplotlib", "scipy", "IPython", "notebook", "pytest"):
        command.append("--exclude-module=" + excluded)
    if sys.platform == "win32":
        command.append("--version-file=" + str(root / "windows-version.txt"))
        if not arguments.console:
            command.append("--windowed")
    command.append(str(root / "run.py"))
    subprocess.check_call(command, cwd=str(root))
    destination = root / "dist" if arguments.onefile else root / "dist" / "H5ToExcelConverter"
    shutil.copyfile(root / "README.md", destination / "使用说明.md")
    print("构建完成：{}（PyInstaller {}）".format(destination, PyInstaller.__version__))
    return 0


if __name__ == "__main__":
    sys.exit(build_executable())
