"""Modern, lightweight desktop front end.

The window is painted before importing the HDF5/export engine. All filesystem,
inspection, configuration and export calls run away from Tk's event thread.
"""

from __future__ import annotations

import copy
import importlib
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

import customtkinter as ctk


PAGE_SIZE = 100
FIELD_PAGE_SIZE = 200
TERMINAL_STATES = {"completed", "failed", "cancelled"}
TIME_TYPES = {
    "Unix 时间戳（秒）": "timestamp_seconds",
    "Unix 时间戳（毫秒）": "timestamp_ms",
    "相对时间（秒）": "relative_seconds",
    "日期文本": "string",
    "未识别 / 自动": "none",
}
TEMPERATURE_UNITS = {"°C 摄氏度": "degC", "°F 华氏度": "degF", "K 开尔文": "K"}
PRESSURE_UNITS = {"PSI": "PSI", "Pa": "Pa", "kPa": "kPa", "bar": "bar", "MPa": "MPa"}


class Interface:
    """Shared small controls; no image assets or browser runtime are needed."""

    background = "#F3F6FA"
    surface = "#FFFFFF"
    ink = "#172B46"
    muted = "#75849A"
    accent = "#2864E8"
    soft = "#EDF3FF"
    line = "#E4EAF2"

    def __init__(self):
        self.family = "Microsoft YaHei UI" if sys.platform == "win32" else "PingFang SC" if sys.platform == "darwin" else "sans-serif"
        self.font = (self.family, 13)
        self.small = (self.family, 12)
        self.heading = (self.family, 15, "bold")

    def frame(self, parent, *, card=False, **options):
        return ctk.CTkFrame(parent, fg_color=self.surface if card else "transparent",
                            corner_radius=12 if card else 0,
                            border_width=1 if card else 0, border_color=self.line, **options)

    def label(self, parent, *, text="", muted=False, font=None, **options):
        options.setdefault("text_color", self.muted if muted else self.ink)
        options.setdefault("anchor", "w")
        selected_font = font or self.font
        options.setdefault("height", max(20, selected_font[1] + 5))
        return ctk.CTkLabel(parent, text=text, font=selected_font, **options)

    def button(self, parent, text, command, *, primary=False, quiet=False, width=100, height=34, **options):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                             corner_radius=8, border_width=0,
                             font=(self.family, 13, "bold") if primary else self.font,
                             fg_color=self.accent if primary else self.surface if quiet else self.soft,
                             hover_color="#1D51CA" if primary else "#E2EBFF",
                             text_color="#FFFFFF" if primary else self.accent,
                             text_color_disabled="#A0AEC1", **options)

    def entry(self, parent, variable, *, width=160, **options):
        return ctk.CTkEntry(parent, textvariable=variable, width=width, height=34,
                            corner_radius=8, border_width=1, border_color=self.line,
                            fg_color="#FBFCFE", text_color=self.ink,
                            placeholder_text_color=self.muted, font=self.font, **options)

    def combo(self, parent, variable, values, *, width=170, command=None):
        return ctk.CTkComboBox(parent, variable=variable, values=list(values), state="readonly",
                               command=command, width=width, height=34, corner_radius=8,
                               border_width=1, border_color=self.line, fg_color="#FBFCFE",
                               button_color=self.soft, button_hover_color="#E2EBFF",
                               dropdown_fg_color=self.surface, dropdown_hover_color=self.soft,
                               dropdown_text_color=self.ink, text_color=self.ink,
                               text_color_disabled="#A0AEC1", font=self.font, dropdown_font=self.font)

    def step(self, parent, number, title):
        self.label(parent, text=str(number), font=(self.family, 12, "bold"),
                   fg_color=self.soft, corner_radius=7, width=24,
                   anchor="center", text_color=self.accent).pack(side="left")
        self.label(parent, text=title, font=self.heading).pack(side="left", padx=(8, 0))


class ModernProgressBar(ctk.CTkProgressBar):
    """Use a slim CTk track while retaining the export's percentage API."""

    def __init__(self, parent, *, maximum=100, **options):
        self._maximum = float(maximum)
        self._percentage = 0.0
        super().__init__(parent, **options)
        self.set(0)

    def __getitem__(self, key):
        return self.cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def cget(self, attribute):
        if attribute == "value":
            return self._percentage
        if attribute == "maximum":
            return self._maximum
        return super().cget(attribute)

    def configure(self, require_redraw=False, **options):
        value = options.pop("value", None)
        super().configure(require_redraw=require_redraw, **options)
        if value is not None:
            self._percentage = max(0.0, min(self._maximum, float(value)))
            self.set(self._percentage / self._maximum)

    def start(self, interval=None):
        # CTk schedules its own smooth animation; legacy callers pass a Tk
        # interval that is unnecessary for its internal animation loop.
        super().start()


def default_fields(datasets: list[dict], time_field: Optional[str]) -> list[str]:
    """Match the established web application's pressure/temperature defaults."""
    paths = [item["path"] for item in datasets if item["path"] != time_field]
    preferred = [path for path in paths if any(
        name in path.lower() for name in ("eqrtz s1 pres psi a", "eqrtz s1 temp celsius a")
    )]
    if preferred:
        return preferred
    pressure_temperature = [path for path in paths if any(
        name in path.lower() for name in ("pres", "temp")
    )]
    return pressure_temperature or paths


def default_name(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    match = re.match(r"^Instruct_[^-]*-(.+)$", base, flags=re.IGNORECASE)
    return (match.group(1) if match else base) + ".xlsx"


def match_preset_fields(paths: list[str], fields: list[str]) -> list[str]:
    """Match portable field basenames while accepting older full-path presets."""
    full_names = {field.casefold() for field in fields}
    short_names = {field.rsplit("/", 1)[-1].casefold() for field in fields}
    return [path for path in paths if path.casefold() in full_names
            or path.rsplit("/", 1)[-1].casefold() in short_names]


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return str(size)


def error_text(exc: Exception) -> str:
    return str(getattr(exc, "detail", None) or exc) or type(exc).__name__


def _label_for(options: dict[str, str], value: str, default: str) -> str:
    return next((label for label, code in options.items() if code == value), default)


def _window_scale(window) -> float:
    """Tk reports physical screen pixels; CTk geometry uses logical pixels."""
    getter = getattr(window, "_get_window_scaling", None)
    return float(getter()) if getter else 1.0


def _fit_display(root) -> tuple[int, int]:
    available_width = max(1, root.winfo_screenwidth() - 80)
    available_height = max(1, root.winfo_screenheight() - 96)
    if isinstance(root, ctk.CTk):
        detected_scale = _window_scale(root) / ctk.ScalingTracker.window_scaling
        # A 1366 x 768 monitor at 150% cannot fit the minimum layout at the
        # full OS multiplier. Keep window and controls at the same fitted scale.
        fitted_scale = min(detected_scale, available_width / 860, available_height / 620)
        factor = fitted_scale / detected_scale
        ctk.set_widget_scaling(factor)
        ctk.set_window_scaling(factor)
    scale = _window_scale(root)
    return int(available_width / scale), int(available_height / scale)


class DesktopApp:
    def __init__(self, root: tk.Tk, *, smoke_test: bool = False, started: Optional[float] = None):
        self.root = root
        self.started = started if started is not None else time.perf_counter()
        self.smoke_test = smoke_test
        self.report = {"success": False, "backendReady": False}
        self.backend = None
        self.events: queue.Queue = queue.Queue()
        self.closed = False
        self.closing = False
        self.files: list[dict] = []
        self.file_by_path: dict[str, dict] = {}
        self.chosen: set[str] = set()
        self.inspections: dict[str, dict] = {}
        self.configs: dict[str, dict] = {}
        self.presets: dict[str, dict] = {}
        self.inspecting: set[str] = set()
        self.pending_edit: set[str] = set()
        self.page = 0
        self.scanning = False
        self.busy = False
        self.preparing = False
        self.task_ids: list[str] = []
        self.export_total = 0
        self.task_paths: dict[str, str] = {}
        self.task_states: dict[str, dict] = {}
        self.logged_tasks: set[str] = set()
        self.cancel_event = threading.Event()
        self.save_after = None
        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.format = tk.StringVar(value="Excel (.xlsx)")
        self.interval = tk.StringVar(value="10")
        self.temperature = tk.StringVar(value="°C 摄氏度")
        self.pressure = tk.StringVar(value="PSI")
        self.status = tk.StringVar(value="正在载入数据组件…")
        self.selection_info = tk.StringVar(value="尚未选择文件")
        self.page_info = tk.StringVar(value="0 个文件")
        self.detail = tk.StringVar(value="自动识别压力和温度。双击文件可调整字段、时间范围与名称。")
        self._build_window()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after_idle(self._first_paint)
        root.after(60, self._drain_events)
        if smoke_test:
            root.after(180, self._finish_smoke)
        else:
            # This callback is delayed until after Tk has mapped its window.
            root.after(100, self._load_backend)

    def _build_window(self):
        root = self.root
        ctk.set_appearance_mode("light")
        self.design = ui = Interface()
        root.title("井下压力 · 数据导出")
        available_width, available_height = _fit_display(root)
        width = min(1040, max(860, available_width))
        height = min(780, max(620, available_height))
        # CTk temporarily clamps a window to its previous size when changing
        # scale. Restore the fitted bounds before setting the first geometry.
        root.maxsize(available_width, available_height)
        root.geometry(f"{width}x{height}")
        root.minsize(860, 620)
        root.configure(background=ui.background)
        self.report["displayScale"] = round(_window_scale(root), 3)
        # A native table keeps large directories responsive. Its neutral,
        # borderless style matches the custom controls around it.
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        scale = _window_scale(root)
        style.configure("Modern.Treeview", rowheight=round(33 * scale), font=(ui.family, -round(13 * scale)),
                        fieldbackground=ui.surface, background=ui.surface,
                        foreground=ui.ink, borderwidth=0, relief="flat")
        style.configure("Modern.Treeview.Heading", font=(ui.family, -round(12 * scale), "bold"),
                        background="#F4F7FC", foreground="#6F7F95", relief="flat",
                        borderwidth=0, padding=(round(8 * scale), round(8 * scale)))
        style.map("Modern.Treeview", background=[("selected", "#EDF3FF")],
                  foreground=[("selected", "#1E4E9B")])
        style.map("Modern.Treeview.Heading", background=[("active", "#EDF3FF")],
                  relief=[("active", "flat"), ("pressed", "flat")])
        style.layout("Modern.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        outer = ui.frame(root)
        outer.pack(fill="both", expand=True, padx=18, pady=12)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        header = ui.frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        heading = ui.frame(header)
        heading.pack(side="left")
        ui.label(heading, text="井下压力数据导出", font=(ui.family, 25, "bold")).pack(anchor="w")
        ui.label(heading, text="将 HDF5 数据转换为 Excel 或 CSV", muted=True, font=ui.small).pack(anchor="w", pady=(2, 0))
        ui.label(header, text="HDF5  →  EXCEL / CSV", muted=True, font=ui.small).pack(side="right", pady=(10, 0))

        source = ui.frame(outer, card=True)
        source.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        source.columnconfigure(0, weight=1)
        source_title = ui.frame(source)
        source_title.grid(row=0, column=0, columnspan=4, sticky="ew", padx=14, pady=(10, 7))
        ui.step(source_title, 1, "选择数据")
        self.source_entry = ui.entry(source, self.source)
        self.source_entry.grid(row=1, column=0, sticky="ew", padx=(14, 8), pady=(0, 12))
        self.source_entry.bind("<Return>", lambda _event: self.scan_source())
        self.folder_button = ui.button(source, "选择文件夹", self.choose_source_folder, width=112)
        self.folder_button.grid(row=1, column=1, padx=(0, 7), pady=(0, 12))
        self.files_button = ui.button(source, "选择文件", self.choose_source_files, width=98)
        self.files_button.grid(row=1, column=2, padx=(0, 7), pady=(0, 12))
        self.scan_button = ui.button(source, "刷新", self.scan_source, quiet=True, width=60)
        self.scan_button.grid(row=1, column=3, padx=(0, 14), pady=(0, 12))

        file_frame = ui.frame(outer, card=True)
        file_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        file_frame.columnconfigure(0, weight=1)
        file_frame.rowconfigure(1, weight=1, minsize=round(120 * scale))
        toolbar = ui.frame(file_frame)
        toolbar.grid(row=0, column=0, sticky="ew", padx=14, pady=(9, 7))
        ui.step(toolbar, 2, "文件列表")
        self.configure_button = ui.button(toolbar, "字段与时间设置", self.configure_file,
                                           quiet=True, width=126, height=30)
        self.configure_button.pack(side="right")
        ui.button(toolbar, "清空选择", lambda: self.choose_all(False), quiet=True,
                  width=82, height=30).pack(side="right", padx=(0, 5))
        ui.button(toolbar, "全选", lambda: self.choose_all(True), quiet=True,
                  width=58, height=30).pack(side="right", padx=(0, 5))
        table = ui.frame(file_frame)
        table.grid(row=1, column=0, sticky="nsew", padx=14)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1, minsize=round(120 * scale))
        self.tree = ttk.Treeview(table, columns=("choose", "name", "size", "fields", "state"),
                                 show="headings", selectmode="extended", height=6, style="Modern.Treeview")
        columns = [("choose", "选择", 48, False), ("name", "文件名", 280, True),
                   ("size", "大小", 80, False), ("fields", "导出字段", 210, True),
                   ("state", "状态", 180, True)]
        for column, title, column_width, stretch in columns:
            self.tree.heading(column, text=title)
            self.tree.column(column, width=column_width, minwidth=column_width if not stretch else 100,
                             stretch=stretch, anchor="center" if column in {"choose", "size"} else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ctk.CTkScrollbar(table, orientation="vertical", command=self.tree.yview,
                                     fg_color=ui.surface, button_color="#C8D4E6",
                                     button_hover_color="#A7B9D1", width=12)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(3, 0))
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Button-1>", self._tree_click)
        self.tree.bind("<space>", self._toggle_highlighted)
        self.tree.bind("<Double-1>", self._tree_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._file_focus_changed)
        pagination = ui.frame(file_frame)
        pagination.grid(row=2, column=0, sticky="ew", padx=14, pady=(4, 0))
        ui.label(pagination, textvariable=self.selection_info, muted=True, font=ui.small).pack(side="left")
        self.next_button = ui.button(pagination, "下一页 ›", lambda: self.change_page(1),
                                      quiet=True, width=78, height=26)
        self.next_button.pack(side="right")
        self.prev_button = ui.button(pagination, "‹ 上一页", lambda: self.change_page(-1),
                                      quiet=True, width=78, height=26)
        self.prev_button.pack(side="right", padx=(0, 2))
        ui.label(pagination, textvariable=self.page_info, muted=True, font=ui.small).pack(side="right", padx=(8, 12))
        ui.label(file_frame, textvariable=self.detail, muted=True, font=ui.small,
                  wraplength=900).grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 7))

        output = ui.frame(outer, card=True)
        output.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        output.columnconfigure(0, weight=1)
        output_title = ui.frame(output)
        output_title.grid(row=0, column=0, columnspan=3, sticky="ew", padx=14, pady=(9, 7))
        ui.step(output_title, 3, "保存与导出")
        self.output_entry = ui.entry(output, self.output)
        self.output_entry.grid(row=1, column=0, sticky="ew", padx=(14, 8))
        self.output.trace_add("write", lambda *_args: self._settings_changed())
        self.output_button = ui.button(output, "保存文件夹", self.choose_output_folder, width=112)
        self.output_button.grid(row=1, column=1, padx=(0, 8))
        self.format_combo = ui.combo(output, self.format, ("Excel (.xlsx)", "CSV (.csv)"), width=158,
                                      command=lambda _choice: self._settings_changed())
        self.format_combo.grid(row=1, column=2, padx=(0, 14))
        options = ui.frame(output)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(5, 7))
        self.advanced_button = ui.button(options, "采样与单位  ›", self.toggle_advanced,
                                          quiet=True, width=120, height=26)
        self.advanced_button.pack(side="left")
        self.settings_summary = tk.StringVar(value="每 10 秒取样 · 温度 °C · 压力 PSI")
        ui.label(options, textvariable=self.settings_summary, muted=True, font=ui.small).pack(side="left", padx=12)
        self.advanced = ui.frame(output)
        self.advanced.grid(row=3, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 10))
        ui.label(self.advanced, text="采样间隔（秒）", font=ui.small).grid(row=0, column=0, padx=(0, 8))
        self.interval_entry = ui.entry(self.advanced, self.interval, width=84)
        self.interval_entry.grid(row=0, column=1, padx=(0, 18))
        ui.label(self.advanced, text="温度", font=ui.small).grid(row=0, column=2, padx=(0, 8))
        self.temp_combo = ui.combo(self.advanced, self.temperature, TEMPERATURE_UNITS, width=150)
        self.temp_combo.grid(row=0, column=3, padx=(0, 18))
        ui.label(self.advanced, text="压力", font=ui.small).grid(row=0, column=4, padx=(0, 8))
        self.pres_combo = ui.combo(self.advanced, self.pressure, PRESSURE_UNITS, width=110)
        self.pres_combo.grid(row=0, column=5)
        self.advanced.grid_remove()
        for variable in (self.interval, self.temperature, self.pressure):
            variable.trace_add("write", lambda *_args: self._settings_changed())

        footer = ui.frame(outer)
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        actions = ui.frame(footer)
        actions.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        ui.label(actions, textvariable=self.status, muted=True, font=ui.small,
                  wraplength=390).pack(side="left")
        self.export_button = ui.button(actions, "开始导出  →", self.start_export,
                                        primary=True, width=144, height=36)
        self.export_button.pack(side="right")
        self.cancel_button = ui.button(actions, "取消任务", self.cancel_export, quiet=True,
                                        width=90, state="disabled")
        self.cancel_button.pack(side="right", padx=8)
        self.open_button = ui.button(actions, "打开结果文件夹", self.open_output, quiet=True,
                                      width=130, state="disabled")
        self.open_button.pack(side="right")
        self.progress = ModernProgressBar(footer, maximum=100, mode="determinate", height=4,
                                           corner_radius=2, border_width=0,
                                           fg_color="#E5ECF7", progress_color=ui.accent)
        self.progress.grid(row=1, column=0, sticky="ew")
        self.log = tk.Text(footer, height=1, borderwidth=0, highlightthickness=0,
                           background=ui.background, foreground=ui.muted, font=(ui.family, -round(12 * scale)),
                           wrap="word", state="disabled")
        self.log.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.log.grid_remove()
        self._update_controls()

    def _first_paint(self):
        self.root.update_idletasks()
        self.report.update(firstPaintMs=round((time.perf_counter() - self.started) * 1000, 1),
                           backendReady=False, backendImported="converter" in sys.modules or "app" in sys.modules)

    def _finish_smoke(self):
        self.report.update(success=bool(self.root.winfo_ismapped()),
                           backendReady=False, backendImported="converter" in sys.modules or "app" in sys.modules,
                           heavyImports=[name for name in ("converter", "app", "numpy", "h5py", "pandas") if name in sys.modules])
        self.closed = True
        self.root.destroy()

    def _worker(self, event: str, operation):
        def run():
            try:
                self.events.put((event, operation()))
            except Exception as exc:
                self.events.put(("error", (event, error_text(exc))))
        threading.Thread(target=run, name=f"desktop-{event}", daemon=True).start()

    def _load_backend(self):
        def load():
            backend = importlib.import_module("converter")
            config = backend.get_config()
            # A persisted removable-drive or network path can take time to check.
            # Keep those checks on this worker as well.
            saved_source = config.get("h5_src_path", "")
            saved_output = config.get("h5_out_path", "")
            config["h5_src_path"] = saved_source if saved_source and os.path.exists(saved_source) else ""
            config["h5_out_path"] = saved_output if saved_output and os.path.isdir(saved_output) else ""
            return backend, config
        self._worker("backend", load)

    def _drain_events(self):
        if self.closed:
            return
        # Bound work per tick so very large exports cannot starve Tk redraws.
        for _ in range(40):
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event, payload)
            if self.closed:
                return
        if not self.closed:
            self.root.after(60, self._drain_events)

    def _handle_event(self, event, payload):
        if event == "backend":
            self.backend, config = payload
            self.presets = config.get("h5_field_presets", {})
            if not isinstance(self.presets, dict):
                self.presets = {}
            self.report["backendReady"] = True
            saved_source = config.get("h5_src_path", "")
            saved_output = config.get("h5_out_path", "")
            if not self.source.get() and saved_source:
                self.source.set(saved_source)
            if not self.output.get() and saved_output:
                self.output.set(saved_output)
            settings = config.get("desktop_settings", {})
            self.interval.set(str(settings.get("interval", 10)))
            self.temperature.set(_label_for(TEMPERATURE_UNITS, settings.get("tempUnit", "degC"), "°C 摄氏度"))
            self.pressure.set(_label_for(PRESSURE_UNITS, settings.get("presUnit", "PSI"), "PSI"))
            self.format.set("CSV (.csv)" if settings.get("format") == "csv" else "Excel (.xlsx)")
            self.status.set("就绪。选择文件夹或 HDF5 文件开始。")
            self._update_controls()
        elif event == "scan":
            self.scanning = False
            self.files = payload
            self.file_by_path = {item["path"]: item for item in payload}
            self.chosen = set(self.file_by_path)
            self.inspections = {path: data for path, data in self.inspections.items() if path in self.file_by_path}
            self.configs = {path: data for path, data in self.configs.items() if path in self.file_by_path}
            self.task_states.clear()
            self.task_paths.clear()
            self.page = 0
            self._render_files()
            self.status.set(f"找到 {len(payload)} 个 HDF5 文件。" if payload else "该文件夹没有 HDF5 文件。")
            if payload and not self.output.get().strip():
                self.output.set(os.path.dirname(payload[0]["path"]))
            self._settings_changed()
            self._update_controls()
        elif event in {"inspect", "inspect_edit"}:
            path, data = payload
            self.inspecting.discard(path)
            if path not in self.file_by_path:
                return
            self.inspections[path] = data
            self._render_files()
            self._show_detail(path)
            edit = event == "inspect_edit" or path in self.pending_edit
            self.pending_edit.discard(path)
            if edit and not self.busy and not self.closing:
                FileSettings(self, path, data)
        elif event == "prepared":
            path, data = payload
            self.inspections[path] = data
            self.status.set(f"正在准备：{os.path.basename(path)}")
        elif event == "submitted":
            ids, paths, names = payload
            self.task_ids.extend(ids)
            self.task_paths.update(zip(ids, paths))
            self.task_states.update({task_id: {"status": "pending", "progress": 0, "message": "排队等待",
                                              "output_path": names[index]}
                                    for index, task_id in enumerate(ids)})
            self.status.set(f"正在导出 {self.export_total} 个文件…")
            self.progress.configure(mode="determinate")
            self.progress.stop()
            self._render_files()
            if len(self.task_ids) == len(ids):
                self._poll_status()
        elif event == "preparation_done":
            self.preparing = False
            if not self.task_ids:
                self._end_export("任务已取消。")
            elif all(state.get("status") in TERMINAL_STATES for state in self.task_states.values()):
                self._handle_event("status", {})
        elif event == "prepare_cancelled":
            self._end_export("任务已取消。")
        elif event in {"status", "batch_finished"}:
            for task_id, state in payload.items():
                # Late polls must not replace cached terminal results, including
                # after the engine has pruned old task history.
                if self.task_states.get(task_id, {}).get("status") not in TERMINAL_STATES:
                    self.task_states[task_id] = state
            self._render_files()
            states = list(self.task_states.values())
            completed = sum(state.get("status") == "completed" for state in states)
            failed = sum(state.get("status") == "failed" for state in states)
            cancelled = sum(state.get("status") == "cancelled" for state in states)
            progress = sum(float(state.get("progress", 0)) if state.get("status") not in TERMINAL_STATES else 100
                           for state in states) / max(self.export_total, 1)
            self.progress["value"] = progress
            for task_id, state in self.task_states.items():
                if state.get("status") in TERMINAL_STATES and task_id not in self.logged_tasks:
                    self.logged_tasks.add(task_id)
                    name = os.path.basename(self.task_paths.get(task_id, ""))
                    if state.get("status") == "completed":
                        self._log(f"完成：{state.get('output_path', name)}")
                    else:
                        self._log(f"{name}：{state.get('error') or state.get('message', '任务已停止')}")
            if states and not self.preparing and all(state.get("status") in TERMINAL_STATES for state in states):
                self._end_export(f"导出结束：成功 {completed}，失败 {failed}，取消 {cancelled}。")
            else:
                self.status.set(f"导出中：完成 {completed} / {self.export_total} · 总进度 {progress:.0f}%")
                if event == "status":
                    self.root.after(750, self._poll_status)
        elif event == "saved":
            pass
        elif event == "cancelled":
            self.status.set("正在停止任务，请稍候…")
        elif event == "closed":
            self._destroy_window()
        elif event == "error":
            operation, detail = payload
            if operation == "backend":
                self.status.set("数据组件加载失败。请检查软件是否完整解压。")
                self._log(detail)
                messagebox.showerror("无法加载数据组件", detail, parent=self.root)
            elif operation == "saved":
                self._log(f"设置未保存：{detail}")
            elif operation in {"inspect", "inspect_edit"}:
                self.inspecting.clear()
                self.pending_edit.clear()
                self.status.set("文件读取失败。")
                messagebox.showerror("无法读取文件", detail, parent=self.root)
            elif operation == "scan":
                self.scanning = False
                self.status.set("扫描失败。请检查路径。")
                self._update_controls()
                messagebox.showerror("扫描失败", detail, parent=self.root)
            elif operation == "status":
                self._log(f"进度读取失败：{detail}")
                if self.busy:
                    self.root.after(1500, self._poll_status)
            elif operation == "submit":
                self._end_export("未能开始导出。")
                messagebox.showerror("无法导出", detail, parent=self.root)
            elif operation == "closed":
                self._destroy_window()
            else:
                self._log(detail)

    def _log(self, text):
        self.log.grid()
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        # Keep a bounded log even when processing thousands of files.
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 200:
            self.log.delete("1.0", f"{line_count - 200}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _update_controls(self):
        available = self.backend is not None and not self.busy and not self.scanning and not self.closing
        for widget in (self.folder_button, self.files_button, self.scan_button, self.configure_button):
            widget.configure(state="normal" if available else "disabled")
        self.source_entry.configure(state="normal" if available else "disabled")
        for widget in (self.output_entry, self.output_button, self.interval_entry):
            widget.configure(state="disabled" if self.busy or self.closing else "normal")
        for widget in (self.format_combo, self.temp_combo, self.pres_combo):
            widget.configure(state="disabled" if self.busy or self.closing else "readonly")
        self.export_button.configure(state="normal" if available and self.chosen else "disabled")
        self.cancel_button.configure(state="normal" if self.busy and not self.cancel_event.is_set() else "disabled")
        self.selection_info.set(f"已勾选 {len(self.chosen)} / {len(self.files)} 个文件")
        self.prev_button.configure(state="normal" if self.page > 0 else "disabled")
        self.next_button.configure(state="normal" if (self.page + 1) * PAGE_SIZE < len(self.files) else "disabled")

    def choose_source_folder(self):
        path = filedialog.askdirectory(title="选择 HDF5 文件所在文件夹", parent=self.root)
        if path:
            self.source.set(path)
            self.scan_source()

    def choose_source_files(self):
        paths = filedialog.askopenfilenames(title="选择 HDF5 文件", parent=self.root,
                                            filetypes=[("HDF5 数据", "*.h5 *.hdf5"), ("所有文件", "*")])
        if not paths:
            return
        self.source.set(paths[0] if len(paths) == 1 else os.path.dirname(paths[0]))
        self.scanning = True
        self.status.set("正在读取文件列表…")
        self._update_controls()
        backend = self.backend
        def scan():
            files = []
            seen = set()
            for path in paths:
                for item in backend.scan_directory(backend.ScanPayload(path=path)).get("files", []):
                    if item["path"] not in seen:
                        files.append(item)
                        seen.add(item["path"])
            return sorted(files, key=lambda item: item["name"].lower())
        self._worker("scan", scan)

    def scan_source(self):
        if self.backend is None or self.busy or self.scanning:
            return
        path = self.source.get().strip()
        if not path:
            self.choose_source_folder()
            return
        self.scanning = True
        self.status.set("正在扫描文件…")
        self._update_controls()
        backend = self.backend
        self._worker("scan", lambda: backend.scan_directory(backend.ScanPayload(path=path)).get("files", []))

    def choose_output_folder(self):
        path = filedialog.askdirectory(title="选择导出保存文件夹", parent=self.root)
        if path:
            self.output.set(path)

    def _settings_changed(self):
        temperature = {"°C 摄氏度": "°C", "°F 华氏度": "°F", "K 开尔文": "K"}.get(self.temperature.get(), "°C")
        self.settings_summary.set(f"每 {self.interval.get()} 秒取样 · 温度 {temperature} · 压力 {self.pressure.get()}")
        if self.smoke_test or self.backend is None or self.closed:
            return
        if self.save_after is not None:
            self.root.after_cancel(self.save_after)
        self.save_after = self.root.after(500, self._save_settings)

    def _settings_payload(self):
        try:
            interval = float(self.interval.get())
        except ValueError:
            interval = 10
        if not math.isfinite(interval) or interval <= 0:
            interval = 10
        return {"h5_src_path": self.source.get().strip(), "h5_out_path": self.output.get().strip(),
                "desktop_settings": {"interval": interval,
                                     "tempUnit": TEMPERATURE_UNITS.get(self.temperature.get(), "degC"),
                                     "presUnit": PRESSURE_UNITS.get(self.pressure.get(), "PSI"),
                                     "format": "csv" if self.format.get().startswith("CSV") else "xlsx"}}

    def _save_settings(self):
        self.save_after = None
        payload = self._settings_payload()
        backend = self.backend
        if backend is not None:
            self._worker("saved", lambda: backend.save_config(payload))

    def save_presets(self):
        backend, presets = self.backend, copy.deepcopy(self.presets)
        if backend is not None:
            self._worker("saved", lambda: backend.save_config({"h5_field_presets": presets}))

    def toggle_advanced(self):
        if self.advanced.winfo_ismapped():
            self.advanced.grid_remove()
            self.advanced_button.configure(text="采样与单位  ›")
        else:
            self.advanced.grid()
            self.advanced_button.configure(text="采样与单位  ⌄")

    def _render_files(self):
        focus = self.tree.focus()
        highlighted = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        begin = self.page * PAGE_SIZE
        path_to_state = {self.task_paths[task_id]: state for task_id, state in self.task_states.items()
                         if task_id in self.task_paths}
        for item in self.files[begin:begin + PAGE_SIZE]:
            path = item["path"]
            config = self.configs.get(path)
            data = self.inspections.get(path)
            fields = config.get("selectedFields", []) if config else default_fields(
                data.get("datasets", []), data.get("detectedTimeField")) if data else None
            summary = f"{len(fields)} 个字段" if fields is not None else "自动识别压力 / 温度"
            if config:
                summary += " · 已自定义"
            task = path_to_state.get(path)
            if task:
                status = task.get("status")
                state_text = {"completed": "已完成", "failed": "失败", "cancelled": "已取消"}.get(
                    status, f"{task.get('progress', 0):.0f}% · {task.get('message', '等待中')}")
            else:
                state_text = "待导出" if fields is None or fields else "无可导出字段"
            self.tree.insert("", "end", iid=path, values=("☑" if path in self.chosen else "☐",
                              item["name"], format_size(item["size"]), summary, state_text))
        if focus and self.tree.exists(focus):
            self.tree.focus(focus)
        existing = [path for path in highlighted if self.tree.exists(path)]
        if existing:
            self.tree.selection_set(existing)
        pages = max(1, math.ceil(len(self.files) / PAGE_SIZE))
        self.page_info.set(f"{len(self.files)} 个文件 · 第 {self.page + 1} / {pages} 页")
        self._update_controls()

    def change_page(self, delta):
        next_page = self.page + delta
        if 0 <= next_page < max(1, math.ceil(len(self.files) / PAGE_SIZE)):
            self.page = next_page
            self._render_files()

    def choose_all(self, choose: bool):
        if self.busy:
            return
        self.chosen = set(self.file_by_path) if choose else set()
        self._render_files()

    def _tree_click(self, event):
        path = self.tree.identify_row(event.y)
        if path and self.tree.identify_column(event.x) == "#1" and not self.busy:
            self._toggle_paths([path])

    def _tree_double_click(self, event):
        if self.tree.identify_column(event.x) != "#1":
            path = self.tree.identify_row(event.y)
            if path:
                self.tree.focus(path)
                self.configure_file()

    def _toggle_highlighted(self, _event):
        if not self.busy:
            self._toggle_paths(self.tree.selection())
        return "break"

    def _toggle_paths(self, paths):
        for path in paths:
            if path in self.chosen:
                self.chosen.remove(path)
            else:
                self.chosen.add(path)
        self._render_files()

    def _focused_path(self):
        path = self.tree.focus()
        if path in self.file_by_path:
            return path
        selected = self.tree.selection()
        return selected[0] if selected else None

    def _file_focus_changed(self, _event):
        path = self._focused_path()
        if not path:
            return
        self._show_detail(path)
        if path not in self.inspections and not self.busy:
            self._inspect(path)

    def _show_detail(self, path):
        config = self.configs.get(path)
        data = self.inspections.get(path)
        if config or data:
            fields = config["selectedFields"] if config else default_fields(
                data["datasets"], data.get("detectedTimeField"))
            names = [field.split("/")[-1] for field in fields]
            shown = "、".join(names[:3]) or "无可导出字段"
            extra = f" 等 {len(fields)} 个字段" if len(fields) > 3 else ""
            self.detail.set(f"当前文件：{shown}{extra}")
        else:
            self.detail.set("正在识别当前文件的压力和温度字段…")

    def _inspect(self, path, edit=False):
        if self.backend is None:
            return
        if path in self.inspecting:
            if edit:
                self.pending_edit.add(path)
            return
        self.inspecting.add(path)
        backend = self.backend
        self._worker("inspect_edit" if edit else "inspect",
                     lambda: (path, backend.inspect_hdf5(backend.InspectPayload(path=path))))

    def configure_file(self):
        if self.busy or self.backend is None:
            return
        path = self._focused_path()
        if path is None and self.files:
            path = self.files[self.page * PAGE_SIZE]["path"]
            self.tree.focus(path)
            self.tree.selection_set(path)
        if path is None:
            return
        if path in self.inspections:
            FileSettings(self, path, self.inspections[path])
        elif path in self.inspecting:
            self.status.set("正在读取文件，完成后自动打开设置。")
            self._inspect(path, edit=True)
        else:
            self.status.set("正在读取文件字段…")
            self._inspect(path, edit=True)

    def _common_export_settings(self):
        try:
            interval = float(self.interval.get())
        except ValueError:
            raise ValueError("采样间隔需要填写大于 0 的数字。") from None
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("采样间隔需要填写大于 0 的数字。")
        return {"interval": interval, "tempUnit": TEMPERATURE_UNITS[self.temperature.get()],
                "presUnit": PRESSURE_UNITS[self.pressure.get()]}

    def start_export(self):
        if self.busy or self.backend is None or not self.chosen:
            return
        output = self.output.get().strip()
        if not output:
            self.choose_output_folder()
            output = self.output.get().strip()
            if not output:
                return
        try:
            common = self._common_export_settings()
        except ValueError as exc:
            messagebox.showerror("请检查采样设置", str(exc), parent=self.root)
            return
        paths = [item["path"] for item in self.files if item["path"] in self.chosen]
        configs = copy.deepcopy(self.configs)
        inspections = dict(self.inspections)
        extension = ".csv" if self.format.get().startswith("CSV") else ".xlsx"
        backend = self.backend
        self.busy = self.preparing = True
        self.cancel_event = threading.Event()
        cancel_event = self.cancel_event
        self.task_ids = []
        self.export_total = len(paths)
        self.task_paths.clear()
        self.task_states.clear()
        self.logged_tasks.clear()
        self.status.set("正在识别字段并准备导出…")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.open_button.configure(state="disabled")
        self._update_controls()
        self._save_settings()

        def prepare():
            export_configs = []
            names = []
            batch_paths = []
            used_names = set()
            batch_limit = min(64, int(getattr(backend, "MAX_ACTIVE_TASKS", 64)))

            def submit_batch(wait_for_completion):
                result = backend.trigger_export(backend.ExportPayload(configs=export_configs, outputDir=output))
                ids = result["taskIds"]
                self.events.put(("submitted", (ids, list(batch_paths), list(names))))
                if cancel_event.is_set():
                    for task_id in ids:
                        backend.cancel_task(task_id)
                # Bound the engine queue and metadata memory for very large
                # directories. Tk's own progress polling stays independent.
                if wait_for_completion:
                    while not cancel_event.is_set():
                        states = backend.get_status(",".join(ids))
                        if all(state.get("status") in TERMINAL_STATES for state in states.values()):
                            self.events.put(("batch_finished", states))
                            break
                        cancel_event.wait(0.5)
                    if cancel_event.is_set():
                        for task_id in ids:
                            backend.cancel_task(task_id)
                export_configs.clear()
                names.clear()
                batch_paths.clear()

            for path in paths:
                if cancel_event.is_set():
                    self.events.put(("preparation_done", None))
                    return None
                data = inspections.get(path)
                if data is None:
                    data = backend.inspect_hdf5(backend.InspectPayload(path=path))
                    self.events.put(("prepared", (path, data)))
                config = {"filePath": path, "selectedFields": default_fields(data["datasets"], data.get("detectedTimeField")),
                          "timeField": data.get("detectedTimeField"),
                          "timeType": data.get("timeType") if data.get("timeType") in TIME_TYPES.values() and data.get("timeType") != "none" else None,
                          "startTimeStr": None, "endTimeStr": None, "baseDate": "1970-01-01 00:00:00",
                          "customName": default_name(os.path.basename(path)), **common}
                if path in configs:
                    config.update(configs[path])
                    if not configs[path].get("overrideSampling", False):
                        config.update(common)
                config.pop("overrideSampling", None)
                if not config["selectedFields"]:
                    raise ValueError(f"{os.path.basename(path)} 没有选中可导出的字段。请打开字段设置。")
                name = os.path.splitext(config["customName"])[0] + extension
                base = os.path.splitext(name)[0]
                number = 2
                while name.casefold() in used_names:
                    name = f"{base}_{number}{extension}"
                    number += 1
                used_names.add(name.casefold())
                config["customName"] = name
                names.append(os.path.join(output, name))
                batch_paths.append(path)
                export_configs.append(backend.ExportConfig(**config))
                if len(export_configs) >= batch_limit:
                    submit_batch(wait_for_completion=True)
            if cancel_event.is_set():
                self.events.put(("preparation_done", None))
                return None
            if export_configs:
                submit_batch(wait_for_completion=False)
            self.events.put(("preparation_done", None))
            return None
        self._worker("submit", prepare)

    def _poll_status(self):
        if not self.busy or not self.task_ids or self.closed:
            return
        active_ids = [task_id for task_id in self.task_ids
                      if self.task_states.get(task_id, {}).get("status") not in TERMINAL_STATES]
        if not active_ids:
            if self.preparing:
                self.root.after(750, self._poll_status)
            else:
                self._handle_event("status", {})
            return
        backend, ids = self.backend, ",".join(active_ids)
        self._worker("status", lambda: backend.get_status(ids))

    def _end_export(self, message):
        self.busy = self.preparing = False
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress["value"] = sum(
            100 if state.get("status") in TERMINAL_STATES else float(state.get("progress", 0))
            for state in self.task_states.values()
        ) / max(self.export_total, 1)
        self.status.set(message)
        self.open_button.configure(state="normal" if self.output.get().strip() else "disabled")
        self._render_files()

    def cancel_export(self):
        if not self.busy:
            return
        self.cancel_event.set()
        self.status.set("正在停止任务，请稍候…")
        self._update_controls()
        backend, ids = self.backend, list(self.task_ids)
        if ids:
            self._worker("cancelled", lambda: [backend.cancel_task(task_id) for task_id in ids])

    def open_output(self):
        path = self.output.get().strip()
        if not path:
            return
        def open_folder():
            if sys.platform == "win32":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        self._worker("opened", open_folder)

    def close(self):
        if self.closing:
            return
        if self.busy and not messagebox.askokcancel("退出软件", "当前仍在导出。退出将取消这些任务，是否退出？", parent=self.root):
            return
        self.closing = True
        self.cancel_event.set()
        backend, ids = self.backend, list(self.task_ids)
        settings = self._settings_payload()
        def finish():
            if backend is not None:
                for task_id in ids:
                    backend.cancel_task(task_id)
                backend.save_config(settings)
        if backend is not None and not self.smoke_test:
            if self.save_after is not None:
                self.root.after_cancel(self.save_after)
                self.save_after = None
            self.status.set("正在保存设置并关闭…")
            self._update_controls()
            self._worker("closed", finish)
            # Saving stays off Tk's event thread. A stalled removable drive must
            # not prevent the user from closing the window indefinitely.
            self.root.after(2500, self._destroy_window)
        else:
            self._destroy_window()

    def _destroy_window(self):
        if self.closed:
            return
        self.closed = True
        self.report["success"] = True
        self.root.destroy()


class FileSettings:
    """A compact optional editor; one paged Treeview replaces many check widgets."""

    def __init__(self, app: DesktopApp, path: str, data: dict):
        self.app, self.path, self.data = app, path, data
        self.design = ui = app.design
        self.window = ctk.CTkToplevel(app.root, fg_color=ui.background)
        self.window.title("字段与时间设置")
        scale = _window_scale(self.window)
        available_width = int((app.root.winfo_screenwidth() - 80) / scale)
        available_height = int((app.root.winfo_screenheight() - 96) / scale)
        width = min(850, max(760, available_width))
        height = min(650, max(600, available_height))
        self.window.maxsize(available_width, available_height)
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(760, 600)
        self.window.transient(app.root)
        self.window.grab_set()
        self.paths = [item["path"] for item in data["datasets"]]
        previous = app.configs.get(path, {})
        self.selected = set(previous.get("selectedFields", default_fields(data["datasets"], data.get("detectedTimeField"))))
        self.filter = tk.StringVar()
        self.field_page = 0
        self.field_info = tk.StringVar()
        self.name = tk.StringVar(value=previous.get("customName", default_name(os.path.basename(path))))
        self.time_field = tk.StringVar(value=previous.get("timeField") or data.get("detectedTimeField") or "自动 / 各字段自带时间")
        self.time_type = tk.StringVar(value=_label_for(TIME_TYPES, previous.get("timeType") or data.get("timeType"), "未识别 / 自动"))
        self.start = tk.StringVar(value=previous.get("startTimeStr") or "")
        self.end = tk.StringVar(value=previous.get("endTimeStr") or "")
        self.base_date = tk.StringVar(value=previous.get("baseDate", "1970-01-01 00:00:00"))
        self.override = tk.BooleanVar(value=previous.get("overrideSampling", False))
        self.interval = tk.StringVar(value=str(previous.get("interval", app.interval.get())))
        self.temperature = tk.StringVar(value=_label_for(TEMPERATURE_UNITS, previous.get("tempUnit"), app.temperature.get()))
        self.pressure = tk.StringVar(value=_label_for(PRESSURE_UNITS, previous.get("presUnit"), app.pressure.get()))
        content = ui.frame(self.window)
        content.pack(fill="both", expand=True, padx=20, pady=16)
        content.rowconfigure(2, weight=1)
        content.columnconfigure(0, weight=1)
        heading = ui.frame(content)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ui.label(heading, text="字段与时间设置", font=(ui.family, 21, "bold")).pack(anchor="w")
        ui.label(heading, text=os.path.basename(path), muted=True, font=ui.small).pack(anchor="w", pady=(3, 0))
        self.tab_selector = ctk.CTkSegmentedButton(
            content, values=["导出字段", "时间与采样", "文件名称"], height=34,
            font=(ui.family, 13, "bold"), corner_radius=8, border_width=3,
            fg_color="#E8EDF5", selected_color="#D6E5FF", selected_hover_color="#C4DBFF",
            unselected_color="#E8EDF5", unselected_hover_color="#DEE7F4", text_color="#22549D",
            command=self._show_tab)
        self.tab_selector.grid(row=1, column=0, sticky="w", pady=(0, 10))
        fields = ui.frame(content, card=True)
        timing = ui.frame(content, card=True)
        naming = ui.frame(content, card=True)
        self.pages = {"导出字段": fields, "时间与采样": timing, "文件名称": naming}
        for page in self.pages.values():
            page.grid(row=2, column=0, sticky="nsew")
            page.grid_remove()
        self.tab_selector.set("导出字段")
        self._show_tab("导出字段")
        fields.columnconfigure(0, weight=1)
        fields.rowconfigure(2, weight=1, minsize=round(120 * scale))
        presets = ui.frame(fields)
        presets.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        ui.label(presets, text="常用配置", font=ui.small).pack(side="left", padx=(0, 10))
        self.preset = tk.StringVar(value="自动推荐")
        self.preset_combo = ui.combo(presets, self.preset, ["自动推荐"] + list(app.presets),
                                      width=240, command=lambda _choice: self.apply_preset())
        self.preset_combo.pack(side="left", fill="x", expand=True)
        ui.button(presets, "保存为配置", self.save_preset, quiet=True, width=104).pack(side="left", padx=6)
        ui.button(presets, "删除配置", self.delete_preset, quiet=True, width=90).pack(side="left")
        search = ui.frame(fields)
        search.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))
        ui.label(search, text="搜索字段", font=ui.small).pack(side="left", padx=(0, 10))
        ui.entry(search, self.filter).pack(side="left", fill="x", expand=True)
        ui.button(search, "恢复推荐", self.restore_defaults, quiet=True, width=100).pack(side="left", padx=(6, 0))
        table = ui.frame(fields)
        table.grid(row=2, column=0, sticky="nsew", padx=14)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1, minsize=round(120 * scale))
        self.tree = ttk.Treeview(table, columns=("choose", "field", "rows"), show="headings",
                                 selectmode="extended", style="Modern.Treeview")
        for column, title, column_width in (("choose", "选择", 48), ("field", "字段路径", 480), ("rows", "数据条数", 100)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=column_width, stretch=column == "field", anchor="w" if column == "field" else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ctk.CTkScrollbar(table, orientation="vertical", command=self.tree.yview,
                                  fg_color=ui.surface, button_color="#C8D4E6",
                                  button_hover_color="#A7B9D1", width=12)
        scroll.grid(row=0, column=1, sticky="ns", padx=(3, 0))
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Button-1>", self.toggle_field)
        self.tree.bind("<space>", self.toggle_selected_fields)
        controls = ui.frame(fields)
        controls.grid(row=3, column=0, sticky="ew", padx=14, pady=(6, 0))
        ui.button(controls, "全选搜索结果", lambda: self.select_filtered(True),
                  quiet=True, width=112, height=30).pack(side="left")
        ui.button(controls, "清空搜索结果", lambda: self.select_filtered(False),
                  quiet=True, width=112, height=30).pack(side="left", padx=6)
        ui.button(controls, "下一页 ›", lambda: self.field_change_page(1),
                  quiet=True, width=78, height=30).pack(side="right")
        ui.button(controls, "‹ 上一页", lambda: self.field_change_page(-1),
                  quiet=True, width=78, height=30).pack(side="right", padx=6)
        ui.label(fields, textvariable=self.field_info, muted=True, font=ui.small).grid(
            row=4, column=0, sticky="w", padx=14, pady=(3, 10))
        self.filter.trace_add("write", self.filter_changed)

        time_content = ui.frame(timing)
        time_content.pack(fill="both", expand=True, padx=18, pady=14)
        time_content.columnconfigure(1, weight=1)
        self._row(time_content, 0, "时间字段", ui.combo(time_content, self.time_field,
                  ["自动 / 各字段自带时间"] + data.get("timeFields", self.paths)))
        self._row(time_content, 1, "时间格式", ui.combo(time_content, self.time_type, TIME_TYPES))
        self._row(time_content, 2, "开始时间", ui.entry(time_content, self.start))
        self._row(time_content, 3, "结束时间", ui.entry(time_content, self.end))
        ui.label(time_content, text="留空使用全部时间范围。日期示例：2026-10-08 08:00:00（UTC）。",
                  muted=True, font=ui.small).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 6))
        time_min, time_max = data.get("timeMinStr"), data.get("timeMaxStr")
        if time_min is not None and time_max is not None:
            ui.label(time_content, text=f"检测范围：{time_min}  —  {time_max}", muted=True,
                      font=ui.small, wraplength=720).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self._row(time_content, 6, "相对时间基准", ui.entry(time_content, self.base_date))
        ctk.CTkCheckBox(time_content, text="此文件使用单独的采样与单位", variable=self.override,
                        font=ui.font, text_color=ui.ink, fg_color=ui.accent, hover_color="#1D51CA",
                        border_color="#BFCCE0", corner_radius=5, checkbox_width=20,
                        checkbox_height=20, border_width=2, height=24).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(2, 8))
        self._row(time_content, 8, "采样间隔（秒）", ui.entry(time_content, self.interval))
        self._row(time_content, 9, "温度 / 压力", self._unit_controls(time_content))
        name_content = ui.frame(naming)
        name_content.pack(fill="both", expand=True, padx=18, pady=18)
        name_content.columnconfigure(0, weight=1)
        ui.label(name_content, text="输出文件名称", font=ui.heading).grid(row=0, column=0, sticky="w", pady=(0, 10))
        ui.entry(name_content, self.name).grid(row=1, column=0, sticky="ew")
        ui.label(name_content, text="后缀按主界面所选格式自动设置。批量文件重名时自动编号。",
                  muted=True, font=ui.small, wraplength=680).grid(row=2, column=0, sticky="w", pady=(12, 0))
        buttons = ui.frame(content)
        buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ui.button(buttons, "保存设置", self.save, primary=True, width=132, height=36).pack(side="right")
        ui.button(buttons, "取消", self.window.destroy, quiet=True, width=90).pack(side="right", padx=8)
        self.render_fields()

    def _show_tab(self, selected):
        for title, frame in self.pages.items():
            if title == selected:
                frame.grid()
            else:
                frame.grid_remove()

    def _unit_controls(self, parent):
        ui = self.design
        frame = ui.frame(parent)
        ui.combo(frame, self.temperature, TEMPERATURE_UNITS, width=160).pack(side="left")
        ui.combo(frame, self.pressure, PRESSURE_UNITS, width=120).pack(side="left", padx=12)
        return frame

    def _row(self, parent, row, label, widget):
        self.design.label(parent, text=label, font=self.design.small).grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=(0, 6))
        widget.grid(row=row, column=1, sticky="ew", pady=(0, 6))

    def filtered_fields(self):
        query = self.filter.get().strip().lower().replace("temperature", "temp").replace("pressure", "pres")
        return [item for item in self.data["datasets"] if query in item["path"].lower()]

    def filter_changed(self, *_args):
        self.field_page = 0
        self.render_fields()

    def render_fields(self):
        self.tree.delete(*self.tree.get_children())
        fields = self.filtered_fields()
        begin = self.field_page * FIELD_PAGE_SIZE
        for item in fields[begin:begin + FIELD_PAGE_SIZE]:
            path = item["path"]
            self.tree.insert("", "end", iid=path, values=("☑" if path in self.selected else "☐", path, f"{item['size']:,}"))
        pages = max(1, math.ceil(len(fields) / FIELD_PAGE_SIZE))
        self.field_info.set(f"已选 {len(self.selected)} 个字段 · 搜索结果 {len(fields)} · 第 {self.field_page + 1} / {pages} 页")

    def field_change_page(self, delta):
        new_page = self.field_page + delta
        if 0 <= new_page < max(1, math.ceil(len(self.filtered_fields()) / FIELD_PAGE_SIZE)):
            self.field_page = new_page
            self.render_fields()

    def toggle_field(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        path = self.tree.identify_row(event.y)
        if path:
            self.selected.symmetric_difference_update({path})
            self.render_fields()

    def toggle_selected_fields(self, _event):
        self.selected.symmetric_difference_update(self.tree.selection())
        self.render_fields()
        return "break"

    def select_filtered(self, select):
        paths = {item["path"] for item in self.filtered_fields()}
        if select:
            self.selected.update(paths)
        else:
            self.selected.difference_update(paths)
        self.render_fields()

    def restore_defaults(self):
        self.selected = set(default_fields(self.data["datasets"], self.data.get("detectedTimeField")))
        self.render_fields()

    def apply_preset(self, _event=None):
        name = self.preset.get()
        if name == "自动推荐":
            self.restore_defaults()
            return
        preset = self.app.presets.get(name, {})
        fields = match_preset_fields(self.paths, preset.get("fields", []))
        if not fields:
            messagebox.showwarning("未匹配字段", "该文件未找到此配置中的字段，请选择其他配置或手动勾选。", parent=self.window)
            return
        self.selected = set(fields)
        time_field = preset.get("timeField")
        available_time = set(self.data.get("timeFields", self.paths))
        if time_field and time_field in available_time:
            self.time_field.set(time_field)
            self.time_type.set(_label_for(TIME_TYPES, preset.get("timeType"), "未识别 / 自动"))
        else:
            self.time_field.set("自动 / 各字段自带时间")
            self.time_type.set("未识别 / 自动")
        self.start.set(preset.get("startTimeStr") or "")
        self.end.set(preset.get("endTimeStr") or "")
        self.base_date.set(preset.get("baseDate") or "1970-01-01 00:00:00")
        self.interval.set(str(preset.get("interval", self.app.interval.get())))
        self.temperature.set(_label_for(TEMPERATURE_UNITS, preset.get("tempUnit"), self.app.temperature.get()))
        self.pressure.set(_label_for(PRESSURE_UNITS, preset.get("presUnit"), self.app.pressure.get()))
        self.override.set(True)
        self.render_fields()

    def save_preset(self):
        if not self.selected:
            messagebox.showerror("请选择字段", "至少选择一个字段再保存配置。", parent=self.window)
            return
        name = simpledialog.askstring("保存常用配置", "配置名称：", parent=self.window,
                                      initialvalue="" if self.preset.get() == "自动推荐" else self.preset.get())
        if not name or not name.strip():
            return
        name = name.strip()
        try:
            interval = float(self.interval.get())
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("请检查采样间隔", "采样间隔需要填写大于 0 的数字。", parent=self.window)
            return
        time_type = TIME_TYPES[self.time_type.get()]
        self.app.presets[name] = {
            "fields": [path.rsplit("/", 1)[-1] for path in self.paths if path in self.selected],
            "timeField": None if self.time_field.get() == "自动 / 各字段自带时间" else self.time_field.get(),
            "timeType": None if time_type == "none" else time_type,
            "startTimeStr": self.start.get().strip(), "endTimeStr": self.end.get().strip(),
            "baseDate": self.base_date.get().strip(), "interval": interval,
            "tempUnit": TEMPERATURE_UNITS[self.temperature.get()], "presUnit": PRESSURE_UNITS[self.pressure.get()],
        }
        self.app.save_presets()
        self.preset_combo.configure(values=["自动推荐"] + list(self.app.presets))
        self.preset.set(name)

    def delete_preset(self):
        name = self.preset.get()
        if name not in self.app.presets:
            return
        if not messagebox.askyesno("删除常用配置", f"删除配置“{name}”？", parent=self.window):
            return
        del self.app.presets[name]
        self.app.save_presets()
        self.preset_combo.configure(values=["自动推荐"] + list(self.app.presets))
        self.preset.set("自动推荐")

    def save(self):
        if not self.selected:
            messagebox.showerror("请选择字段", "至少选择一个导出字段。", parent=self.window)
            return
        name = self.name.get().strip()
        if not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
            messagebox.showerror("请检查文件名称", "名称不能为空，也不能包含路径或特殊字符。", parent=self.window)
            return
        try:
            interval = float(self.interval.get())
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("请检查采样间隔", "采样间隔需要填写大于 0 的数字。", parent=self.window)
            return
        self.app.configs[self.path] = {
            "filePath": self.path, "selectedFields": [path for path in self.paths if path in self.selected],
            "timeField": None if self.time_field.get() == "自动 / 各字段自带时间" else self.time_field.get(),
            "timeType": None if TIME_TYPES[self.time_type.get()] == "none" else TIME_TYPES[self.time_type.get()],
            "startTimeStr": self.start.get().strip() or None,
            "endTimeStr": self.end.get().strip() or None, "baseDate": self.base_date.get().strip(),
            "customName": name, "overrideSampling": self.override.get(), "interval": interval,
            "tempUnit": TEMPERATURE_UNITS[self.temperature.get()], "presUnit": PRESSURE_UNITS[self.pressure.get()],
        }
        self.app._render_files()
        self.app._show_detail(self.path)
        self.window.destroy()


def main(smoke_test: bool = False) -> dict:
    """Run the native app, or paint and close it for Windows CI verification."""
    started = time.perf_counter()
    root = ctk.CTk()
    desktop = DesktopApp(root, smoke_test=smoke_test, started=started)
    root.mainloop()
    return desktop.report


if __name__ == "__main__":
    main(smoke_test="--smoke-test" in sys.argv)
