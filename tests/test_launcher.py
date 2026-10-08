"""Startup must stay independent of the scientific engine and GUI availability."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_import_entry_point_without_loading_engine(self):
        result = subprocess.run(
            [sys.executable, '-c',
             "import run, desktop_entry, sys, json; print(json.dumps([name for name in ('app', 'converter', 'numpy', 'h5py', 'tkinter', 'customtkinter') if name in sys.modules]))"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
        self.assertEqual(json.loads(result.stdout), [])

    def test_help_works_without_initializing_a_window(self):
        result = subprocess.run([sys.executable, str(ROOT / 'run.py'), '--help'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        self.assertIn(b'--self-test', result.stdout)
        self.assertIn(b'--web', result.stdout)


if __name__ == '__main__':
    unittest.main()
