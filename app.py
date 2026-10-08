"""Optional FastAPI adapter; the standalone desktop build does not import it."""
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import converter as core

app = FastAPI(title="HDF5 to Excel Converter Backend")
base_dir = core.base_dir


class ScanPayload(BaseModel):
    path: str


class InspectPayload(BaseModel):
    path: str


class ExportConfig(BaseModel):
    filePath: str
    selectedFields: List[str]
    timeField: Optional[str] = None
    timeType: Optional[str] = None
    startTimeStr: Optional[str] = None
    endTimeStr: Optional[str] = None
    baseDate: Optional[str] = "1970-01-01 00:00:00"
    interval: float = 10.0
    customName: str
    tempUnit: Optional[str] = "degC"
    presUnit: Optional[str] = "PSI"


class ExportPayload(BaseModel):
    configs: List[ExportConfig]
    outputDir: str



def _model_values(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _invoke(function, *args):
    try:
        return function(*args)
    except core.ConversionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@app.post("/api/scan")
def scan_directory(payload: ScanPayload):
    return _invoke(core.scan_directory, core.ScanPayload(path=payload.path))


@app.post("/api/inspect")
def inspect_hdf5(payload: InspectPayload):
    return _invoke(core.inspect_hdf5, core.InspectPayload(path=payload.path))


@app.post("/api/export")
def trigger_export(payload: ExportPayload):
    return _invoke(lambda: core.trigger_export(core.ExportPayload(**_model_values(payload))))


@app.get("/api/status")
def get_status(taskIds: str):
    return _invoke(core.get_status, taskIds)


@app.post("/api/cancel")
def cancel_task(taskId: str):
    return _invoke(core.cancel_task, taskId)


@app.get("/api/config")
def get_config():
    return _invoke(core.get_config)


@app.post("/api/config")
def save_config(payload: dict):
    return _invoke(core.save_config, payload)


@app.post("/api/browse")
def browse_directory():
    """打开原生系统的选择文件夹对话框"""
    import sys
    import subprocess
    
    path = ""
    error_msg = ""
    
    if sys.platform == "darwin":
        # macOS 优先使用 AppleScript (osascript)，不使用 shell=True 以避免加载环境变量导致的慢启动
        cmd = ["osascript", "-e", 'POSIX path of (choose folder with prompt "请选择文件夹:")']
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode('utf-8').strip()
            if output:
                path = output
        except Exception as e_osa:
            err_out = ""
            is_cancel = False
            if isinstance(e_osa, subprocess.CalledProcessError):
                err_out = e_osa.output.decode('utf-8', errors='ignore') if e_osa.output else ""
                if "-128" in err_out or "canceled" in err_out.lower() or "取消" in err_out:
                    is_cancel = True
            
            if is_cancel:
                # 用户主动取消，干净返回，不显示报错弹窗
                return {"path": ""}
                
            # osascript 真实报错或非 Cancel 时，尝试 tkinter 兜底
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                selected = filedialog.askdirectory()
                root.destroy()
                if selected:
                    path = os.path.abspath(selected)
            except Exception as e_tk:
                error_msg = f"osascript 错误: {err_out if err_out else str(e_osa)}; Tkinter 错误: {str(e_tk)}"
    elif sys.platform == "win32":
        # Windows 优先使用 win32 ctypes 接口，实现毫秒级瞬时弹窗 (不依赖任何外部 GUI/Shell 进程)
        try:
            import ctypes
            from ctypes import wintypes
            
            class BROWSEINFO(ctypes.Structure):
                _fields_ = [
                    ("hwndOwner", ctypes.c_void_p),
                    ("pidlRoot", ctypes.c_void_p),
                    ("pszDisplayName", ctypes.c_wchar_p),
                    ("lpszTitle", ctypes.c_wchar_p),
                    ("ulFlags", ctypes.c_uint),
                    ("lpfn", ctypes.c_void_p),
                    ("lParam", ctypes.c_void_p),
                    ("iImage", ctypes.c_int)
                ]
            
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            user32 = ctypes.windll.user32
            
            # 必须显式设置 restype 和 argtypes，防止 64 位 Windows 系统下指针被截断为 32 位 int 导致崩溃并触发 Tkinter 兜底
            shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
            shell32.SHBrowseForFolderW.argtypes = [ctypes.c_void_p]
            
            shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
            shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            
            ole32.CoTaskMemFree.restype = None
            ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.GetForegroundWindow.argtypes = []
            
            bi = BROWSEINFO()
            # 获取当前浏览器/前台窗口句柄，使弹窗显示在最前端，防止隐藏在浏览器后面
            bi.hwndOwner = user32.GetForegroundWindow()
            bi.pidlRoot = None
            bi.pszDisplayName = None
            bi.lpszTitle = "请选择文件夹:"
            bi.ulFlags = 0x0001 | 0x0040  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
            bi.lpfn = None
            bi.lParam = None
            bi.iImage = 0
            
            pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
            if pidl:
                path_buf = ctypes.create_unicode_buffer(260)
                if shell32.SHGetPathFromIDListW(pidl, path_buf):
                    path = path_buf.value
                ole32.CoTaskMemFree(pidl)
        except Exception as e_c:
            # win32 API 异常时，以 tkinter 动作做第一级备份
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                selected = filedialog.askdirectory()
                root.destroy()
                if selected:
                    path = os.path.abspath(selected)
            except Exception as e_tk:
                # 依然失败时使用 PowerShell 脚本做终极兜底
                try:
                    cmd = 'powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; if ($f.ShowDialog() -eq \'OK\') { $f.SelectedPath }"'
                    output = subprocess.check_output(cmd, shell=True).decode('gbk', errors='ignore').strip()
                    if output:
                        path = output
                except Exception as e_ps:
                    error_msg = f"Ctypes 错误: {str(e_c)}; Tkinter 错误: {str(e_tk)}; PowerShell 错误: {str(e_ps)}"
    else:
        # 其他系统使用 tkinter
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            selected = filedialog.askdirectory()
            root.destroy()
            if selected:
                path = os.path.abspath(selected)
        except Exception as e:
            error_msg = str(e)
            
    if path:
        return {"path": path}
    if error_msg:
        print(f"打开文件夹选择器失败: {error_msg}")
        return {"path": "", "error": error_msg}
    return {"path": ""}



@app.get("/")
def read_root():
    index = os.path.join(base_dir, "static", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"message": "Frontend static file index.html not found."})


static_path = os.path.join(base_dir, "static")
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")
