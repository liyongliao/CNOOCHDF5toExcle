"""Record shipped runtime files and reject unwanted desktop dependencies."""
import argparse
import hashlib
import json
from pathlib import Path


def category(relative):
    lowered = relative.lower()
    if "numpy" in lowered or "openblas" in lowered:
        return "NumPy 数值计算"
    if "h5py" in lowered or "hdf5" in lowered:
        return "HDF5 数据读取"
    if "customtkinter" in lowered:
        return "现代界面资源"
    if any(value in lowered for value in ("tcl", "tk8", "tk86", "_tkinter")):
        return "窗口运行库"
    if lowered.endswith((".md", ".txt")):
        return "文档与许可"
    return "应用及 Python 运行库"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="dist/H5ToExcelConverter")
    parser.add_argument("--report", default="test-reports/package-manifest.json")
    arguments = parser.parse_args()
    root = Path(arguments.directory)
    files = []
    totals = {}
    forbidden = ("pydantic", "fastapi", "uvicorn", "starlette", "pandas", "scipy", "libssl", "libcrypto")
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if any(part in relative.lower() for part in forbidden):
            raise RuntimeError("Desktop includes unwanted dependency: " + relative)
        if path.suffix.lower() == ".zip" and path.name != "base_library.zip":
            raise RuntimeError("Unexpected nested archive: " + relative)
        size = path.stat().st_size
        purpose = category(relative)
        totals[purpose] = totals.get(purpose, 0) + size
        files.append({"path": relative, "bytes": size, "purpose": purpose,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if not files:
        raise RuntimeError("No application files found")
    report = {"status": "passed", "bytes": sum(item["bytes"] for item in files),
              "file_count": len(files), "categories": totals, "files": files}
    destination = Path(arguments.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Verified {} runtime files, {:.1f} MiB".format(len(files), report["bytes"] / 1024**2))


if __name__ == "__main__":
    main()
