import os,sys,ctypes,unittest
from unittest.mock import patch
from pathlib import Path
from app.core.system_open import system_dll_search,open_local_path

class SystemOpenTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DLL search test")
    def test_real_dll_search_cleared_and_restored_even_on_failure(self):
        k=ctypes.WinDLL("kernel32",use_last_error=True)
        k.GetDllDirectoryW.argtypes=[ctypes.c_uint,ctypes.c_wchar_p]
        k.SetDllDirectoryW.argtypes=[ctypes.c_wchar_p]
        def current():
            b=ctypes.create_unicode_buffer(32768);k.GetDllDirectoryW(len(b),b);return b.value
        previous=current()
        try:
            self.assertTrue(k.SetDllDirectoryW(str(Path(__file__).parent.resolve())))
            before=current()
            with patch.object(sys,"frozen",True,create=True):
                with self.assertRaisesRegex(RuntimeError,"sentinel"):
                    with system_dll_search():
                        self.assertEqual(current(),"")
                        raise RuntimeError("sentinel")
            self.assertEqual(current(),before)
        finally:k.SetDllDirectoryW(previous or None)

    def test_qt_success_does_not_launch_second_explorer(self):
        with patch("app.core.system_open.subprocess.Popen") as process:
            self.assertTrue(open_local_path(".",lambda:True))
            process.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows shell test")
    def test_fallback_uses_windows_explorer_and_clean_environment(self):
        with patch.dict(os.environ,{"PATH":"bad-bundle","QT_PLUGIN_PATH":"bad-plugin"}),patch("app.core.system_open.subprocess.Popen") as process:
            self.assertTrue(open_local_path("C:/test folder",lambda:False))
        args=process.call_args
        self.assertTrue(str(args.args[0][0]).lower().endswith("explorer.exe"))
        self.assertNotIn("bad-bundle",args.kwargs['env']['PATH'])
        self.assertNotIn('QT_PLUGIN_PATH',args.kwargs['env'])
        self.assertEqual(args.kwargs['cwd'],os.environ.get('SystemRoot','C:/Windows'))
