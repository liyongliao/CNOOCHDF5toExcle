from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import h5py
import numpy as np
import pandas as pd
import datetime
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(title="HDF5 to Excel Converter Backend")

# 获取当前脚本所在绝对路径（如果是 PyInstaller 打包后的环境，使用 sys._MEIPASS 作为静态资源根路径）
import sys
if getattr(sys, 'frozen', False):
    base_dir = sys._MEIPASS
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

# 内存中保存的导出任务状态
TASKS = {}
tasks_lock = threading.Lock()

# 限制并发导出线程数为 4
executor = ThreadPoolExecutor(max_workers=4)

# 启发式识别时间列的关键词列表
TIME_KEYWORDS = ["time", "timestamp", "datetime", "date", "t", "epoch", "sec", "utc", "elapsed"]

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

# ----------------------------------------------------------------
# 核心辅助函数
# ----------------------------------------------------------------

def find_datasets(group, prefix="") -> list:
    """递归查找 H5 文件中所有可导出的一维、等价一维的数据集"""
    datasets = []
    for name, item in group.items():
        path = f"{prefix}/{name}" if prefix else name
        if isinstance(item, h5py.Dataset):
            shape = item.shape
            if len(shape) != 1 and not (len(shape) == 2 and (shape[0] == 1 or shape[1] == 1)):
                continue
                
            size = int(shape[0]) if len(shape) == 1 else int(max(shape))
            
            # 判断是否为复合结构体 (Compound) 类型数据，如 [('time', '<u4'), ('value', '<f4')]
            if item.dtype.names is not None:
                # 检查是否包含时间字段和数值字段
                has_time = any(any(t_kw in n.lower() for t_kw in TIME_KEYWORDS) for n in item.dtype.names)
                non_time_fields = [n for n in item.dtype.names if not any(t_kw in n.lower() for t_kw in TIME_KEYWORDS)]
                
                if has_time and len(non_time_fields) > 0:
                    # 作为一个整体的时间序列数据集显示，不单独展开其内部子字段
                    datasets.append({
                        "path": path,
                        "shape": shape,
                        "dtype": "Compound (Time Series)",
                        "size": size
                    })
                else:
                    # 否则，展开子字段
                    for sub_name in item.dtype.names:
                        datasets.append({
                            "path": f"{path}:{sub_name}",
                            "shape": shape,
                            "dtype": str(item.dtype[sub_name]),
                            "size": size
                        })
            else:
                datasets.append({
                    "path": path,
                    "shape": shape,
                    "dtype": str(item.dtype),
                    "size": size
                })
        elif isinstance(item, h5py.Group):
            datasets.extend(find_datasets(item, path))
    return datasets

def is_time_field(field_path: str) -> bool:
    """启发式判断某个字段路径是否为时间字段"""
    name = field_path.split("/")[-1].replace(":", "_").lower()
    if "temp" in name:
        return False
    if name in TIME_KEYWORDS:
        return True
    tokens = name.replace("-", "_").split("_")
    for token in tokens:
        if token in TIME_KEYWORDS:
            return True
    for kw in ["time", "timestamp", "datetime", "date", "epoch", "elapsed"]:
        if kw in name:
            return True
    return False

def detect_time_dataset(datasets: list) -> Optional[str]:
    """启发式匹配时间列"""
    for ds in datasets:
        name = ds["path"].split("/")[-1].replace(":", "_").lower()
        if name in TIME_KEYWORDS:
            return ds["path"]
    for ds in datasets:
        if is_time_field(ds["path"]):
            return ds["path"]
    return None

def parse_time_array(time_array) -> tuple:
    """解析时间列的格式、最小值、最大值"""
    if len(time_array) == 0:
        return "empty", None, None
        
    first_val = time_array[0]
    if isinstance(first_val, (bytes, str)):
        try:
            sample_str = first_val.decode('utf-8') if isinstance(first_val, bytes) else first_val
            pd.to_datetime(sample_str)
            return "string", None, None
        except Exception:
            return "unknown_string", None, None
            
    try:
        min_val = float(np.min(time_array))
        max_val = float(np.max(time_array))
    except Exception:
        return "error_numeric", None, None
        
    if 1e9 < min_val < 3e9:
        return "timestamp_seconds", min_val, max_val
    elif 1e12 < min_val < 3e12:
        return "timestamp_ms", min_val, max_val
    else:
        return "relative_seconds", min_val, max_val

def convert_time_array_to_float_timestamps(t_orig_raw, t_type, baseDate) -> np.ndarray:
    if t_type in ["string", "unknown_string"]:
        t_orig_strs = []
        for x in t_orig_raw:
            if isinstance(x, bytes):
                try:
                    t_orig_strs.append(x.decode('utf-8'))
                except Exception:
                    t_orig_strs.append("")
            elif isinstance(x, str):
                t_orig_strs.append(x)
            else:
                if hasattr(x, 'decode'):
                    try:
                        t_orig_strs.append(x.decode('utf-8'))
                    except Exception:
                        t_orig_strs.append("")
                else:
                    t_orig_strs.append(str(x))
        s = pd.to_datetime(t_orig_strs, errors='coerce')
        t_orig = s.values.astype('datetime64[s]').astype(float)
        t_orig[t_orig < 0] = np.nan
        return t_orig
        
    try:
        t_orig = t_orig_raw.astype(float)
    except Exception:
        t_orig_strs = []
        for x in t_orig_raw:
            if hasattr(x, 'decode'):
                try:
                    t_orig_strs.append(x.decode('utf-8'))
                except Exception:
                    t_orig_strs.append("")
            else:
                t_orig_strs.append(str(x))
        s = pd.to_datetime(t_orig_strs, errors='coerce')
        t_orig = s.values.astype('datetime64[s]').astype(float)
        t_orig[t_orig < 0] = np.nan
        return t_orig

    if t_type == "timestamp_ms":
        t_orig = t_orig / 1000.0
    elif t_type == "relative_seconds" and baseDate:
        try:
            base_ts = pd.to_datetime(baseDate).tz_localize('UTC').timestamp()
            t_orig = t_orig + base_ts
        except Exception:
            pass
    return t_orig

def find_nearest_indices(t_orig: np.ndarray, t_grid: np.ndarray) -> np.ndarray:
    """高效的最邻近插值匹配算法"""
    idx = np.searchsorted(t_orig, t_grid)
    idx = np.clip(idx, 0, len(t_orig) - 1)
    
    idx_prev = np.clip(idx - 1, 0, len(t_orig) - 1)
    dist_curr = np.abs(t_orig[idx] - t_grid)
    dist_prev = np.abs(t_orig[idx_prev] - t_grid)
    
    final_indices = np.where(dist_curr < dist_prev, idx, idx_prev)
    return final_indices

def save_to_excel_with_meta(df: pd.DataFrame, output_path: str, meta_line: str):
    """保存 DataFrame 到 Excel 中，支持首行写元数据，并在超出 104 万行时自动分表"""
    max_rows_per_sheet = 1040000
    num_rows = len(df)
    
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        if num_rows <= max_rows_per_sheet:
            ws = writer.book.create_sheet(title="Data")
            ws.cell(row=1, column=1, value=meta_line)
            df.to_excel(writer, sheet_name="Data", startrow=1, index=False)
            if "Sheet" in writer.book.sheetnames:
                writer.book.remove(writer.book["Sheet"])
        else:
            num_sheets = (num_rows + max_rows_per_sheet - 1) // max_rows_per_sheet
            for i in range(num_sheets):
                start_row = i * max_rows_per_sheet
                end_row = min((i + 1) * max_rows_per_sheet, num_rows)
                df_chunk = df.iloc[start_row:end_row]
                sheet_name = f"Data_Part{i+1}"
                
                ws = writer.book.create_sheet(title=sheet_name)
                ws.cell(row=1, column=1, value=meta_line)
                df_chunk.to_excel(writer, sheet_name=sheet_name, startrow=1, index=False)
                
            if "Sheet" in writer.book.sheetnames:
                writer.book.remove(writer.book["Sheet"])

def read_field_array(f, field: str) -> np.ndarray:
    """读取 H5 字段数据，支持复合字段（冒号分隔）、未展开复合字段的数据部分提取"""
    if ":" in field:
        ds_path, sub_field = field.split(":", 1)
        ds = f[ds_path]
        val_arr = ds[sub_field][:]
    else:
        ds = f[field]
        if ds.dtype.names is not None:
            # 复合结构体未展开，寻找首个非时间分量作为数值
            non_time_fields = [n for n in ds.dtype.names if not any(t_kw in n.lower() for t_kw in TIME_KEYWORDS)]
            if non_time_fields:
                val_arr = ds[non_time_fields[0]][:]
            else:
                val_arr = ds[ds.dtype.names[0]][:]
        else:
            val_arr = ds[:]
            
    if len(val_arr.shape) == 2:
        val_arr = val_arr[0, :] if val_arr.shape[0] == 1 else val_arr[:, 0]
    return val_arr

def find_time_array_for_field(f, field_path: str) -> tuple:
    """寻找字段对应的时间列"""
    if ":" in field_path:
        ds_path, sub_field = field_path.split(":", 1)
        try:
            ds = f[ds_path]
            if ds.dtype.names is not None:
                for name in ds.dtype.names:
                    if name.lower() in TIME_KEYWORDS:
                        return f"{ds_path}:{name}", ds[name][:]
        except Exception:
            pass
    else:
        try:
            ds = f[field_path]
            if ds.dtype.names is not None:
                for name in ds.dtype.names:
                    if any(t_kw in name.lower() for t_kw in TIME_KEYWORDS):
                        return f"{field_path}:{name}", ds[name][:]
        except Exception:
            pass

    base_field = field_path.split(":", 1)[0] if ":" in field_path else field_path
    parent_path = os.path.dirname(base_field)
    base_name = os.path.basename(base_field)
    
    try:
        parent_group = f[parent_path] if parent_path else f
        for name in parent_group.keys():
            if name.lower() == f"{base_name.lower()}_time" or name.lower() == f"{base_name.lower()}_timestamp":
                item = parent_group[name]
                if isinstance(item, h5py.Dataset):
                    return f"{parent_path}/{name}" if parent_path else name, item[:]
        
        val_len = len(read_field_array(f, field_path))
        for name, item in parent_group.items():
            if isinstance(item, h5py.Dataset) and name.lower() in TIME_KEYWORDS:
                arr = item[:]
                if len(arr.shape) == 2:
                    arr = arr[0, :] if arr.shape[0] == 1 else arr[:, 0]
                if len(arr) == val_len:
                    return f"{parent_path}/{name}" if parent_path else name, arr
    except Exception:
        pass
        
    return None, None

def determine_field_type_and_unit(f, field_path: str, default_temp_unit: str, default_pres_unit: str) -> tuple:
    """判断字段类型，返回单位名和单位转换函数"""
    base_field = field_path.split(":", 1)[0] if ":" in field_path else field_path
    ds = f[base_field]
    
    measurement_type = ""
    uom = ""
    if "Measurement Type" in ds.attrs:
        measurement_type = str(ds.attrs["Measurement Type"]).lower()
    if "UoM" in ds.attrs:
        uom = str(ds.attrs["UoM"]).lower()
        
    name = base_field.split("/")[-1].lower()
    
    is_pressure = False
    if "pressure" in measurement_type or uom == "pa" or "pres" in name:
        is_pressure = True
        
    is_temperature = False
    if "temperature" in measurement_type or uom in ["°k", "k", "c", "°c", "degc"] or "temp" in name:
        is_temperature = True
        
    if is_pressure:
        unit = default_pres_unit
        original_is_psi = ("psi" in uom)
        
        if original_is_psi:
            if unit == "PSI":
                convert_func = lambda x: x
            elif unit == "MPa":
                convert_func = lambda x: x * 0.006894757293
            elif unit == "kPa":
                convert_func = lambda x: x * 6.894757293
            elif unit == "bar":
                convert_func = lambda x: x * 0.06894757293
            else:
                unit = "Pa"
                convert_func = lambda x: x * 6894.757293
        else:
            if unit == "PSI":
                convert_func = lambda x: x * 0.00014503773773
            elif unit == "MPa":
                convert_func = lambda x: x / 1000000.0
            elif unit == "kPa":
                convert_func = lambda x: x / 1000.0
            elif unit == "bar":
                convert_func = lambda x: x / 100000.0
            else:
                unit = "Pa"
                convert_func = lambda x: x
        return "pressure", unit, convert_func
        
    elif is_temperature:
        unit = default_temp_unit
        is_origin_celsius = "c" in uom or "celsius" in uom
        
        if unit == "degC":
            convert_func = lambda x: x if is_origin_celsius else (x - 273.15)
        elif unit == "degF":
            convert_func = lambda x: (x if is_origin_celsius else (x - 273.15)) * 1.8 + 32.0
        elif unit in ["K", "°K"]:
            convert_func = lambda x: (x + 273.15) if is_origin_celsius else x
        else:
            unit = "degC"
            convert_func = lambda x: x if is_origin_celsius else (x - 273.15)
        return "temperature", unit, convert_func
    else:
        return "other", "", lambda x: x

def get_column_header(f, field_path: str, default_temp_unit: str, default_pres_unit: str) -> str:
    """生成带括弧单位的列名，如 EQRTZ S1 PRES PSI A (PSI)"""
    base_field = field_path.split(":", 1)[0] if ":" in field_path else field_path
    ds = f[base_field]
    ds_name = base_field.split("/")[-1]
    
    measurement_type = str(ds.attrs.get("Measurement Type", "")).lower()
    uom = str(ds.attrs.get("UoM", "")).lower()
    
    unit_suffix = ""
    if "pressure" in measurement_type or uom == "pa" or "pres" in ds_name.lower():
        unit_suffix = f" ({default_pres_unit})"
    elif "temperature" in measurement_type or uom in ["°k", "k", "c", "°c", "degc"] or "temp" in ds_name.lower():
        unit_suffix = f" ({default_temp_unit})"
    elif "electric current" in measurement_type:
        unit_suffix = " (A)"
    elif "electric potential" in measurement_type:
        unit_suffix = " (Volt)"
    elif uom:
        uom_upper = ds.attrs.get("UoM", "")
        if uom_upper:
            unit_suffix = f" ({uom_upper})"
    return f"{ds_name}{unit_suffix}"

def parse_filename_metadata(filename: str) -> tuple:
    """解析文件名，提取 Well Name、Sn 和 Version"""
    base = os.path.basename(filename)
    sn = ""
    well = ""
    version = "2.110r512"
    
    parts = base.split("-")
    if len(parts) >= 2:
        first = parts[0]
        if "_" in first:
            sn = first.split("_")[-1]
        else:
            sn = first
        well = parts[1]
    return well, sn, version

# ----------------------------------------------------------------
# 异步导出工作线程
# ----------------------------------------------------------------

def export_task_worker(task_id: str, cfg: ExportConfig, output_dir: str):
    def update_status(progress: int, message: str, status: str = "running", error: str = None):
        with tasks_lock:
            if task_id in TASKS:
                TASKS[task_id]["progress"] = progress
                TASKS[task_id]["message"] = message
                TASKS[task_id]["status"] = status
                if error:
                    TASKS[task_id]["error"] = error

    update_status(5, "正在初始化导出任务...")
    
    try:
        output_path = os.path.join(output_dir, cfg.customName)
        is_csv = output_path.lower().endswith(".csv")
        if not is_csv and not output_path.lower().endswith(".xlsx") and not output_path.lower().endswith(".xls"):
            output_path += ".xlsx"
            
        update_status(10, "正在打开 HDF5 文件...")
        
        with h5py.File(cfg.filePath, "r") as f:
            for field in cfg.selectedFields:
                base_field = field.split(":", 1)[0] if ":" in field else field
                if base_field not in f:
                    raise ValueError(f"文件内未找到字段: {base_field}")
            
            update_status(15, "正在确定全局时间轴范围...")
            min_times = []
            max_times = []
            
            for field in cfg.selectedFields:
                t_field_path, t_orig_raw = find_time_array_for_field(f, field)
                if t_orig_raw is not None and len(t_orig_raw) > 0:
                    t_type, _, _ = parse_time_array(t_orig_raw)
                    t_orig = convert_time_array_to_float_timestamps(t_orig_raw, t_type, cfg.baseDate)
                    t_orig_valid = t_orig[~np.isnan(t_orig)]
                    if len(t_orig_valid) > 0:
                        min_times.append(np.min(t_orig_valid))
                        max_times.append(np.max(t_orig_valid))
            
            try:
                start_ts = pd.to_datetime(cfg.startTimeStr).tz_localize('UTC').timestamp() if cfg.startTimeStr else (min(min_times) if min_times else 0.0)
                end_ts = pd.to_datetime(cfg.endTimeStr).tz_localize('UTC').timestamp() if cfg.endTimeStr else (max(max_times) if max_times else 0.0)
            except Exception:
                raise ValueError("起止时间格式无效，应为 YYYY-MM-DD HH:MM:SS 或 YYYY/M/D")
                
            interval = cfg.interval if cfg.interval > 0 else 10.0
            t_grid = np.arange(start_ts, end_ts + (interval / 2.0), interval)
            
            if len(t_grid) == 0:
                raise ValueError("计算出的时间网格为空，请检查起止时间是否在数据范围内！")
            if len(t_grid) > 2000000:
                raise ValueError(f"导出数据量过大 ({len(t_grid)} 行)，请缩短时间范围或增大时间间隔！")
                
            update_status(25, "正在构建全局时间网格...")
            t_grid_datetimes = []
            for ts in t_grid:
                dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                t_grid_datetimes.append(f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}:{dt.second:02d}")
                
            data_dict = {"Date time": t_grid_datetimes}
            num_fields = len(cfg.selectedFields)
            
            for idx, field in enumerate(cfg.selectedFields):
                col_title = get_column_header(f, field, cfg.tempUnit, cfg.presUnit)
                update_status(
                    int(30 + 50 * ((idx + 1) / max(num_fields, 1))),
                    f"正在对齐并转换字段: {col_title} ({idx+1}/{num_fields})..."
                )
                
                t_field_path, t_orig_raw = find_time_array_for_field(f, field)
                val_arr = read_field_array(f, field)
                
                if t_orig_raw is not None and len(t_orig_raw) > 0:
                    t_type, _, _ = parse_time_array(t_orig_raw)
                    t_orig = convert_time_array_to_float_timestamps(t_orig_raw, t_type, cfg.baseDate)
                    
                    # Filter out NaN elements so alignment doesn't break
                    valid_mask = ~np.isnan(t_orig)
                    t_orig_clean = t_orig[valid_mask]
                    val_arr_clean = val_arr[valid_mask]
                    
                    if len(t_orig_clean) > 0:
                        nearest_indices = find_nearest_indices(t_orig_clean, t_grid)
                        v_aligned = val_arr_clean[nearest_indices]
                    else:
                        v_aligned = np.full(len(t_grid), np.nan)
                else:
                    if len(val_arr) == len(t_grid):
                        v_aligned = val_arr
                    else:
                        v_aligned = np.full(len(t_grid), np.nan)
                        
                _, _, convert_func = determine_field_type_and_unit(f, field, cfg.tempUnit, cfg.presUnit)
                v_final = convert_func(v_aligned)
                data_dict[col_title] = v_final
                
            update_status(85, "正在整理对齐后的数据表...")
            df = pd.DataFrame(data_dict)
            
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            well, sn, version = parse_filename_metadata(cfg.filePath)
            meta_line = f"Well name:{well}, Sn :{sn} ,  Version :{version}"
            
            if is_csv:
                update_status(90, f"正在将单表数据写入 CSV 文件: {os.path.basename(output_path)}...")
                with open(output_path, "w", encoding="utf-8") as csv_file:
                    csv_file.write(meta_line + "\n")
                    df.to_csv(csv_file, index=False)
            else:
                update_status(90, f"正在将单表数据写入 Excel 文件: {os.path.basename(output_path)}...")
                save_to_excel_with_meta(df, output_path, meta_line)
                
            update_status(100, "导出成功！", status="completed")
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        update_status(100, f"导出失败: {str(e)}", status="failed", error=str(e))

# ----------------------------------------------------------------
# API 路由
# ----------------------------------------------------------------

@app.post("/api/scan")
def scan_directory(payload: ScanPayload):
    """扫描目录下的顶级 H5 文件"""
    path = payload.path.strip()
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail="指定的路径不存在，请检查后重新输入。")
        
    if os.path.isfile(path):
        if path.lower().endswith((".h5", ".hdf5")):
            return {"files": [{
                "path": path,
                "name": os.path.basename(path),
                "size": os.path.getsize(path)
            }]}
        raise HTTPException(status_code=400, detail="输入的文件不是有效的 HDF5 文件。")
        
    try:
        files = []
        for entry in os.scandir(path):
            if entry.is_file() and entry.name.lower().endswith((".h5", ".hdf5")):
                files.append({
                    "path": entry.path,
                    "name": entry.name,
                    "size": entry.stat().st_size
                })
        files.sort(key=lambda x: x["name"])
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描文件夹失败: {str(e)}")

@app.post("/api/browse")
def browse_directory():
    """打开原生系统的选择文件夹对话框"""
    import sys
    import subprocess
    
    path = ""
    error_msg = ""
    
    if sys.platform == "darwin":
        # macOS 优先使用 AppleScript (osascript)
        cmd = "osascript -e 'POSIX path of (choose folder with prompt \"请选择文件夹:\")'"
        try:
            output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
            if output:
                path = output
        except Exception as e_osa:
            # osascript 失败时尝试 tkinter 兜底
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                selected = filedialog.askdirectory()
                root.destroy()
                if selected:
                    path = os.path.abspath(selected)
            except Exception as e_tk:
                error_msg = f"osascript 错误: {str(e_osa)}; Tkinter 错误: {str(e_tk)}"
    elif sys.platform == "win32":
        # Windows 优先使用 tkinter
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
            # tkinter 失败时使用 PowerShell 脚本调用 Windows 原生 FolderBrowserDialog 兜底 (不依赖 tkinter 模块)
            try:
                cmd = 'powershell -NoProfile -Command "Add-Type -AssemblyName System.Windows.Forms; $f = New-Object System.Windows.Forms.FolderBrowserDialog; if ($f.ShowDialog() -eq \'OK\') { $f.SelectedPath }"'
                output = subprocess.check_output(cmd, shell=True).decode('gbk', errors='ignore').strip()
                if output:
                    path = output
            except Exception as e_ps:
                error_msg = f"Tkinter 错误: {str(e_tk)}; PowerShell 错误: {str(e_ps)}"
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

@app.post("/api/inspect")
def inspect_hdf5(payload: InspectPayload):
    """提取单个 H5 文件的结构、时间列和起止范围"""
    path = payload.path.strip()
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail="文件不存在")
        
    try:
        with h5py.File(path, "r") as f:
            datasets = find_datasets(f)
            if not datasets:
                return {
                    "datasets": [],
                    "detectedTimeField": None,
                    "timeType": "none",
                    "timeMinStr": None,
                    "timeMaxStr": None
                }
                
            detected_time = detect_time_dataset(datasets)
            time_type = "none"
            time_min_str = None
            time_max_str = None
            
            if detected_time:
                if ":" in detected_time:
                    ds_path, sub_field = detected_time.split(":", 1)
                    t_ds = f[ds_path]
                    t_arr = t_ds[sub_field][:]
                else:
                    t_ds = f[detected_time]
                    t_arr = t_ds[:]
                
                if len(t_arr.shape) == 2:
                    t_arr = t_arr[0, :] if t_arr.shape[0] == 1 else t_arr[:, 0]
                
                t_type, min_ts, max_ts = parse_time_array(t_arr)
                time_type = t_type
                
                if t_type == "timestamp_seconds" and min_ts is not None:
                    time_min_str = datetime.datetime.fromtimestamp(min_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    time_max_str = datetime.datetime.fromtimestamp(max_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                elif t_type == "timestamp_ms" and min_ts is not None:
                    time_min_str = datetime.datetime.fromtimestamp(min_ts/1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    time_max_str = datetime.datetime.fromtimestamp(max_ts/1000.0, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                elif t_type == "relative_seconds" and min_ts is not None:
                    time_min_str = str(min_ts)
                    time_max_str = str(max_ts)
                elif t_type == "string":
                    try:
                        time_min_str = t_arr[0].decode('utf-8') if isinstance(t_arr[0], bytes) else str(t_arr[0])
                        time_max_str = t_arr[-1].decode('utf-8') if isinstance(t_arr[-1], bytes) else str(t_arr[-1])
                    except Exception:
                        pass
            
            return {
                "datasets": datasets,
                "detectedTimeField": detected_time,
                "timeType": time_type,
                "timeMinStr": time_min_str,
                "timeMaxStr": time_max_str
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析 HDF5 文件失败: {str(e)}")

@app.post("/api/export")
def trigger_export(payload: ExportPayload):
    """接收导出任务并异步执行"""
    if not payload.configs:
        raise HTTPException(status_code=400, detail="没有提交任何导出配置")
        
    output_dir = payload.outputDir.strip()
    if not output_dir:
        raise HTTPException(status_code=400, detail="未指定导出保存的目录")
        
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法创建导出目标目录: {str(e)}")
        
    task_ids = []
    with tasks_lock:
        for cfg in payload.configs:
            task_id = str(uuid.uuid4())
            TASKS[task_id] = {
                "file_name": os.path.basename(cfg.filePath),
                "status": "pending",
                "progress": 0,
                "message": "排队等待中...",
                "error": None,
                "output_path": os.path.join(output_dir, cfg.customName)
            }
            task_ids.append(task_id)
            executor.submit(export_task_worker, task_id, cfg, output_dir)
            
    return {"taskIds": task_ids}

@app.get("/api/status")
def get_status(taskIds: str):
    """查询导出任务进度"""
    ids = taskIds.split(",")
    results = {}
    with tasks_lock:
        for t_id in ids:
            if t_id in TASKS:
                results[t_id] = TASKS[t_id]
            else:
                results[t_id] = {
                    "status": "failed",
                    "progress": 100,
                    "message": "未找到任务",
                    "error": "Task not found"
                }
    return results

@app.post("/api/cancel")
def cancel_task(taskId: str):
    """取消任务"""
    with tasks_lock:
        if taskId in TASKS:
            if TASKS[taskId]["status"] in ["pending", "running"]:
                TASKS[taskId]["status"] = "failed"
                TASKS[taskId]["message"] = "任务已被用户取消"
                TASKS[taskId]["error"] = "Cancelled by user"
                return {"success": True}
    return {"success": False, "detail": "任务已完成或不存在"}

@app.get("/")
def read_root():
    static_index = os.path.join(base_dir, "static", "index.html")
    if os.path.exists(static_index):
        return FileResponse(static_index)
    return JSONResponse(status_code=404, content={"message": "Frontend static file index.html not found."})

static_path = os.path.join(base_dir, "static")
if not os.path.exists(static_path):
    os.makedirs(static_path, exist_ok=True)
    
app.mount("/static", StaticFiles(directory=static_path), name="static")
