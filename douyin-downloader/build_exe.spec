# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台窗口、收集 qfluentwidgets 资源"""
import os
from PyInstaller.utils.hooks import collect_all

_SPECDIR = os.path.dirname(os.path.abspath(SPEC))
ICON_PATH = os.path.join(_SPECDIR, 'assets', 'app.ico')
if not os.path.exists(ICON_PATH):
    ICON_PATH = None

VERSION_FILE = os.path.join(_SPECDIR, 'app_version.txt')
if not os.path.exists(VERSION_FILE):
    VERSION_FILE = None

datas, binaries, hiddenimports = collect_all('qfluentwidgets')
pw_datas, pw_binaries, pw_hiddenimports = collect_all('playwright')
datas += pw_datas
binaries += pw_binaries
hiddenimports += pw_hiddenimports

# jmcomic 在业务代码里是延迟 import，必须 collect_all 打进包。
# 只 hiddenimport 时 jmcomic 会进 PYZ，运行期 import 失败就会提示「缺少 jmcomic 组件」。
for _pkg in ('jmcomic', 'curl_cffi', 'common'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h
hiddenimports += [
    'jmcomic', 'jmcomic.api', 'jmcomic.cli', 'jmcomic.jm_option',
    'jmcomic.jm_downloader', 'jmcomic.jm_client_impl',
    'jmcomic.jm_client_interface', 'jmcomic.jm_config', 'jmcomic.jm_entity',
    'jmcomic.jm_exception', 'jmcomic.jm_toolkit', 'jmcomic.jm_plugin',
    'jmcomic.jm_task_context', 'jmcomic.jm_feature',
    'jmcomic.jm_async_client', 'jmcomic.jm_async_downloader',
    'curl_cffi', 'curl_cffi.requests', 'curl_cffi.curl', 'curl_cffi.const',
    '_cffi_backend',
    'Crypto', 'Crypto.Cipher', 'Crypto.Cipher.AES',
    'PIL', 'PIL.Image', 'yaml', 'common',
]
try:
    import curl_cffi as _curl_cffi
    _cffi_dir = os.path.dirname(_curl_cffi.__file__)
    _wrapper = os.path.join(_cffi_dir, '_wrapper.pyd')
    if os.path.exists(_wrapper):
        binaries.append((_wrapper, 'curl_cffi'))
    _libs = os.path.join(os.path.dirname(_cffi_dir), 'curl_cffi.libs')
    if os.path.isdir(_libs):
        for _name in os.listdir(_libs):
            binaries.append((os.path.join(_libs, _name), 'curl_cffi.libs'))
except Exception:
    pass

# 随包携带的图标资源（运行期通过 sys._MEIPASS/assets 读取）
for _f in ('app.ico', 'app.png'):
    _p = os.path.join(_SPECDIR, 'assets', _f)
    if os.path.exists(_p):
        datas.append((_p, 'assets'))

# 版本号文件：app/__init__.py 在运行期读它来显示版本（放包根目录，
# 对应 _read_version() 的第一个候选路径 <_MEIPASS>/app_version.txt）。
# 不打包的话 about 页会显示 v0.0.0，用户无法确认手上是哪个构建。
_VERSION_SRC = os.path.join(_SPECDIR, 'app_version.txt')
if os.path.exists(_VERSION_SRC):
    datas.append((_VERSION_SRC, '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'cv2', 'PyQt5', 'PyQt6',
              'PIL.HeifImagePlugin', 'PIL.AvifImagePlugin'],
    noarchive=False,
)

def _keep_binary(item):
    src = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else str(item)
    s = str(src).replace('\\', '/').lower()
    if 'libheif' in s or 'codex-runtimes' in s or 'codex-primary-runtime' in s:
        return False
    return True

a.binaries = [b for b in a.binaries if _keep_binary(b)]
a.datas = [d for d in a.datas if _keep_binary(d)]

# Analysis 会从 PATH 里的 Codex/poppler 命中同名 OpenSSL，随后又被上面的
# codex-runtimes 过滤掉，导致 _ssl.pyd 缺 libssl/libcrypto。
import sys
_py_dlls = os.path.join(getattr(sys, 'base_prefix', sys.prefix), 'DLLs')
_have = {str(b[0]).replace('\\', '/').lower() for b in a.binaries}
for _name in ('libssl-3-x64.dll', 'libcrypto-3-x64.dll', 'libssl-3.dll', 'libcrypto-3.dll'):
    _src = os.path.join(_py_dlls, _name)
    if os.path.isfile(_src) and _name.lower() not in _have:
        a.binaries.append((_name, _src, 'BINARY'))
        _have.add(_name.lower())

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='视频下载器',
    icon=ICON_PATH,
    version=VERSION_FILE,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
