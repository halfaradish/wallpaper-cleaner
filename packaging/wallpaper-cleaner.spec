# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件、无控制台的 wallpaper-cleaner.exe

构建：
    python packaging/build.py

或手动：
    .venv-build\\Scripts\\pyinstaller --noconfirm --clean packaging/wallpaper-cleaner.spec

几个关键选择：
- console=False  双击不弹黑框；命令行输出由 desktop.attach_console() 接回调用者的终端
- upx=False      压缩能减小体积，但会明显提高杀软误报率，不值得
- 静态资源打进 wallpaper_cleaner/static，与源码运行的相对位置保持一致
"""

import os
import re
import sys

from PyInstaller.utils.hooks import collect_all

sys.path.insert(0, SPECPATH)
import make_icon

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
PACKAGE_DIR = os.path.join(PROJECT_ROOT, 'wallpaper_cleaner')

# ---------------------------------------------------------------- 版本号

def read_version():
    """从 wallpaper_cleaner/__init__.py 读取版本号，作为 exe 属性与文件名的唯一来源"""
    path = os.path.join(PACKAGE_DIR, '__init__.py')
    with open(path, 'r', encoding='utf-8') as f:
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read(), re.M)
    if not match:
        raise SystemExit('无法从 wallpaper_cleaner/__init__.py 读取 __version__')
    return match.group(1)


VERSION = read_version()
VERSION_PARTS = [int(part) for part in re.findall(r'\d+', VERSION)[:4]]
while len(VERSION_PARTS) < 4:
    VERSION_PARTS.append(0)


def write_version_file():
    """生成 VSVersionInfo 资源文件，让 exe 属性里能看到产品名与版本"""
    path = os.path.join(SPECPATH, 'version_info.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple(VERSION_PARTS)},
    prodvers={tuple(VERSION_PARTS)},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'wallpaper-cleaner'),
        StringStruct('FileDescription', '清理 Wallpaper Engine 中已取消订阅的壁纸残留'),
        StringStruct('FileVersion', '{VERSION}'),
        StringStruct('InternalName', 'wallpaper-cleaner'),
        StringStruct('OriginalFilename', 'wallpaper-cleaner.exe'),
        StringStruct('ProductName', 'Wallpaper Cleaner'),
        StringStruct('ProductVersion', '{VERSION}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
""")
    return path


def write_icon_file():
    """生成 exe 图标

    和版本信息一样在构建时生成而不是往仓库里塞二进制：改造型或调色只改
    packaging/make_icon.py，不会出现脚本与图标文件不同步的情况。
    """
    return make_icon.build_icon(PROJECT_ROOT)


# ---------------------------------------------------------------- 收集依赖

datas = [
    # 面板前端资源；目标路径与源码内的相对位置一致，web.py 才能按 __file__ 找到
    (os.path.join(PACKAGE_DIR, 'static'), os.path.join('wallpaper_cleaner', 'static')),
]
binaries = []
hiddenimports = []

# pywebview 按平台动态导入后端，并带有一批 JS 资源，必须整包收集
for package in ('webview',):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Windows 下 pywebview 走 pythonnet 调 WinForms + WebView2，这两个是动态导入的
hiddenimports += ['clr_loader', 'pythonnet']

# ---------------------------------------------------------------- 分析

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'wallpaper-cleaner.py')],
    pathex=[PROJECT_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 用不到的后端与开发期依赖，剔掉可以减小体积。
        # 注意不要排除 distutils / setuptools / pip —— PyInstaller 的钩子会去别名
        # distutils，排掉它们会和钩子冲突并直接构建失败。
        'tkinter', 'unittest', 'pydoc', 'doctest',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'cefpython3',
        'numpy', 'PIL', 'matplotlib', 'pytest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='wallpaper-cleaner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=write_version_file(),
    icon=write_icon_file(),
)
