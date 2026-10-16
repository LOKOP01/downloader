"""Launch Windows shell apps without inheriting the frozen application's DLL path."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading

_lock = threading.RLock()

@contextmanager
def system_dll_search():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    with _lock:
        size = kernel.GetDllDirectoryW(0, None)
        buffer = ctypes.create_unicode_buffer(size + 1)
        if size:
            kernel.GetDllDirectoryW(len(buffer), buffer)
        previous = buffer.value or None
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous):
                raise ctypes.WinError(ctypes.get_last_error())

def open_local_path(path, opener):
    """opener is the Qt shell callback. A successful request is not a visibility guarantee."""
    with system_dll_search():
        if opener():
            return True
        if os.name != "nt":
            return False
        windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
        explorer = windows / "explorer.exe"
        env = os.environ.copy()
        # Avoid loading Qt/Python shipped DLLs through PATH or current directory.
        env["PATH"] = os.pathsep.join([str(windows / "System32"), str(windows)])
        for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONHOME", "PYTHONPATH"):
            env.pop(name, None)
        subprocess.Popen([str(explorer), os.path.normpath(str(path))], cwd=str(windows), env=env)
        return True
