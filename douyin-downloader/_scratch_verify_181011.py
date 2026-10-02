# -*- coding: utf-8 -*-
"""校验 1.8.10.11 打包产物：内嵌版本号 + IDM 式多连接改动是否进了包。

用项目 venv 的 python 跑：
    .venv\\Scripts\\python.exe _scratch_verify_181011.py
"""
import marshal
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "1.8.10.11"
EXE = ROOT / "dist" / VERSION / "视频下载器.exe"

if not EXE.exists():
    raise SystemExit(f"未找到产物：{EXE}")

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

arc = CArchiveReader(str(EXE))
arc_names = set(arc.toc.keys())
pyz_data = None
for name, _entry in arc.toc.items():
    if name.endswith(".pyz") or name == "PYZ.pyz":
        pyz_data = arc.extract(name)
        print("PYZ entry:", name, len(pyz_data), "bytes")
        break
if pyz_data is None:
    raise SystemExit("归档里没有 PYZ")

_tmp = Path(tempfile.gettempdir()) / "_verify_pyz_181011.pyz"
_tmp.write_bytes(pyz_data)
pyz = ZlibArchiveReader(str(_tmp))
names = list(pyz.toc.keys())
print("module count:", len(names))

fail = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        fail.append(msg)


# 1) 本次改动的符号必须真的进了包（改完源码但忘了重新打包，会在这里暴露）
print("[1] 下载引擎改动是否打进包")
downloader = marshal.dumps(pyz.extract("app.core.downloader"))
for token in (b"_fetch_track", b"_segment_connections", b"segment_connections",
              b"SEGMENT_DEFAULT_CONNECTIONS", b"base_done"):
    check(token in downloader, f"app.core.downloader 含 {token.decode()}")

setting = marshal.dumps(pyz.extract("app.view.setting_interface"))
check(b"segment_connections" in setting,
      "app.view.setting_interface 含 segment_connections")

# 2) 关键模块与随包资源都在（入口脚本 main 走 CArchive，不在 PYZ）
print("[2] 关键模块与资源完整性")
for mod in ("app.main_window", "app.core.config", "app.core.downloader",
            "app.view.setting_interface", "app.view.task_interface"):
    check(mod in names, f"{mod} 在 PYZ 内")
for entry in ("main", "app_version.txt",
              "assets\\app.ico", "assets\\app.png"):
    check(entry in arc_names, f"{entry} 在归档内")

# 3) 内嵌版本资源
print("[3] exe 内嵌版本")
raw = EXE.read_bytes()
for tag, expect in ((VERSION, True), ("1.8.10.10", False)):
    found = tag.encode("utf-16-le") in raw
    check(found is expect,
          f"版本串 {tag} {'存在' if expect else '不应存在'}（实际 {'存在' if found else '不存在'}）")

print("exe size:", EXE.stat().st_size, f"({EXE.stat().st_size / 1024 / 1024:.1f} MB)")

if fail:
    print(f"\n结果：{len(fail)} 项未通过")
    sys.exit(1)
print("\n结果：全部通过")
