"""Native UI regressions using a bounded fake engine, without reading HDF5 data."""

import os
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
    import desktop
except ImportError:
    tk = None
    desktop = None


class FakeBackend:
    MAX_ACTIVE_TASKS = 64
    ExportConfig = ExportPayload = InspectPayload = types.SimpleNamespace

    def __init__(self):
        self.lock = threading.Lock()
        self.states = {}
        self.submissions = []
        self.total = 0

    def inspect_hdf5(self, payload):
        return {
            "datasets": [{"path": "Pressure", "size": 10}, {"path": "Temp", "size": 10}],
            "timeType": "none", "detectedTimeField": None,
        }

    def trigger_export(self, payload):
        if len(payload.configs) > self.MAX_ACTIVE_TASKS:
            raise AssertionError("Desktop exceeded the engine queue limit")
        with self.lock:
            self.submissions.append(payload)
            task_ids = []
            for config in payload.configs:
                task_id = "task%d" % self.total
                self.total += 1
                task_ids.append(task_id)
                self.states[task_id] = {
                    "status": "completed", "progress": 100,
                    "output_path": os.path.join(payload.outputDir, config.customName),
                }
            # Mirrors the production engine's bounded terminal task history.
            while len(self.states) > 100:
                del self.states[next(iter(self.states))]
            return {"taskIds": task_ids}

    def get_status(self, task_ids):
        with self.lock:
            return {
                task_id: dict(self.states.get(task_id, {
                    "status": "failed", "progress": 100, "error": "Task not found",
                })) for task_id in task_ids.split(",")
            }

    def save_config(self, config):
        return {"status": "ok"}

    def cancel_task(self, task_id):
        return {"success": True}


@unittest.skipIf(tk is None, "Python was installed without tkinter")
class DesktopTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest("No graphical display is available: %s" % exc)
        self.folder = tempfile.TemporaryDirectory()
        self.engine_import = patch("desktop.importlib.import_module")
        self.importer = self.engine_import.start()
        self.error_dialog = patch.object(desktop.messagebox, "showerror", side_effect=AssertionError("Unexpected GUI error"))
        self.error_dialog.start()
        self.loader = patch.object(desktop.DesktopApp, "_load_backend", new=lambda _self: None)
        self.loader.start()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: (self.callback_errors.append(error), self.root.quit())
        # Keep normal Tk idle/layout callbacks intact; mock only the engine
        # loader so these tests exercise the production paint and event loop.
        self.ui = desktop.DesktopApp(self.root)
        self.root.update_idletasks()
        self.root.withdraw()
        self.ui.smoke_test = False
        self.backend = FakeBackend()
        self.ui.backend = self.backend

    def tearDown(self):
        self.ui.closed = True
        self.root.destroy()
        self.error_dialog.stop()
        self.loader.stop()
        self.engine_import.stop()
        self.folder.cleanup()

    def test_large_batch_preserves_results_after_engine_history_is_pruned(self):
        files = [{
            "path": os.path.join(self.folder.name, "Instruct_%d-Well.h5" % number),
            "name": "Instruct_%d-Well.h5" % number, "size": 100,
        } for number in range(205)]
        self.ui._handle_event("scan", files)
        self.assertEqual(len(self.ui.tree.get_children()), 100)
        self.ui.change_page(1)
        self.assertEqual(len(self.ui.tree.get_children()), 100)
        self.ui.change_page(1)
        self.assertEqual(len(self.ui.tree.get_children()), 5)
        self.ui.output.set(self.folder.name)
        self.ui.start_export()

        def finish_when_idle():
            if self.ui.busy:
                self.root.after(25, finish_when_idle)
            else:
                self.root.quit()

        deadline = self.root.after(2500, self.root.quit)
        self.root.after(25, finish_when_idle)
        self.root.mainloop()
        self.root.after_cancel(deadline)
        self.assertFalse(self.callback_errors, self.callback_errors)
        self.assertFalse(self.ui.busy, "Mock export did not complete in time")
        self.assertEqual(self.backend.total, 205)
        self.assertEqual(len(self.backend.submissions), 4)
        self.assertLessEqual(len(self.backend.states), 100)
        self.assertEqual(len(self.ui.task_states), 205)
        self.assertTrue(all(state["status"] == "completed" for state in self.ui.task_states.values()))
        self.assertEqual(float(self.ui.progress["value"]), 100)
        configs = [config for payload in self.backend.submissions for config in payload.configs]
        names = [config.customName.casefold() for config in configs]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(config.timeType is None for config in configs))
        self.importer.assert_not_called()

    def test_small_screen_keeps_file_rows_and_all_export_controls_visible(self):
        self.ui._handle_event("scan", [{
            "path": os.path.join(self.folder.name, "sample%d.h5" % number),
            "name": "sample%d.h5" % number, "size": 100,
        } for number in range(6)])
        self.root.geometry("944x672")
        self.root.deiconify()
        stages = []

        def check_layout():
            self.assertTrue(self.root.winfo_ismapped())
            self.assertGreaterEqual(self.ui.tree.winfo_height(), 120)
            third_row = self.ui.tree.bbox(self.ui.tree.get_children()[2])
            self.assertTrue(third_row, "The third file row has no visible viewport")
            self.assertLessEqual(third_row[1] + third_row[3], self.ui.tree.winfo_height())
            controls = [
                self.ui.source_entry, self.ui.folder_button, self.ui.files_button,
                self.ui.configure_button, self.ui.output_entry, self.ui.output_button,
                self.ui.format_combo, self.ui.advanced_button, self.ui.export_button,
                self.ui.cancel_button, self.ui.open_button, self.ui.progress,
            ]
            if len(stages) >= 1:
                controls.extend([self.ui.interval_entry, self.ui.temp_combo, self.ui.pres_combo])
            if len(stages) >= 2:
                controls.append(self.ui.log)
            for widget in controls:
                self.assertTrue(widget.winfo_ismapped(), str(widget))
                left = widget.winfo_rootx() - self.root.winfo_rootx()
                top = widget.winfo_rooty() - self.root.winfo_rooty()
                self.assertGreaterEqual(left, 0, str(widget))
                self.assertGreaterEqual(top, 0, str(widget))
                self.assertLessEqual(left + widget.winfo_width(), self.root.winfo_width(), str(widget))
                self.assertLessEqual(top + widget.winfo_height(), self.root.winfo_height(), str(widget))
            stages.append(self.ui.tree.winfo_height())
            if len(stages) == 1:
                self.assertFalse(self.ui.log.winfo_ismapped())
                self.ui.toggle_advanced()
                self.root.after(30, check_layout)
            elif len(stages) == 2:
                self.ui._log("完成：Pressure.xlsx")
                self.root.after(30, check_layout)
            else:
                self.root.quit()

        deadline = self.root.after(2500, self.root.quit)
        self.root.after(75, check_layout)
        self.root.mainloop()
        self.root.after_cancel(deadline)
        self.assertFalse(self.callback_errors, self.callback_errors)
        self.assertEqual(len(stages), 3, "Not all compact layout states were checked")
        self.importer.assert_not_called()

    def test_portable_presets_match_fields_and_missing_clock_uses_automatic_time(self):
        path = os.path.join(self.folder.name, "sample.h5")
        data = {
            "datasets": [
                {"path": "well/EQRTZ S1 PRES PSI A", "size": 100},
                {"path": "well/EQRTZ S1 TEMP CELSIUS A", "size": 100},
                {"path": "time", "size": 100},
            ],
            "detectedTimeField": "time", "timeType": "timestamp_seconds", "timeFields": ["time"],
        }
        self.ui.files = [{"path": path, "name": "sample.h5", "size": 100}]
        self.ui.file_by_path = {path: self.ui.files[0]}
        self.ui.inspections[path] = data
        self.ui.presets = {"普通井口": {
            "fields": ["eqrtz s1 pres psi a", "EQRTZ S1 TEMP CELSIUS A"],
            "timeField": "another-file/time", "presUnit": "kPa",
        }}
        settings = desktop.FileSettings(self.ui, path, data)
        settings.preset.set("普通井口")
        settings.apply_preset()
        settings.name.set("Pressure.xlsx")
        settings.save()
        config = self.ui.configs[path]
        self.assertEqual(config["selectedFields"], ["well/EQRTZ S1 PRES PSI A", "well/EQRTZ S1 TEMP CELSIUS A"])
        self.assertIsNone(config["timeField"])
        self.assertIsNone(config["timeType"])
        self.assertEqual(config["presUnit"], "kPa")
        self.assertEqual(config["customName"], "Pressure.xlsx")
        self.importer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
