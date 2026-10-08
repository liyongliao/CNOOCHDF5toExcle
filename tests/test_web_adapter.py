"""The optional web adapter keeps the existing API request and error contracts."""
import importlib.util
import unittest
from unittest.mock import patch

import converter

WEB_AVAILABLE = importlib.util.find_spec("fastapi") is not None
if WEB_AVAILABLE:
    import app as web


@unittest.skipUnless(WEB_AVAILABLE, "Optional FastAPI web dependencies are not installed")
class WebAdapterTests(unittest.TestCase):
    def test_export_request_is_converted_to_core_dataclasses(self):
        request = web.ExportPayload(configs=[dict(filePath="sample.h5", selectedFields=["pressure"],
                                                  customName="sample.xlsx", interval=15)], outputDir="output")
        with patch.object(converter, "trigger_export", return_value={"taskIds": ["test"]}) as trigger:
            self.assertEqual(web.trigger_export(request), {"taskIds": ["test"]})
        payload = trigger.call_args[0][0]
        self.assertIsInstance(payload, converter.ExportPayload)
        self.assertIsInstance(payload.configs[0], converter.ExportConfig)
        self.assertEqual(payload.configs[0].selectedFields, ["pressure"])
        self.assertEqual(payload.configs[0].interval, 15.0)
        self.assertEqual(payload.configs[0].presUnit, "PSI")

    def test_scan_and_inspect_use_core_payloads(self):
        with patch.object(converter, "scan_directory", return_value={"files": []}) as scan:
            self.assertEqual(web.scan_directory(web.ScanPayload(path="sample")), {"files": []})
        self.assertIsInstance(scan.call_args[0][0], converter.ScanPayload)
        with patch.object(converter, "inspect_hdf5", return_value={"datasets": []}) as inspect:
            self.assertEqual(web.inspect_hdf5(web.InspectPayload(path="sample.h5")), {"datasets": []})
        self.assertIsInstance(inspect.call_args[0][0], converter.InspectPayload)

    def test_core_errors_keep_http_status_and_detail(self):
        with patch.object(converter, "save_config", side_effect=converter.ConversionError(500, "无法保存配置")):
            with self.assertRaises(web.HTTPException) as error:
                web.save_config({"h5_out_path": "output"})
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(error.exception.detail, "无法保存配置")

    def test_all_existing_api_routes_remain_available(self):
        routes = {(route.path, method) for route in web.app.routes for method in getattr(route, "methods", [])}
        self.assertTrue({("/api/scan", "POST"), ("/api/inspect", "POST"), ("/api/export", "POST"),
                         ("/api/browse", "POST"), ("/api/status", "GET"), ("/api/cancel", "POST"),
                         ("/api/config", "GET"), ("/api/config", "POST"), ("/", "GET")} <= routes)


if __name__ == "__main__":
    unittest.main()
