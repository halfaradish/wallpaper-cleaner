"""一键本地构建 exe

    python packaging/build.py

会做三件事：
1. 准备好 .venv-build 虚拟环境并安装 PyInstaller 与 requirements-desktop.txt
   （已存在就复用，不重复安装；不污染全局 Python）
2. 用 packaging/wallpaper-cleaner.spec 打包
3. 打印产物路径，并可选跑一次冒烟测试

参数：
    --skip-deps    跳过虚拟环境准备（依赖已经装好时用，CI 里走这条路）
    --skip-tests   跳过单元测试
    --skip-smoke   构建完不跑冒烟测试
"""

import argparse
import locale
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_DIR = os.path.join(PROJECT_ROOT, '.venv-build')
SPEC = os.path.join(PROJECT_ROOT, 'packaging', 'wallpaper-cleaner.spec')
SMOKE = os.path.join(PROJECT_ROOT, 'packaging', 'smoke_test.py')
REQUIREMENTS = os.path.join(PROJECT_ROOT, 'requirements-desktop.txt')
EXE_NAME = 'wallpaper-cleaner.exe'


def _force_utf8_console():
    """日志里有中文，stdout/stderr 必须能编码它们。

    Windows 上输出被重定向时（比如 CI 管道、重定向到文件），
    Python 会用系统 ANSI 代码页（英文系统是 cp1252），中文 print 直接抛
    UnicodeEncodeError，构建明明成功却以退出码 1 结束。这里统一改成 UTF-8。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def _decode_output(raw):
    """解子进程的输出

    Windows 上原生进程和普通 CPython 都按系统 ANSI 代码页输出（中文系统是 cp936），
    不是 UTF-8 —— 先按 UTF-8 解会抛 UnicodeDecodeError，而 cp936 的字节有时恰好
    也能被 UTF-8 解出来（变成乱码）。所以先试本地代码页，再退回 UTF-8。

    反正这个函数只用于打印诊断信息，解错了也不影响判断，判断只看退出码。
    """
    if not raw:
        return ''
    for encoding in (locale.getpreferredencoding(False), 'utf-8'):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode('utf-8', 'replace')


def venv_python():
    if sys.platform == 'win32':
        return os.path.join(VENV_DIR, 'Scripts', 'python.exe')
    return os.path.join(VENV_DIR, 'bin', 'python')


def run(cmd, **kwargs):
    printable = ' '.join(cmd)
    print(f'[build] $ {printable}', flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def ensure_venv():
    """准备好虚拟环境并装齐依赖，返回该环境的 Python 路径"""
    python = venv_python()
    if not os.path.exists(python):
        print(f'[build] 创建虚拟环境: {VENV_DIR}', flush=True)
        run([sys.executable, '-m', 'venv', VENV_DIR])
    else:
        print(f'[build] 复用已有虚拟环境: {VENV_DIR}', flush=True)

    run([python, '-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'])
    run([python, '-m', 'pip', 'install', '--quiet', 'pyinstaller', '-r', REQUIREMENTS])
    return python


def pick_python(skip_deps):
    """选打包用的解释器

    只要 .venv-build 在就优先用它 —— --skip-deps 的意思是「跳过安装依赖」（CI 里
    已经装好了），不是「别用虚拟环境」。这里以前直接退回 sys.executable，于是
    `python packaging/build.py --skip-deps` 会拿全局 Python 打包，而全局环境通常
    没有 pythonnet，打出来的 exe 桌面窗口打不开，还看不出来。
    """
    venv = venv_python()
    if os.path.exists(venv):
        if skip_deps:
            print(f'[build] 使用已有虚拟环境: {VENV_DIR}', flush=True)
        else:
            ensure_venv()
        return venv

    if skip_deps:
        print('[build] 没有 .venv-build，将使用当前解释器', flush=True)
        return sys.executable

    return ensure_venv()


def require_desktop_deps(python):
    """打包前确认依赖确实装在这个解释器里

    缺 pywebview / pythonnet 时 PyInstaller 只会打一行 ERROR 就继续，照样产出一个
    「浏览器面板能用、桌面窗口打不开」的 exe。这里提前拦住，别等用户双击才发现。
    """
    probe = [python, '-c', 'import webview, clr_loader, pythonnet']
    result = subprocess.run(probe, capture_output=True)
    if result.returncode == 0:
        return True

    lines = _decode_output(result.stderr).strip().splitlines()
    print(f'[build] 打包依赖不全: {python}', file=sys.stderr)
    if lines:
        print(f'[build] {lines[-1]}', file=sys.stderr)
    print('[build] 删掉 .venv-build 后重新运行，或直接执行: '
          r'.venv-build\Scripts\python.exe packaging/build.py', file=sys.stderr)
    return False


def main():
    _force_utf8_console()

    parser = argparse.ArgumentParser(description='构建 wallpaper-cleaner.exe')
    parser.add_argument('--skip-deps', action='store_true',
                        help='跳过依赖安装（仍优先用 .venv-build）')
    parser.add_argument('--skip-tests', action='store_true', help='跳过单元测试')
    parser.add_argument('--skip-smoke', action='store_true', help='构建后不跑冒烟测试')
    args = parser.parse_args()

    python = pick_python(args.skip_deps)
    if not require_desktop_deps(python):
        return 1

    if not args.skip_tests:
        run([python, '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.'], cwd=PROJECT_ROOT)

    run([
        python, '-m', 'PyInstaller',
        '--noconfirm', '--clean',
        '--distpath', os.path.join(PROJECT_ROOT, 'dist'),
        '--workpath', os.path.join(PROJECT_ROOT, 'build'),
        SPEC,
    ], cwd=PROJECT_ROOT)

    exe_path = os.path.join(PROJECT_ROOT, 'dist', EXE_NAME)
    if not os.path.exists(exe_path):
        print(f'[build] 构建失败：没有找到 {exe_path}', file=sys.stderr)
        return 1

    size_mb = os.path.getsize(exe_path) / 1024 / 1024
    print(f'[build] 产物: {exe_path} ({size_mb:.1f} MB)', flush=True)

    if not args.skip_smoke:
        # 冒烟测试用系统 Python 即可，它只发 HTTP 请求
        run([sys.executable, SMOKE, exe_path], cwd=PROJECT_ROOT)

    return 0


if __name__ == '__main__':
    sys.exit(main())
