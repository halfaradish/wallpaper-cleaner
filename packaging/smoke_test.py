"""打包产物的冒烟测试：确认发布出去的 zip 真的能用，而不只是「构建成功」

    python packaging/smoke_test.py dist/wallpaper-cleaner-0.5.0.zip
    python packaging/smoke_test.py dist/wallpaper-cleaner.exe        # 也接受裸 exe

做法：解压 zip，把 exe 复制到一个干净的临时目录，用 --web --no-browser 启动它，
轮询面板接口直到拿到页面，然后结束进程。

刻意不碰 WebView2 —— 这样在没有桌面会话的 CI 上也能稳定运行，同时覆盖了打包后
最容易碎的几点：
1. bundle 里的 import 是否完整（缺 hidden import 时进程会立刻退出）
2. 前端静态资源有没有被打进去（缺了会返回 500，页面拿不到）
3. 配置、日志、界面偏好是否落在 exe 旁边（这是发布形态的一部分，
   在源码运行下测不出来）

刻意不设 WALLPAPER_CLEANER_HOME：那个环境变量会把落盘位置强行改到别处，
于是"配置落在 exe 旁边"这条最需要验证的默认行为刚好被屏蔽掉。
把 exe 复制到临时目录里跑，临时目录本身就是沙箱，不需要额外的隔离手段。

退出码 0 表示通过。
"""

import json
import locale
import os
import posixpath
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

STARTUP_TIMEOUT = 120      # 单文件 exe 首次启动要解压，慢一点正常
POLL_INTERVAL = 1.0
EXE_NAME = 'wallpaper-cleaner.exe'
ZIP_ENTRY = 'wallpaper-cleaner/' + EXE_NAME   # 发布 zip 里的唯一条目


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


def _decode_output(raw):
    """解子进程的输出

    这是被测的 exe 的输出：实测它按系统 ANSI 代码页输出（中文系统 cp936），
    而且不受 PYTHONIOENCODING 影响，所以先按本地代码页解。

    check-deps 的输出本身是纯 ASCII，怎么解都对；这里主要是兼容它以后可能的中文输出。
    """
    if not raw:
        return ''
    for encoding in (locale.getpreferredencoding(False), 'utf-8'):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode('utf-8', 'replace')


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


def check_assets(port, html):
    """把首页引用到的静态资源、以及它们 import 到的模块全部取一遍

    返回检查过的资源个数。任何一个取不到就抛 SystemExit。
    PyInstaller 的 datas 是整目录拷贝，理论上不会漏；但这条检查的价值在于：
    只要漏了，这里是"打包后跑一遍"的最后一道闸，而不是等用户打开面板才发现某块功能没了。
    """
    pattern = re.compile(r"""(?:href|src)="(/static/[^"]+)"|from\s+['"](\.[^'"]+)['"]""")
    pending = [ref for ref in re.findall(r'(?:href|src)="(/static/[^"]+)"', html)]
    seen = set()

    while pending:
        asset = pending.pop()
        if asset in seen:
            continue
        seen.add(asset)
        status, body = fetch(f'http://127.0.0.1:{port}{asset}')
        if status != 200 or not body:
            raise SystemExit(f'[smoke] 失败：静态资源 {asset} 不可用（HTTP {status}）')
        # 顺着相对 import 继续走：模块图里任何一环缺失都会在这里暴露
        for _static_ref, relative in pattern.findall(body):
            if not relative:
                continue
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(asset), relative)
            )
            pending.append(resolved)

    if len(seen) < 5:
        raise SystemExit(f'[smoke] 失败：只找到 {len(seen)} 个静态资源，模块图似乎没走通')
    return len(seen)


def check_prefs(port, home, html):
    """界面偏好：读默认值 → 写一个值 → 确认落在 exe 旁边 → 确认首页第一帧就带着它

    首页那一帧很关键：主题如果等页面加载完再问一次接口，用户选了深色而系统是浅色时
    会先闪一下浅色。所以它必须像 token 一样由服务端注入。
    """
    token = re.search(r'name="panel-token" content="([^"]+)"', html)
    if not token:
        raise SystemExit('[smoke] 失败：首页里取不到面板 token')

    status, body = fetch(f'http://127.0.0.1:{port}/api/prefs')
    if status != 200 or '"theme"' not in (body or ''):
        raise SystemExit(f'[smoke] 失败：/api/prefs 返回 HTTP {status}')

    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api/prefs',
        data=json.dumps({'theme': 'dark'}).encode('utf-8'),
        method='POST',
        headers={'Content-Type': 'application/json', 'X-Panel-Token': token.group(1)},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as res:
            if res.status != 200:
                raise SystemExit(f'[smoke] 失败：写入界面偏好返回 HTTP {res.status}')
    except urllib.error.HTTPError as e:
        raise SystemExit(f'[smoke] 失败：写入界面偏好返回 HTTP {e.code}: {e.read()[:120]}')

    prefs_file = os.path.join(home, 'prefs.json')
    if not os.path.exists(prefs_file):
        raise SystemExit(f'[smoke] 失败：界面偏好没有写到 {prefs_file}')
    with open(prefs_file, 'r', encoding='utf-8') as f:
        if json.load(f).get('theme') != 'dark':
            raise SystemExit('[smoke] 失败：界面偏好的内容不对')

    _status, page = fetch(f'http://127.0.0.1:{port}/')
    if 'data-theme="dark"' not in (page or ''):
        raise SystemExit('[smoke] 失败：存下来的主题没有注入首页第一帧')


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


def unpack_artifact(path):
    """把待测产物准备成一个可执行的 exe，返回 (exe 路径, 运行目录)

    给 zip 就解压：顺便断言"zip 里只有 wallpaper-cleaner/ 下那一个 exe"——
    那是发布形态的一部分，多塞了文件用户就会多一份不知道能不能删的东西。
    给 exe 就直接用。
    返回的运行目录是 exe 所在的文件夹（zip 情况下是解压出的 wallpaper-cleaner/
    子目录，不是临时目录根），这样"配置落在 exe 旁边"就落在临时目录里，
    不会污染 dist/ 或仓库。
    """
    work = tempfile.mkdtemp(prefix='wc-smoke-')
    if path.lower().endswith('.zip'):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if names != [ZIP_ENTRY]:
                raise SystemExit(
                    f'[smoke] 失败：zip 里应当只有 {ZIP_ENTRY} 一个条目，实际是 {names}'
                )
            zf.extractall(work)
        print(f'[smoke] 已解压 {os.path.basename(path)}', flush=True)
        exe = os.path.join(work, *ZIP_ENTRY.split('/'))
    else:
        exe = os.path.join(work, os.path.basename(path))
        shutil.copy2(path, exe)
    if not os.path.exists(exe):
        raise SystemExit(f'[smoke] 失败：产物里没有找到可执行文件（{work}）')
    return exe, os.path.dirname(exe)


def main():
    _force_utf8_console()

    if len(sys.argv) < 2:
        print('用法: python packaging/smoke_test.py <zip 或 exe 路径>', file=sys.stderr)
        return 2

    artifact = os.path.abspath(sys.argv[1])
    if not os.path.exists(artifact):
        print(f'[smoke] 找不到产物: {artifact}', file=sys.stderr)
        return 2

    exe, home = unpack_artifact(artifact)

    # 先查依赖：缺 pywebview / pythonnet 时 PyInstaller 只打一行 ERROR 就继续，
    # 产出的 exe 浏览器面板能用、桌面窗口打不开，不看这一步发现不了
    print('[smoke] 检查桌面窗口依赖', flush=True)
    probe = subprocess.run([exe, '--check-deps'], capture_output=True)
    print(_decode_output(probe.stdout).strip(), flush=True)
    if probe.returncode != 0:
        print(_decode_output(probe.stderr).strip(), file=sys.stderr)
        raise SystemExit(f'[smoke] 失败：桌面窗口依赖不齐全（退出码 {probe.returncode}）')

    port = free_port()
    # 刻意不设 WALLPAPER_CLEANER_HOME：要测的就是"配置落在 exe 旁边"这条默认行为，
    # 而那个变量会把它改到别处，等于把最该验的东西屏蔽掉。exe 已经在一个临时目录里，
    # 所以不需要额外的隔离。
    env = dict(os.environ)
    env.pop('WALLPAPER_CLEANER_HOME', None)

    print(f'[smoke] 启动 {os.path.basename(exe)}（端口 {port}，运行目录 {home}）', flush=True)
    process = subprocess.Popen(
        [exe, '--web', '--no-browser', '--host', '127.0.0.1', '--port', str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        html = wait_for_panel(port, process, time.time() + STARTUP_TIMEOUT)
        print('[smoke] 面板首页已返回', flush=True)

        # 首页本身：token 占位符必须被替换掉。没换掉的话面板拿不到 token，
        # 页面看着正常，但所有写操作都会 403——只看"首页返回 200"发现不了。
        if '__PANEL_TOKEN_VALUE__' in html or 'name="panel-token"' not in html:
            raise SystemExit('[smoke] 失败：面板首页的 token 占位符没有被替换')
        if '__PANEL_THEME_VALUE__' in html or 'data-theme=' not in html:
            raise SystemExit('[smoke] 失败：面板首页的主题占位符没有被替换')
        print('[smoke] 首页 token 与主题注入正常', flush=True)

        # 界面偏好要能真的落在 exe 旁边：写不进去的话主题每次启动都会被忘掉，
        # 而这一点在源码运行下测不出来
        check_prefs(port, home, html)
        print('[smoke] 界面偏好可读写', flush=True)

        # 静态资源是打包时最容易漏的东西。前端拆成多个模块后，漏一个文件不会让页面
        # 打不开，只会让某块功能静默失效——所以这里顺着 import 把整张模块图走一遍，
        # 而不只是确认首页引用的那几个文件在。
        count = check_assets(port, html)
        print(f'[smoke] 静态资源全部可用（{count} 个）', flush=True)

        # 接口可用性
        status, body = fetch(f'http://127.0.0.1:{port}/api/state')
        if status != 200:
            raise SystemExit(f'[smoke] 失败：/api/state 返回 HTTP {status}')
        payload = json.loads(body)
        if 'paths' not in payload or 'version' not in payload:
            raise SystemExit('[smoke] 失败：/api/state 返回结构异常')
        if payload.get('home_fallback'):
            raise SystemExit(
                f'[smoke] 失败：配置目录退回了 %APPDATA%（{home} 应当是可写的）'
            )
        print(f"[smoke] /api/state 正常（版本 {payload['version']}）", flush=True)

        # 落盘位置是发布形态的一部分：需要写的东西都必须就在 exe 旁边，
        # 这样用户挪走或删掉这个文件夹就等于搬家或卸载。
        # 这里查 prefs.json 与 logs/：面板启动就会写它们（config.yml 只由
        # 命令行首次运行生成，面板路径不碰它，所以不能拿来当断言）。
        log_dir = os.path.join(home, 'logs')
        logs = os.listdir(log_dir) if os.path.isdir(log_dir) else []
        if not logs:
            raise SystemExit(f'[smoke] 失败：没有在 exe 旁边生成日志（{log_dir}）')
        if not os.path.exists(os.path.join(home, 'prefs.json')):
            raise SystemExit(f'[smoke] 失败：界面偏好没有生成在 exe 旁边（{home}）')
        print(f'[smoke] 日志与界面偏好都落在 exe 旁边（{home}）', flush=True)

        print('[smoke] 通过', flush=True)
        return 0
    finally:
        stop(process)


if __name__ == '__main__':
    sys.exit(main())
