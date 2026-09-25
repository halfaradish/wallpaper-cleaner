"""wallpaper-cleaner 入口

用法：
    python wallpaper-cleaner.py                 命令行清理（与旧版行为一致）
    python wallpaper-cleaner.py --dry-run       只预览将要删除的内容
    python wallpaper-cleaner.py --web           启动浏览器管理面板
"""

import argparse
import sys

from wallpaper_cleaner import cli, core


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wallpaper-cleaner.py',
        description='清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件',
    )
    parser.add_argument('--web', action='store_true', help='启动浏览器管理面板')
    parser.add_argument(
        '--host', default='127.0.0.1',
        help='面板监听地址（默认 127.0.0.1，仅本机可访问）',
    )
    parser.add_argument(
        '--port', type=int, default=8787,
        help='面板监听端口（默认 8787，被占用时自动向后递增）',
    )
    parser.add_argument('--no-browser', action='store_true', help='启动面板后不自动打开浏览器')
    parser.add_argument('--dry-run', action='store_true', help='只预览将要删除的内容，不实际删除')
    return parser


def main():
    args = build_parser().parse_args()
    core.setup_logger()

    if args.web:
        from wallpaper_cleaner import web
        return web.run(host=args.host, port=args.port, open_browser=not args.no_browser)

    return cli.main(dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main() or 0)
