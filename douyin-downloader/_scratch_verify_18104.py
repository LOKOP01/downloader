# -*- coding: utf-8 -*-
"""Verify the rebuilt 1.8.10.4 exe: no PCL module/strings, old FluentWindow shell.

Run with the project venv python.
"""
import io
import marshal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "抖音视频下载器.exe"

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

arc = CArchiveReader(str(EXE))
pyz_data = None
for name, entry in arc.toc.items():
    if name.endswith(".pyz") or name == "PYZ.pyz":
        pyz_data = arc.extract(name)
        print("PYZ entry:", name, len(pyz_data), "bytes")
        break
if pyz_data is None:
    raise SystemExit("no PYZ found in archive")

import tempfile

_tmp = Path(tempfile.gettempdir()) / "_verify_pyz_18104.pyz"
_tmp.write_bytes(pyz_data)
pyz = ZlibArchiveReader(str(_tmp))
names = list(pyz.toc.keys())
print("module count:", len(names))
print("pcl modules in package:", [n for n in names if "pcl" in n.lower()])

hits = []
for mod in names:
    if not (mod.startswith("app.") or mod == "main"):
        continue
    try:
        code = pyz.extract(mod)
    except Exception as exc:  # noqa: BLE001
        print("  extract failed:", mod, exc)
        continue
    blob = marshal.dumps(code)
    if b"pcl" in blob.lower():
        hits.append(mod)
print("app modules carrying 'pcl' string:", hits)

for mod in ("app.main_window", "app.view.home_interface", "app.view.task_interface",
            "app.view.batch_interface", "app.view.setting_interface"):
    code = pyz.extract(mod)
    blob = marshal.dumps(code)
    flags = {
        "FluentWindow": b"FluentWindow" in blob,
        "page_header": b"page_header" in blob,
        "welcomeCard": b"welcomeCard" in blob,
        "update_visual_theme": b"update_visual_theme" in blob,
        "setMicaEffectEnabled": b"setMicaEffectEnabled" in blob,
    }
    print(f"{mod}: {flags}")

raw = EXE.read_bytes()
for tag in ("1.8.10.4", "1.8.10.3"):
    print(f"version tag {tag} in exe:", tag.encode("utf-16-le") in raw)
print("exe size:", EXE.stat().st_size)
