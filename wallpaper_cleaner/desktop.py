"""桌面窗口模式：用 WebView2 承载现有面板，双击 exe 即可使用

与浏览器模式共用同一套 HTTP 服务与页面（见 web.create_server），
区别只是把页面装进一个原生窗口里 —— 用户看到的是一个普通 Windows 应用，
而不是一个浏览器标签页。
"""

import ctypes
import sys
import threading

from . import core, web

WINDOW_TITLE = 'Wallpaper Cleaner 壁纸清理'
MUTEX_NAME = 'Local\\wallpaper-cleaner-desktop'

ERROR_ALREADY_EXISTS = 183
ATTACH_PARENT_PROCESS = -1
SW_RESTORE = 9
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30

WEBVIEW2_HINT = (
    '未能启动内嵌浏览器窗口。\n\n'
    '通常是因为缺少 Microsoft Edge WebView2 运行时，可以从微软官网免费安装：\n'
    'https://developer.microsoft.com/microsoft-edge/webview2/\n\n'
    '安装后重新打开即可。也可以改用浏览器模式：'
)


def message_box(text, title=WINDOW_TITLE, flags=MB_ICONINFORMATION):
    """桌面程序没有控制台可以报错，用系统弹窗兜底"""
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        pass


def attach_console():
    """把输出接回父进程的控制台

    打包成 --noconsole 的 exe 后 sys.stdout / sys.stderr 是 None，
    在终端里执行时（例如 wallpaper-cleaner.exe --dry-run）需要手动挂到
    调用者的控制台上，否则用户什么也看不到。
    """
    if sys.platform != 'win32':
        return False
    if core.has_console():
        return True

    try:
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return False
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8', errors='replace', buffering=1)
        sys.stderr = open('CONOUT$', 'w', encoding='utf-8', errors='replace', buffering=1)
        sys.stdin = open('CONIN$', 'r', encoding='utf-8', errors='replace')
    except (OSError, AttributeError):
        return False
    return True


class SingleInstance:
    """基于 Windows 命名互斥体的单实例守卫

    没有它的话双击两次会起两个面板：第二个进程会绑到递增的端口上，
    于是同时存在两份状态，用户看到两个窗口。
    """

    def __init__(self, name=MUTEX_NAME):
        self.name = name
        self.handle = None

    def acquire(self):
        """拿到实例锁返回 True；已有实例在运行返回 False"""
        if sys.platform != 'win32':
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            self.handle = kernel32.CreateMutexW(None, False, self.name)
            if not self.handle:
                return True  # 拿不到互斥体时不拦住用户
            return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
        except Exception:
            return True

    def release(self):
        if self.handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


def focus_existing_window(prefix=WINDOW_TITLE):
    """把已在运行的窗口提到前台，成功返回 True

    pywebview 会在标题里附加页面名，所以按前缀匹配而不是全等。
    """
    if sys.platform != 'win32':
        return False

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value.startswith(prefix):
                found.append(hwnd)
                return False
        return True

    try:
        user32.EnumWindows(visit, None)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _build_window(webview, state, url, window_title):
    """创建窗口并挂上关闭拦截"""
    window = webview.create_window(
        window_title,
        url,
        width=1120,
        height=840,
        min_size=(780, 560),
        text_select=True,
    )

    def on_closing():
        # 清理任务进行中不允许关窗：进程退出会让删除停在半路，留下半残目录
        with state.lock:
            active = state.active_job
        if not active:
            return True
        message_box(
            '正在执行清理，请等待完成后再关闭窗口。\n\n'
            '关闭窗口会中断删除，可能留下不完整的壁纸目录。',
            window_title,
            MB_ICONWARNING,
        )
        return False

    window.events.closing += on_closing
    return window


def _run_window(host, port, window_title, logger):
    try:
        import webview
    except ImportError:
        logger.error('桌面窗口模式需要 pywebview，请先安装: pip install -r requirements-desktop.txt')
        message_box(
            '缺少 pywebview，无法打开窗口。\n\n'
            '请改用浏览器模式：wallpaper-cleaner.exe --web\n'
            '或安装依赖后重试：pip install pywebview',
            window_title,
        )
        return 1

    try:
        server, state = web.create_server(host, port)
    except OSError as e:
        logger.error(str(e))
        message_box(f'无法启动本地服务：{e}', window_title, MB_ICONWARNING)
        return 1

    url = web.server_url(server)
    logger.info('桌面窗口已启动: %s', url)

    # HTTP 服务跑在后台线程，主线程留给 pywebview 的 GUI 事件循环
    threading.Thread(
        target=server.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True
    ).start()

    try:
        _build_window(webview, state, url, window_title)
        webview.start()
    except Exception as e:
        logger.exception('内嵌浏览器启动失败')
        message_box(f'{WEBVIEW2_HINT}wallpaper-cleaner.exe --web\n\n错误信息：{e}', window_title)
        return 1
    finally:
        server.shutdown()
        server.server_close()
        logger.info('桌面窗口已关闭')

    return 0


def run_desktop(host='127.0.0.1', port=0, window_title=WINDOW_TITLE):
    """启动桌面窗口

    port 默认为 0，由系统分配空闲端口 —— 桌面模式没必要和固定端口较劲。
    """
    logger = core.setup_logger()
    logger.info('========== wallpaper-cleaner 桌面模式 ==========')
    logger.info('配置目录: %s', core.script_dir)

    guard = SingleInstance()
    if not guard.acquire():
        logger.info('检测到已有实例在运行，转为唤起已有窗口')
        if not focus_existing_window(window_title):
            message_box('Wallpaper Cleaner 已经在运行了。', window_title)
        return 0

    try:
        return _run_window(host, port, window_title, logger)
    finally:
        guard.release()


def main():
    """供开发时直接运行本模块调试"""
    return run_desktop()


if __name__ == '__main__':
    sys.exit(main() or 0)
