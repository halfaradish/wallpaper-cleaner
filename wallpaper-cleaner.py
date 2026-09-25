"""wallpaper-cleaner 入口

用法：
    python wallpaper-cleaner.py                 命令行清理（与旧版行为一致）
    python wallpaper-cleaner.py --dry-run       只预览将要删除的内容
"""

import argparse
import sys

from wallpaper_cleaner import cli, core


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wallpaper-cleaner.py',
        description='清理 Wallpaper Engine 中已取消订阅但仍残留在磁盘上的壁纸文件',
    )
    parser.add_argument('--dry-run', action='store_true', help='只预览将要删除的内容，不实际删除')
    return parser


def main():
    args = build_parser().parse_args()
    core.setup_logger()
    return cli.main(dry_run=args.dry_run)


if __name__ == '__main__':
    sys.exit(main() or 0)
