"""定位运行签名算法所需的 Node.js（node.exe）。

Spider_XHS 的 X-s/X-t/X-S-Common、b1、x-rap-param 都由 ``node`` 子进程运行
``js/*.js`` 生成。本模块统一解析 node 可执行文件路径，优先打包自带，其次系统 PATH。
"""

from __future__ import annotations

import os
import shutil
import sys


def get_node_cmd() -> str:
    """返回可执行的 node 路径（字符串），找不到时抛 RuntimeError。

    解析顺序：
      1. 打包运行时：exe 同目录 node.exe，其次 onefile 解压目录里的
         Playwright ``playwright/driver/node.exe``；
      2. 开发期，向上找项目根 / 仓库根的 ``node.exe``；
      3. 系统 PATH 上的 ``node``。
    """
    # 1) 打包：优先 sidecar，其次 Playwright 自带的 driver/node.exe
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        node = os.path.join(exe_dir, 'node.exe')
        if os.path.isfile(node):
            return node
        meipass = getattr(sys, '_MEIPASS', '')
        if meipass:
            for rel in (
                'node.exe',
                os.path.join('playwright', 'driver', 'node.exe'),
            ):
                node = os.path.join(meipass, rel)
                if os.path.isfile(node):
                    return node

    # 2) 开发期：向上找 node.exe
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        node = os.path.join(up, 'node.exe')
        if os.path.isfile(node):
            return node

    # 3) 系统 PATH
    node = shutil.which('node')
    if node:
        return node

    raise RuntimeError(
        '未找到 Node.js：请安装 Node.js，或把 node.exe 放到程序同目录'
    )


__all__ = ['get_node_cmd']
