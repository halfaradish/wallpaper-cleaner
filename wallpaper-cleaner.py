"""wallpaper-cleaner 入口

用法：
    python wallpaper-cleaner.py                 命令行清理（与旧版行为一致）
    python wallpaper-cleaner.py --dry-run       只预览将要删除的内容
    python wallpaper-cleaner.py --web           启动浏览器管理面板
    python wallpaper-cleaner.py --desktop       启动桌面窗口（需 pywebview）

打包成 exe 后双击运行等同于 --desktop；在终端里带 --dry-run / --web 运行时会自动接回控制台。
"""

import argparse
import sys

from wallpaper_cleaner import __version__, cli, core

DEFAULT_WEB_PORT = 8787


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wallpaper-cleaner.py',
        description='清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件',
    )
    parser.add_argument('--desktop', action='store_true', help='启动桌面窗口（需要 pywebview）')
    parser.add_argument('--web', action='store_true', help='启动浏览器管理面板')
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='监听地址（默认 127.0.0.1，仅本机可访问）',
    )
    parser.add_argument(
        '--port', type=int, default=None,
        help='监听端口（默认：桌面窗口由系统分配空闲端口，浏览器模式 %d）' % DEFAULT_WEB_PORT,
    )
    parser.add_argument('--no-browser', action='store_true', help='浏览器模式下不自动打开浏览器')
    parser.add_argument('--dry-run', action='store_true', help='只预览将要删除的内容，不实际删除')
    parser.add_argument('--check-deps', action='store_true',
                        help='检查桌面窗口所需的依赖是否齐全，然后退出')
    parser.add_argument('--version', action='version', version=f'wallpaper-cleaner {__version__}')
    return parser


def check_desktop_deps():
    """检查桌面窗口依赖是否齐全，返回退出码

    打包出来的 exe 如果缺 pywebview / pythonnet，只有真的去开窗口才会暴露；
    这个开关让冒烟测试能在发布前查出来，用户也能用它自查。

    输出刻意只用 ASCII：这一句要跨编码读（中文系统 cp936、CI 的 en-US 是 cp1252），
    而它跑在 setup_logger 之前，没有那份编码兜底。中文在这里会直接抛
    UnicodeEncodeError，把一个诊断开关变成崩溃源。
    """
    missing = []
    for name in ('webview', 'clr_loader', 'pythonnet'):
        try:
            __import__(name)
        except Exception as e:
            missing.append(f'{name} ({e.__class__.__name__}: {e})')

    if missing:
        print('missing desktop deps:')
        for item in missing:
            print(f'  - {item}')
        print('CLI and browser panel modes are unaffected.')
        return 1

    print('desktop deps OK: webview / clr_loader / pythonnet')
    return 0


def _wants_console_mode(args):
    """是否明确要求走命令行/浏览器路径（而不是双击打开窗口）"""
    return bool(args.dry_run or args.web)


def _run_desktop(args):
    from wallpaper_cleaner import desktop
    # 桌面模式默认让系统分配空闲端口，避免与浏览器模式或旧实例抢 8787
    return desktop.run_desktop(host=args.host, port=args.port or 0)


def main(argv=None):
    args = build_parser().parse_args(argv)

    # 打包后没有控制台，走命令行路径前先把输出接回调用它的终端
    if core.is_frozen():
        from wallpaper_cleaner import desktop
        desktop.attach_console()

    if args.check_deps:
        return check_desktop_deps()

    # 打包成 exe 后默认是窗口程序：双击即用
    if args.desktop or (core.is_frozen() and not _wants_console_mode(args)):
        core.setup_logger()
        return _run_desktop(args)

    core.setup_logger()

    if args.web:
        from wallpaper_cleaner import web
        return web.run(
            host=args.host,
            port=args.port or DEFAULT_WEB_PORT,
            open_browser=not args.no_browser,
        )

    return cli.main(dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main() or 0)
