# -*- coding: utf-8 -*-
"""抖音 / X / Instagram / B站 / 小红书 视频下载器

版本号以 `app_version.txt`（exe 版本资源）为唯一来源，这里解析出来供
设置页展示，避免「exe 显示 1.7.2、关于页却写死 v1.0」这类不一致。
"""
import os
import re

_UNKNOWN = "0.0.0"


def _read_version() -> str:
    """从 app_version.txt 解析版本号

    文件放在项目根目录（与 exe 同级，PyInstaller 也按此打包），
    这里同时兼容包内放置的情况。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), "app_version.txt"),   # 项目根
        os.path.join(here, "app_version.txt"),                    # 包内
    ]
    text = ""
    for path in candidates:
        try:
            # app_version.txt 带 UTF-8 BOM，用 utf-8-sig 读掉
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
            break
        except OSError:
            continue
    if not text:
        return _UNKNOWN
    m = re.search(r"filevers=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", text)
    if not m:
        return _UNKNOWN
    # 第 4 位是构建号：为 0 时省略（1.7.2.0 -> 1.7.2），非 0 时保留，
    # 否则 1.8.9 / 1.8.9.1 / 1.8.9.2 在界面里长得一模一样，用户没法确认
    # 手上跑的是不是修复后的构建（exe 属性里可是写全了的）。
    parts = list(m.groups())
    if parts[3] == "0":
        parts = parts[:3]
    return ".".join(parts)


__version__ = _read_version()
