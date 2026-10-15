"""Use an application-local temporary directory without changing Windows settings."""
import os
import tempfile


def configure_runtime_temp(base_dir):
    folder = os.path.join(os.path.abspath(base_dir), ".runtime-temp")
    os.makedirs(folder, exist_ok=True)
    # Verify write access before changing the process environment.
    fd, probe = tempfile.mkstemp(prefix="write-check-", dir=folder)
    os.close(fd)
    os.remove(probe)
    os.environ["TEMP"] = folder
    os.environ["TMP"] = folder
    os.environ["TMPDIR"] = folder
    tempfile.tempdir = folder
    return folder
