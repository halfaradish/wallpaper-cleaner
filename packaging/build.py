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


def venv_python():
    if sys.platform == 'win32':
        return os.path.join(VENV_DIR, 'Scripts', 'python.exe')
    return os.path.join(VENV_DIR, 'bin', 'python')


def run(cmd, **kwargs):
    printable = ' '.join(cmd)
    print(f'[build] $ {printable}', flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def ensure_deps():
    """准备虚拟环境与打包依赖"""
    python = venv_python()
    if not os.path.exists(python):
        print(f'[build] 创建虚拟环境: {VENV_DIR}', flush=True)
        run([sys.executable, '-m', 'venv', VENV_DIR])
    else:
        print(f'[build] 复用已有虚拟环境: {VENV_DIR}', flush=True)

    run([python, '-m', 'pip', 'install', '--upgrade', 'pip', '--quiet'])
    run([python, '-m', 'pip', 'install', '--quiet', 'pyinstaller', '-r', REQUIREMENTS])
    return python


def main():
    _force_utf8_console()

    parser = argparse.ArgumentParser(description='构建 wallpaper-cleaner.exe')
    parser.add_argument('--skip-deps', action='store_true', help='跳过虚拟环境准备')
    parser.add_argument('--skip-tests', action='store_true', help='跳过单元测试')
    parser.add_argument('--skip-smoke', action='store_true', help='构建后不跑冒烟测试')
    args = parser.parse_args()

    python = sys.executable if args.skip_deps else ensure_deps()

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
