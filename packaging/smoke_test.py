"""打包产物的冒烟测试：确认 exe 真的能跑起来，而不只是「构建成功」

    python packaging/smoke_test.py dist/wallpaper-cleaner.exe

做法：用 --web --no-browser 启动 exe，轮询面板接口直到拿到页面，然后结束进程。

刻意不碰 WebView2 —— 这样在没有桌面会话的 CI 上也能稳定运行，同时覆盖了打包后
最容易碎的两点：
1. bundle 里的 import 是否完整（缺 hidden import 时进程会立刻退出）
2. 前端静态资源有没有被打进去（缺了会返回 500，页面拿不到）

退出码 0 表示通过。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

STARTUP_TIMEOUT = 120      # 单文件 exe 首次启动要解压，慢一点正常
POLL_INTERVAL = 1.0


def _force_utf8_console():
    """日志里有中文，stdout/stderr 必须能编码它们。

    Windows 上输出被重定向时（比如 CI 管道），Python 会用系统 ANSI 代码页
    （英文系统是 cp1252），中文 print 会抛 UnicodeEncodeError，冒烟测试明明
    通过却以退出码 1 结束。这里统一改成 UTF-8。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def fetch(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
    except Exception:
        return None, None


def wait_for_panel(port, process, deadline):
    """等到面板开始响应，返回首页 HTML"""
    last_note = ''
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f'[smoke] 失败：exe 启动后立即退出（退出码 {process.returncode}）\n{last_note}'
            )
        status, body = fetch(f'http://127.0.0.1:{port}/')
        if status == 200 and body and 'Wallpaper Cleaner' in body:
            return body
        if status is not None:
            last_note = f'  最近一次响应：HTTP {status}'
        time.sleep(POLL_INTERVAL)
    raise SystemExit(f'[smoke] 失败：等待 {STARTUP_TIMEOUT} 秒仍未拿到面板首页\n{last_note}')


def stop(process):
    """结束被测进程

    PyInstaller 单文件模式是「引导父进程 + 解压出的子进程」两个进程，terminate() 只会
    杀掉父进程，子进程会变成孤儿继续占着端口和日志文件，所以必须按进程树整体结束。
    """
    if process.poll() is None:
        if sys.platform == 'win32':
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()


def main():
    _force_utf8_console()

    if len(sys.argv) < 2:
        print('用法: python packaging/smoke_test.py <exe 路径>', file=sys.stderr)
        return 2

    exe = os.path.abspath(sys.argv[1])
    if not os.path.exists(exe):
        print(f'[smoke] 找不到可执行文件: {exe}', file=sys.stderr)
        return 2

    port = free_port()
    # 把配置与日志写到临时目录，避免污染真实的 %APPDATA%
    home = tempfile.mkdtemp(prefix='wc-smoke-')
    env = dict(os.environ)
    env['WALLPAPER_CLEANER_HOME'] = home

    print(f'[smoke] 启动 {os.path.basename(exe)}（端口 {port}，配置目录 {home}）', flush=True)
    process = subprocess.Popen(
        [exe, '--web', '--no-browser', '--host', '127.0.0.1', '--port', str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        html = wait_for_panel(port, process, time.time() + STARTUP_TIMEOUT)
        print('[smoke] 面板首页已返回', flush=True)

        # 静态资源是打包时最容易漏的东西，单独确认一遍
        for asset, needle in (
            ('/static/style.css', '--accent'),
            ('/static/app.js', '__PANEL_TOKEN__'),
        ):
            status, body = fetch(f'http://127.0.0.1:{port}{asset}')
            if status != 200 or not body or needle not in body:
                raise SystemExit(f'[smoke] 失败：静态资源 {asset} 不可用（HTTP {status}）')
            print(f'[smoke] {asset} 正常', flush=True)

        # 接口可用性
        status, body = fetch(f'http://127.0.0.1:{port}/api/state')
        if status != 200:
            raise SystemExit(f'[smoke] 失败：/api/state 返回 HTTP {status}')
        payload = json.loads(body)
        if 'paths' not in payload or 'version' not in payload:
            raise SystemExit('[smoke] 失败：/api/state 返回结构异常')
        print(f"[smoke] /api/state 正常（版本 {payload['version']}）", flush=True)

        # 日志文件应当写在指定目录里，而不是 exe 旁边
        log_dir = os.path.join(home, 'logs')
        logs = os.listdir(log_dir) if os.path.isdir(log_dir) else []
        if not logs:
            raise SystemExit('[smoke] 失败：没有在 WALLPAPER_CLEANER_HOME 下生成日志')
        print(f'[smoke] 日志已写入 {log_dir}', flush=True)

        print('[smoke] 通过', flush=True)
        return 0
    finally:
        stop(process)


if __name__ == '__main__':
    sys.exit(main())
