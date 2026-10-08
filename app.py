"""HDF5 conversion service shared by the desktop application and optional web UI."""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
import csv
import datetime
import json
import math
import os
import re
import struct
import sys
import tempfile
import threading
import time
import uuid

import h5py
import numpy as np

app = FastAPI(title="HDF5 to Excel Converter Backend")
base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
app_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else base_dir
IS_32_BIT = struct.calcsize("P") == 4
MAX_EXPORT_ROWS = 2_000_000
MAX_SOURCE_MEMORY = 384 * 1024**2 if IS_32_BIT else 1024**3
CHUNK_ROWS = 8192
MAX_CHUNK_MEMORY = (16 if IS_32_BIT else 32) * 1024**2
MAX_ROWS_PER_SHEET = 1_040_000
MAX_TASK_HISTORY = 100
MAX_ACTIVE_TASKS = 64
TIME_KEYWORDS = {"time", "timestamp", "datetime", "date", "t", "epoch", "sec", "utc", "elapsed"}
TIME_TYPES = {"timestamp_seconds", "timestamp_ms", "relative_seconds", "string", "unknown_string"}
TASKS = {}
_TASK_CANCEL = {}
_TASK_FUTURES = {}
tasks_lock = threading.RLock()
executor = ThreadPoolExecutor(max_workers=1 if IS_32_BIT else 2, thread_name_prefix="hdf5-export")


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


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def is_time_field(field_path: str) -> bool:
    """Recognise time tokens without treating the 't' in 'value' as time."""
    name = field_path.rsplit(":", 1)[-1].split("/")[-1].lower()
    if "temp" in name:
        return False
    tokens = re.split(r"[^a-z0-9]+", name)
    return any(token in TIME_KEYWORDS for token in tokens) or any(
        keyword in name for keyword in ("timestamp", "datetime", "elapsed", "epoch")
    ) or name.startswith("time") or name.endswith("time")


def _vector_size(ds):
    shape = ds.shape
    if shape is None:
        return None
    if len(shape) == 1:
        return shape[0]
    if len(shape) == 2 and 1 in shape:
        return shape[0] * shape[1]
    return None


def find_datasets(group, prefix="") -> list:
    datasets = []
    for name, item in group.items():
        path = f"{prefix}/{name}" if prefix else name
        if isinstance(item, h5py.Group):
            datasets.extend(find_datasets(item, path))
            continue
        if not isinstance(item, h5py.Dataset):
            continue
        size = _vector_size(item)
        if size is None:
            continue
        entry = {"path": path, "shape": item.shape, "dtype": str(item.dtype), "size": size}
        if item.dtype.names:
            times = [n for n in item.dtype.names if is_time_field(n)]
            values = [n for n in item.dtype.names if not is_time_field(n)]
            if times and len(values) == 1:
                entry.update(dtype="Compound (Time Series)", timeField=f"{path}:{times[0]}")
                datasets.append(entry)
            else:
                for member in item.dtype.names:
                    datasets.append(dict(entry, path=f"{path}:{member}", dtype=str(item.dtype[member])))
        else:
            datasets.append(entry)
    return datasets


def detect_time_dataset(datasets: list) -> Optional[str]:
    for ds in datasets:
        if ds["path"].rsplit(":", 1)[-1].split("/")[-1].lower() in TIME_KEYWORDS:
            return ds.get("timeField") or ds["path"]
    for ds in datasets:
        if is_time_field(ds["path"]):
            return ds.get("timeField") or ds["path"]
    for ds in datasets:
        if ds.get("timeField"):
            return ds["timeField"]
    return None


def _date_timestamp(value):
    text = _text(value).strip()
    if not text:
        raise ValueError("时间不能为空")
    text = text.replace("/", "-")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(text)
    except ValueError:
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d %H:%M:%S"):
            try:
                dt = datetime.datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValueError("时间格式无效，应为 YYYY-MM-DD HH:MM:SS 或 YYYY/M/D")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def parse_time_array(time_array) -> tuple:
    arr = np.asarray(time_array).reshape(-1)
    if not arr.size:
        return "empty", None, None
    if arr.dtype.kind in "SUO":
        for value in arr[:32]:
            try:
                _date_timestamp(value)
                return "string", None, None
            except (ValueError, TypeError, OverflowError):
                continue
        return "unknown_string", None, None
    try:
        values = arr.astype(np.float64, copy=False)
        valid = values[np.isfinite(values)]
        if not valid.size:
            return "error_numeric", None, None
        low, high = float(np.min(valid)), float(np.max(valid))
    except (TypeError, ValueError):
        return "error_numeric", None, None
    if 1e9 < low < 3e9:
        return "timestamp_seconds", low, high
    if 1e12 < low < 3e12:
        return "timestamp_ms", low, high
    return "relative_seconds", low, high


def convert_time_array_to_float_timestamps(t_orig_raw, t_type, baseDate) -> np.ndarray:
    raw = np.asarray(t_orig_raw).reshape(-1)
    if t_type in ("string", "unknown_string") or raw.dtype.kind in "SUO":
        result = np.full(len(raw), np.nan, dtype=np.float64)
        for index, value in enumerate(raw):
            try:
                result[index] = _date_timestamp(value)
            except (ValueError, TypeError, OverflowError):
                pass
        return result
    result = raw.astype(np.float64, copy=False)
    if t_type == "timestamp_ms":
        return result / 1000.0
    if t_type == "relative_seconds":
        return result + _date_timestamp(baseDate or "1970-01-01 00:00:00")
    return result


def _prepare_axis(t_orig):
    times = np.asarray(t_orig, dtype=np.float64).reshape(-1)
    finite = np.isfinite(times)
    if not np.any(finite):
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.intp)
    if np.all(finite) and (len(times) < 2 or np.all(times[1:] >= times[:-1])):
        return times, None
    original_indices = np.flatnonzero(finite)
    times = times[original_indices]
    if len(times) > 1 and not np.all(times[1:] >= times[:-1]):
        order = np.argsort(times, kind="stable")
        times = times[order]
        original_indices = original_indices[order]
    return times, original_indices


def _nearest_sorted(times, t_grid):
    indices = np.searchsorted(times, t_grid, side="left")
    current = np.clip(indices, 0, len(times) - 1)
    previous = np.clip(indices - 1, 0, len(times) - 1)
    return np.where(np.abs(times[current] - t_grid) < np.abs(times[previous] - t_grid), current, previous)


def find_nearest_indices(t_orig: np.ndarray, t_grid: np.ndarray) -> np.ndarray:
    """Return original row indices, handling unsorted, duplicate and missing times."""
    times, original_indices = _prepare_axis(t_orig)
    if not len(times):
        return np.full(len(t_grid), -1, dtype=np.intp)
    result = _nearest_sorted(times, t_grid)
    return result if original_indices is None else original_indices[result]


class _H5Reader:
    """Cache one raw read per dataset, including compound time and value members."""
    def __init__(self, file, check_cancel=lambda: None):
        self.file = file
        self.arrays = {}
        self.check_cancel = check_cancel

    def read(self, field):
        path, _, member = field.partition(":")
        ds = self.file[path]
        if not isinstance(ds, h5py.Dataset) or _vector_size(ds) is None:
            raise ValueError(f"字段不是一维数据集: {field}")
        if path not in self.arrays:
            self.check_cancel()
            self.arrays[path] = ds[...].reshape(-1)
            self.check_cancel()
        raw = self.arrays[path]
        if member:
            if not ds.dtype.names or member not in ds.dtype.names:
                raise ValueError(f"文件内未找到复合字段: {field}")
            return raw[member]
        if ds.dtype.names:
            values = [name for name in ds.dtype.names if not is_time_field(name)]
            return raw[values[0] if values else ds.dtype.names[0]]
        return raw


def read_field_array(f, field: str) -> np.ndarray:
    return _H5Reader(f).read(field)


def _time_path_for_field(f, field_path, configured=None, global_time=None):
    path = field_path.split(":", 1)[0]
    ds = f[path]
    length = _vector_size(ds)
    if ds.dtype.names:
        for member in ds.dtype.names:
            if is_time_field(member):
                return f"{path}:{member}"
    parent_path, _, name = path.rpartition("/")
    parent = f[parent_path] if parent_path else f
    for sibling, item in parent.items():
        if sibling.lower() in (f"{name.lower()}_time", f"{name.lower()}_timestamp") and isinstance(item, h5py.Dataset):
            if _vector_size(item) == length:
                return f"{parent_path}/{sibling}" if parent_path else sibling
    # A configured global clock takes precedence over a generic sibling clock.
    for candidate in (configured,):
        if candidate:
            candidate_path = candidate.split(":", 1)[0]
            if candidate_path not in f:
                raise ValueError(f"文件内未找到时间字段: {candidate}")
            item = f[candidate_path]
            if isinstance(item, h5py.Dataset) and _vector_size(item) == length:
                return candidate
            raise ValueError(f"所选时间字段的长度与数据不一致: {candidate} / {field_path}")
    for sibling, item in parent.items():
        if isinstance(item, h5py.Dataset) and is_time_field(sibling) and not item.dtype.names and _vector_size(item) == length:
            return f"{parent_path}/{sibling}" if parent_path else sibling
    if global_time:
        item = f[global_time.split(":", 1)[0]]
        if isinstance(item, h5py.Dataset) and _vector_size(item) == length:
            return global_time
    return None


def find_time_array_for_field(f, field_path: str, time_field=None, reader=None, global_time=None) -> tuple:
    path = _time_path_for_field(f, field_path, time_field, global_time)
    return (path, (reader or _H5Reader(f)).read(path)) if path else (None, None)


def determine_field_type_and_unit(f, field_path: str, default_temp_unit: str, default_pres_unit: str) -> tuple:
    path, _, member = field_path.partition(":")
    ds = f[path]
    measurement = _text(ds.attrs.get("Measurement Type", "")).lower()
    original_unit = _text(ds.attrs.get("UoM", "")).strip().lower()
    name = (member or path.split("/")[-1]).lower()
    if "pressure" in measurement or original_unit in ("pa", "psi", "mpa", "kpa", "bar") or "pres" in name:
        unit = default_pres_unit or "PSI"
        scale_to_pa = {"psi": 6894.757293, "mpa": 1e6, "kpa": 1e3, "bar": 1e5}.get(original_unit, 1.0)
        divisor = {"PSI": 6894.757293, "MPa": 1e6, "kPa": 1e3, "bar": 1e5, "Pa": 1.0}[unit]
        return "pressure", unit, lambda values: values * (scale_to_pa / divisor)
    if "temperature" in measurement or original_unit in ("°k", "k", "c", "°c", "degc", "celsius", "f", "°f", "degf") or "temp" in name:
        unit = default_temp_unit or "degC"
        if original_unit in ("c", "°c", "degc", "celsius"):
            celsius = lambda values: values
        elif original_unit in ("f", "°f", "degf"):
            celsius = lambda values: (values - 32.0) / 1.8
        else:
            celsius = lambda values: values - 273.15
        if unit == "degF":
            conversion = lambda values: celsius(values) * 1.8 + 32.0
        elif unit in ("K", "°K"):
            conversion = lambda values: celsius(values) + 273.15
        else:
            conversion = celsius
        return "temperature", unit, conversion
    return "other", "", lambda values: values


def get_column_header(f, field_path: str, default_temp_unit: str, default_pres_unit: str) -> str:
    path, _, member = field_path.partition(":")
    ds = f[path]
    name = path.split("/")[-1] + (f":{member}" if member else "")
    measurement = _text(ds.attrs.get("Measurement Type", "")).lower()
    _, unit, _ = determine_field_type_and_unit(f, field_path, default_temp_unit, default_pres_unit)
    if not unit:
        if "electric current" in measurement:
            unit = "A"
        elif "electric potential" in measurement:
            unit = "Volt"
        else:
            unit = _text(ds.attrs.get("UoM", "")).strip()
    return f"{name} ({unit})" if unit else name


def parse_filename_metadata(filename: str) -> tuple:
    parts = os.path.basename(filename).split("-")
    sn, well = "", ""
    if len(parts) >= 2:
        sn = parts[0].split("_")[-1]
        well = parts[1]
    return well, sn, "2.110r512"


class ExportCancelled(Exception):
    pass


def _cell_value(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        return _text(value)
    if isinstance(value, (int, float)) and not math.isfinite(value):
        return None
    return value


def _write_excel_rows(headers, rows, row_count, output_path, meta_line, check_cancel=lambda: None, progress=None):
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    workbook = Workbook(write_only=True)
    worksheet = None
    cells = None
    try:
        for row_index, row in enumerate(rows):
            if row_index % MAX_ROWS_PER_SHEET == 0:
                if worksheet is not None:
                    worksheet.close()
                title = "Data" if row_count <= MAX_ROWS_PER_SHEET else f"Data_Part{row_index // MAX_ROWS_PER_SHEET + 1}"
                worksheet = workbook.create_sheet(title=title)
                worksheet.append([meta_line])
                header_cells = [WriteOnlyCell(worksheet, value=header) for header in headers]
                for cell in header_cells:
                    cell.data_type = "s"
                worksheet.append(header_cells)
                # append() serialises immediately, so these cell objects can be
                # reused rather than allocating millions of styled cells.
                cells = [WriteOnlyCell(worksheet) for _ in headers]
                for cell in cells[1:]:
                    cell.number_format = "0.00"
            if row_index % 512 == 0:
                check_cancel()
                if progress:
                    progress(row_index, row_count)
            for column_index, value in enumerate(row):
                value = _cell_value(value)
                cell = cells[column_index]
                cell.value = value
                # HDF5 labels/string values are data, never workbook formulae.
                if isinstance(value, str):
                    cell.data_type = "s"
            worksheet.append(cells)
        if worksheet is None:
            worksheet = workbook.create_sheet("Data")
            worksheet.append([meta_line])
            worksheet.append(headers)
        check_cancel()
        workbook.save(output_path)
        check_cancel()
    finally:
        # openpyxl creates temporary sheet XML files; release these even on cancellation.
        for sheet in workbook.worksheets:
            writer = getattr(sheet, "_writer", None)
            if writer is not None:
                if not sheet.closed:
                    sheet.close()
                try:
                    writer.cleanup()
                except FileNotFoundError:
                    pass
        workbook.close()


def save_to_excel_with_meta(df, output_path: str, meta_line: str):
    """Compatibility helper; write each row once without retaining Excel cells."""
    _write_excel_rows(list(df.columns), df.itertuples(index=False, name=None), len(df), output_path, meta_line)


def _normalise_output_name(name):
    name = name.strip()
    if not name or name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
        raise ValueError("导出名称必须是文件名，不能包含路径或 Windows 不允许的字符")
    if len(name) > 180:
        raise ValueError("导出文件名过长，请缩短至 180 个字符以内")
    stem, extension = os.path.splitext(name)
    if stem.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("导出文件名是 Windows 保留名称，请更换名称")
    if not extension:
        return name + ".xlsx"
    if extension.lower() not in (".xlsx", ".csv"):
        raise ValueError("仅支持 .xlsx 或 .csv 导出格式")
    return name


def _validate_export_config(cfg):
    if not cfg.selectedFields:
        raise ValueError("请至少选择一个导出字段")
    if len(cfg.selectedFields) > 16383:
        raise ValueError("导出字段数量超过 Excel 列数限制")
    if len(set(cfg.selectedFields)) != len(cfg.selectedFields):
        raise ValueError("导出字段不能重复")
    if not math.isfinite(cfg.interval) or cfg.interval <= 0:
        raise ValueError("采样间隔必须是有限的正数")
    if cfg.timeType and cfg.timeType not in TIME_TYPES:
        raise ValueError("时间类型无效")
    if (cfg.tempUnit or "degC") not in ("degC", "degF", "K", "°K"):
        raise ValueError("温度单位无效")
    if (cfg.presUnit or "PSI") not in ("PSI", "MPa", "kPa", "bar", "Pa"):
        raise ValueError("压力单位无效")
    if not os.path.isfile(cfg.filePath):
        raise ValueError("HDF5 源文件不存在")
    for bound in (cfg.startTimeStr, cfg.endTimeStr):
        if bound:
            try:
                numeric = float(bound)
            except ValueError:
                _date_timestamp(bound)
            else:
                if not math.isfinite(numeric):
                    raise ValueError("时间范围必须为有限值")
    if cfg.baseDate:
        _date_timestamp(cfg.baseDate)
    return _normalise_output_name(cfg.customName)


def _grid_row_count(start, end, interval):
    if not all(math.isfinite(value) for value in (start, end, interval)) or interval <= 0:
        raise ValueError("时间范围和采样间隔必须是有限值，间隔必须大于零")
    if start > end:
        raise ValueError("开始时间不能晚于结束时间")
    # Validate before allocating even a single grid element.
    ratio = (end - start) / interval
    if not math.isfinite(ratio) or ratio >= MAX_EXPORT_ROWS:
        raise ValueError(f"导出数据量过大，请缩短时间范围或增大间隔（最多 {MAX_EXPORT_ROWS:,} 行）")
    row_count = int(math.floor(ratio + 1e-10)) + 1
    if row_count > MAX_EXPORT_ROWS:
        raise ValueError(f"导出数据量超过 {MAX_EXPORT_ROWS:,} 行")
    datetime.datetime.fromtimestamp(start, datetime.timezone.utc)
    datetime.datetime.fromtimestamp(end, datetime.timezone.utc)
    return row_count


def _bound_timestamp(value, fallback, time_type, base_date):
    if not value:
        return fallback
    try:
        numeric = float(value)
    except ValueError:
        return _date_timestamp(value)
    if time_type == "relative_seconds":
        return numeric + _date_timestamp(base_date or "1970-01-01 00:00:00")
    if time_type == "timestamp_ms":
        return numeric / 1000.0
    return numeric


def _format_timestamp(timestamp):
    dt = datetime.datetime.fromtimestamp(float(timestamp), datetime.timezone.utc)
    return f"{dt.year}/{dt.month}/{dt.day} {dt.hour}:{dt.minute:02d}:{dt.second:02d}"


def _chunk_row_limit(field_count, axis_count):
    """Budget aligned columns, retained match indices and nearest-match temporaries."""
    bytes_per_row = 8 * (field_count + axis_count + 12)
    return max(1, min(CHUNK_ROWS, MAX_CHUNK_MEMORY // bytes_per_row))


def export_task_worker(task_id: str, cfg: ExportConfig, output_dir: str):
    with tasks_lock:
        cancel_event = _TASK_CANCEL.setdefault(task_id, threading.Event())

    def check_cancel():
        if cancel_event.is_set():
            raise ExportCancelled()

    def update_status(progress, message, status="running", error=None):
        with tasks_lock:
            task = TASKS.get(task_id)
            if task and not cancel_event.is_set():
                task.update(progress=progress, message=message, status=status, error=error)
                if status in ("completed", "failed"):
                    task["finished_at"] = time.time()

    temp_path = None
    try:
        check_cancel()
        output_name = _validate_export_config(cfg)
        output_path = os.path.join(output_dir, output_name)
        update_status(5, "正在读取 HDF5 结构...")
        with h5py.File(cfg.filePath, "r") as file:
            datasets = find_datasets(file)
            global_time = cfg.timeField or detect_time_dataset(datasets)
            paths = []
            for field in cfg.selectedFields:
                check_cancel()
                base = field.split(":", 1)[0]
                if base not in file or not isinstance(file[base], h5py.Dataset):
                    raise ValueError(f"文件内未找到字段: {field}")
                if _vector_size(file[base]) is None:
                    raise ValueError(f"字段不是一维数据: {field}")
                time_path = _time_path_for_field(file, field, cfg.timeField, global_time)
                paths.append(time_path)
            source_paths = {field.split(":", 1)[0] for field in cfg.selectedFields}
            source_paths.update(path.split(":", 1)[0] for path in paths if path)
            estimate = sum(file[path].size * file[path].dtype.itemsize for path in source_paths)
            estimate += sum(file[path.split(":", 1)[0]].size * 32 for path in set(paths) if path)
            if estimate > MAX_SOURCE_MEMORY:
                raise ValueError("所选数据超过当前版本的内存预算，请减少字段或使用 64 位版本分批导出")
            reader = _H5Reader(file, check_cancel)
            axes = {}
            field_data = []
            min_times, max_times = [], []
            headers = ["Date time"]
            inferred_types = []
            for index, (field, time_path) in enumerate(zip(cfg.selectedFields, paths)):
                check_cancel()
                update_status(10 + int(15 * index / len(paths)), f"正在读取字段 ({index + 1}/{len(paths)})...")
                values = reader.read(field)
                if values.dtype.kind not in "biuf":
                    raise ValueError(f"导出字段必须是数值: {field}")
                if time_path:
                    if time_path not in axes:
                        raw_times = reader.read(time_path)
                        inferred_type = parse_time_array(raw_times)[0]
                        # Explicit interpretation applies to the chosen global clock;
                        # each embedded clock still retains its native seconds/ms format.
                        chosen_type = cfg.timeType if cfg.timeType and time_path == global_time else inferred_type
                        times = convert_time_array_to_float_timestamps(raw_times, chosen_type, cfg.baseDate)
                        check_cancel()
                        sorted_times, original_indices = _prepare_axis(times)
                        axes[time_path] = (sorted_times, original_indices, chosen_type)
                        if len(sorted_times):
                            min_times.append(float(sorted_times[0]))
                            max_times.append(float(sorted_times[-1]))
                    axis = axes[time_path]
                    if not len(axis[0]):
                        raise ValueError(f"时间字段没有有效时间: {time_path}")
                    inferred_types.append(axis[2])
                else:
                    raise ValueError(f"字段没有匹配的时间轴，请选择有效的时间字段: {field}")
                header = get_column_header(file, field, cfg.tempUnit, cfg.presUnit)
                if header in headers:
                    header = f"{header} [{field}]"
                headers.append(header)
                _, _, conversion = determine_field_type_and_unit(file, field, cfg.tempUnit, cfg.presUnit)
                field_data.append((values, time_path, conversion))
            if not min_times:
                raise ValueError("未找到有效时间轴")
            bound_type = cfg.timeType or (inferred_types[0] if inferred_types else "timestamp_seconds")
            start = _bound_timestamp(cfg.startTimeStr, min(min_times), bound_type, cfg.baseDate)
            end = _bound_timestamp(cfg.endTimeStr, max(max_times), bound_type, cfg.baseDate)
            row_count = _grid_row_count(start, end, cfg.interval)
            update_status(30, f"开始流式写入 {row_count:,} 行...")
            os.makedirs(output_dir, exist_ok=True)
            descriptor, temp_path = tempfile.mkstemp(prefix=".hdf5-export-", suffix=os.path.splitext(output_name)[1], dir=output_dir)
            os.close(descriptor)
            well, serial, version = parse_filename_metadata(cfg.filePath)
            meta = f"Well name:{well}, Sn :{serial} ,  Version :{version}"
            chunk_rows = _chunk_row_limit(len(field_data), len(axes))

            def rows():
                for offset in range(0, row_count, chunk_rows):
                    check_cancel()
                    count = min(chunk_rows, row_count - offset)
                    grid = start + (offset + np.arange(count, dtype=np.float64)) * cfg.interval
                    matches = {}
                    for path, (times, original_indices, _) in axes.items():
                        indices = _nearest_sorted(times, grid)
                        matches[path] = indices if original_indices is None else original_indices[indices]
                    columns = [conversion(values[matches[path]]) for values, path, conversion in field_data]
                    for row in range(count):
                        yield (_format_timestamp(grid[row]), *(column[row] for column in columns))

            def write_progress(done, total):
                update_status(30 + int(65 * done / total), f"正在写入 {done:,}/{total:,} 行...")

            if output_name.lower().endswith(".csv"):
                with open(temp_path, "w", encoding="utf-8", newline="") as stream:
                    stream.write(meta + "\n")
                    writer = csv.writer(stream)
                    writer.writerow(headers)
                    for index, row in enumerate(rows()):
                        if index % 512 == 0:
                            check_cancel()
                            write_progress(index, row_count)
                        writer.writerow([row[0]] + [f"{float(value):.2f}" if math.isfinite(float(value)) else "" for value in row[1:]])
            else:
                _write_excel_rows(headers, rows(), row_count, temp_path, meta, check_cancel, write_progress)
        check_cancel()
        # Cancellation and publication use the same lock, eliminating the final-file race.
        with tasks_lock:
            check_cancel()
            os.replace(temp_path, output_path)
            temp_path = None
            update_status(100, "导出成功！", "completed")
    except ExportCancelled:
        with tasks_lock:
            if task_id in TASKS:
                TASKS[task_id].update(status="cancelled", message="任务已取消", error=None, finished_at=time.time())
    except Exception as error:
        if cancel_event.is_set():
            with tasks_lock:
                if task_id in TASKS:
                    TASKS[task_id].update(status="cancelled", message="任务已取消", error=None, finished_at=time.time())
        else:
            update_status(100, f"导出失败: {error}", "failed", str(error))
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@app.post("/api/scan")
def scan_directory(payload: ScanPayload):
    path = os.path.abspath(os.path.expanduser(payload.path.strip()))
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail="指定的路径不存在，请检查后重新输入。")
    if os.path.isfile(path):
        if path.lower().endswith((".h5", ".hdf5")):
            return {"files": [{"path": path, "name": os.path.basename(path), "size": os.path.getsize(path)}]}
        raise HTTPException(status_code=400, detail="输入的文件不是有效的 HDF5 文件。")
    try:
        with os.scandir(path) as entries:
            files = [{"path": entry.path, "name": entry.name, "size": entry.stat().st_size}
                     for entry in entries if entry.is_file() and entry.name.lower().endswith((".h5", ".hdf5"))]
        return {"files": sorted(files, key=lambda entry: entry["name"])}
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"扫描文件夹失败: {error}")


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



def _read_vector_slice(ds, member, start, stop):
    if len(ds.shape) == 1:
        selection = (slice(start, stop),)
    elif ds.shape[0] == 1:
        selection = (0, slice(start, stop))
    else:
        selection = (slice(start, stop), 0)
    # Combined slice/member indexing works on both h5py 2.10 (Windows x86)
    # and current h5py, and reads only this chunk of the requested member.
    return ds[selection + ((member,) if member else ())].reshape(-1)


def _inspect_time(file, field):
    """Read time in bounded chunks; never read measurement datasets to inspect them."""
    path, _, member = field.partition(":")
    ds = file[path]
    if not isinstance(ds, h5py.Dataset) or _vector_size(ds) is None:
        raise ValueError("时间字段不是一维数据")
    count = _vector_size(ds)
    sample = _read_vector_slice(ds, member, 0, min(count, 64))
    time_type = parse_time_array(sample)[0]
    low, high = None, None
    for start in range(0, count, 65536):
        raw = _read_vector_slice(ds, member, start, min(count, start + 65536))
        if time_type in ("string", "unknown_string"):
            values = convert_time_array_to_float_timestamps(raw, time_type, None)
        else:
            values = np.asarray(raw, dtype=np.float64)
        valid = values[np.isfinite(values)]
        if not len(valid):
            continue
        current_low, current_high = float(np.min(valid)), float(np.max(valid))
        low = current_low if low is None else min(low, current_low)
        high = current_high if high is None else max(high, current_high)
    if low is None:
        return time_type, None, None
    if time_type not in ("string", "unknown_string"):
        time_type = parse_time_array(np.array([low, high]))[0]
    if time_type == "relative_seconds":
        return time_type, str(low), str(high)
    if time_type == "timestamp_ms":
        low, high = low / 1000, high / 1000
    formatter = lambda value: datetime.datetime.fromtimestamp(value, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return time_type, formatter(low), formatter(high)


@app.post("/api/inspect")
def inspect_hdf5(payload: InspectPayload):
    path = os.path.abspath(os.path.expanduser(payload.path.strip()))
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail="文件不存在")
    try:
        with h5py.File(path, "r") as file:
            datasets = find_datasets(file)
            detected = detect_time_dataset(datasets)
            time_type, low, high = _inspect_time(file, detected) if detected else ("none", None, None)
            time_fields = []
            for entry in datasets:
                if entry.get("timeField"):
                    time_fields.append(entry["timeField"])
                elif is_time_field(entry["path"]):
                    time_fields.append(entry["path"])
            return {"datasets": datasets, "detectedTimeField": detected, "timeFields": time_fields,
                    "timeType": time_type, "timeMinStr": low, "timeMaxStr": high}
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"解析 HDF5 文件失败: {error}")


def _prune_tasks():
    terminal = [(task_id, task.get("finished_at", 0)) for task_id, task in TASKS.items()
                if task["status"] in ("completed", "failed", "cancelled")]
    terminal.sort(key=lambda item: item[1], reverse=True)
    for task_id, finished_at in terminal[MAX_TASK_HISTORY:]:
        future = _TASK_FUTURES.get(task_id)
        if future is not None and not future.done():
            continue
        TASKS.pop(task_id, None)
        _TASK_CANCEL.pop(task_id, None)
        _TASK_FUTURES.pop(task_id, None)


@app.post("/api/export")
def trigger_export(payload: ExportPayload):
    if not payload.configs:
        raise HTTPException(status_code=400, detail="没有提交任何导出配置")
    if len(payload.configs) > MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=400, detail=f"每批最多提交 {MAX_ACTIVE_TASKS} 个文件")
    output_dir = os.path.expanduser(payload.outputDir.strip())
    if not output_dir:
        raise HTTPException(status_code=400, detail="未指定导出保存的目录")
    output_dir = os.path.abspath(output_dir)
    try:
        names = [_validate_export_config(cfg) for cfg in payload.configs]
        keys = [os.path.join(output_dir, name).casefold() for name in names]
        if len(set(keys)) != len(keys):
            raise ValueError("同一批次的导出文件名不能重复（Windows 不区分大小写）")
        os.makedirs(output_dir, exist_ok=True)
    except (ValueError, OSError, OverflowError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    ids = []
    with tasks_lock:
        _prune_tasks()
        active = [task for task in TASKS.values() if task["status"] in ("pending", "running")]
        if len(active) + len(payload.configs) > MAX_ACTIVE_TASKS:
            raise HTTPException(status_code=400, detail="排队任务过多，请等待当前任务完成")
        if any(task["output_path"].casefold() in keys for task in active):
            raise HTTPException(status_code=400, detail="同名文件正在导出，请等待完成或更换文件名")
        for cfg, name in zip(payload.configs, names):
            task_id = str(uuid.uuid4())
            TASKS[task_id] = {"file_name": os.path.basename(cfg.filePath), "status": "pending", "progress": 0,
                              "message": "排队等待中...", "error": None, "output_path": os.path.join(output_dir, name)}
            _TASK_CANCEL[task_id] = threading.Event()
            ids.append(task_id)
            _TASK_FUTURES[task_id] = executor.submit(export_task_worker, task_id, cfg, output_dir)
    return {"taskIds": ids}


@app.get("/api/status")
def get_status(taskIds: str):
    with tasks_lock:
        return {task_id: dict(TASKS.get(task_id, {"status": "failed", "progress": 100,
                "message": "未找到任务", "error": "Task not found"})) for task_id in taskIds.split(",")}


@app.post("/api/cancel")
def cancel_task(taskId: str):
    with tasks_lock:
        task = TASKS.get(taskId)
        if task and task["status"] in ("pending", "running"):
            _TASK_CANCEL[taskId].set()
            future = _TASK_FUTURES.get(taskId)
            if future:
                future.cancel()
            task.update(status="cancelled", message="任务已取消", error=None, finished_at=time.time())
            return {"success": True}
    return {"success": False, "detail": "任务已完成或不存在"}


CONFIG_FILE = os.path.join(app_dir, "config.json")
BUNDLED_CONFIG_FILE = os.path.join(base_dir, "config.default.json")
if sys.platform == "win32":
    _settings_root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
elif sys.platform == "darwin":
    _settings_root = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
else:
    _settings_root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
FALLBACK_CONFIG_FILE = os.path.join(_settings_root, "H5ToExcelConverter", "config.json")
_config_lock = threading.RLock()


def _default_config():
    return {"h5_src_path": "", "h5_out_path": "", "h5_field_presets": {}}


def read_local_config():
    with _config_lock:
        candidates = [path for path in dict.fromkeys((CONFIG_FILE, FALLBACK_CONFIG_FILE)) if os.path.isfile(path)]
        candidates.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        if os.path.isfile(BUNDLED_CONFIG_FILE):
            candidates.append(BUNDLED_CONFIG_FILE)
        for path in candidates:
            try:
                with open(path, encoding="utf-8") as source:
                    config = json.load(source)
                if isinstance(config, dict):
                    return config
            except (OSError, ValueError):
                continue
        return _default_config()


def write_local_config(config_data):
    encoded = json.dumps(config_data, ensure_ascii=False, indent=2)
    errors = []
    with _config_lock:
        for path in dict.fromkeys((CONFIG_FILE, FALLBACK_CONFIG_FILE)):
            temporary = None
            try:
                folder = os.path.dirname(os.path.abspath(path))
                os.makedirs(folder, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=folder)
                with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                    target.write(encoded)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, path)
                return path
            except OSError as error:
                errors.append(str(error))
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
    raise OSError("无法保存配置: " + "; ".join(errors))


@app.get("/api/config")
def get_config():
    return read_local_config()


@app.post("/api/config")
def save_config(payload: dict):
    try:
        with _config_lock:
            current = read_local_config()
            current.update(payload)
            path = write_local_config(current)
        return {"status": "ok", "configPath": path}
    except (OSError, TypeError, ValueError) as error:
        raise HTTPException(status_code=500, detail=str(error))


@app.get("/")
def read_root():
    index = os.path.join(base_dir, "static", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"message": "Frontend static file index.html not found."})


static_path = os.path.join(base_dir, "static")
if os.path.isdir(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")
