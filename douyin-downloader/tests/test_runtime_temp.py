import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from app.core.runtime_temp import configure_runtime_temp

class RuntimeTempTest(unittest.TestCase):
    def test_local_directory_is_writable_and_used_by_tempfile(self):
        previous = tempfile.tempdir
        try:
            with tempfile.TemporaryDirectory() as base, patch.dict(os.environ):
                folder = configure_runtime_temp(base)
                self.assertEqual(os.environ['TEMP'], folder)
                self.assertEqual(os.environ['TMP'], folder)
                with tempfile.TemporaryDirectory() as child:
                    self.assertEqual(Path(child).parent, Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])
        finally:
            tempfile.tempdir = previous

    def test_invalid_destination_does_not_change_environment(self):
        with tempfile.TemporaryDirectory() as base, patch.dict(os.environ):
            before = os.environ.get('TEMP')
            Path(base, '.runtime-temp').write_text('not a directory')
            with self.assertRaises(OSError):
                configure_runtime_temp(base)
            self.assertEqual(os.environ.get('TEMP'), before)
