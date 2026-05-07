import json
import os
import sys
import shutil
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# 路径设置
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.json')

# 日志目录（与本脚本同目录下的 logs 文件夹）
log_dir = os.path.join(script_dir, 'logs')
os.makedirs(log_dir, exist_ok=True)

# 日志文件命名：wallpaper-cleaner_YYYYMMDD.log
log_filename = f'wallpaper-cleaner_{datetime.now().strftime("%Y%m%d")}.log'
log_path = os.path.join(log_dir, log_filename)

logger = logging.getLogger('WallpaperCleaner')
logger.setLevel(logging.DEBUG)

# 控制台输出
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(console_fmt)
logger.addHandler(console_handler)

# 文件输出（带轮转：单文件最大 5MB，保留 5 个备份）
file_handler = RotatingFileHandler(
    log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_fmt)
logger.addHandler(file_handler)


def resolve_path(raw_path):
    """将配置中的路径解析为绝对路径：绝对路径直接返回，相对路径基于脚本目录解析"""
    if not raw_path:
        return ''
    if os.path.isabs(raw_path):
        return os.path.normpath(raw_path)
    return os.path.normpath(os.path.join(script_dir, raw_path))


def load_config():
    """加载配置文件，不存在时自动生成默认配置，失败时打印错误并退出"""
    if not os.path.exists(config_path):
        logger.warning('未检测到配置文件，正在自动生成默认配置文件...')
        default_config = {
            "_comment": "请配置 json_path 和 workshop_dir 的路径，支持绝对路径和相对路径（相对路径基于本脚本所在目录）",
            "json_path": "",
            "workshop_dir": ""
        }
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=4)
            logger.info(f'默认配置文件已生成: {config_path}')
        except Exception as e:
            logger.error(f'自动生成配置文件失败: {e}')
            sys.exit(1)

        logger.info('')
        logger.info('=' * 60)
        logger.info('  请先编辑配置文件，填入正确的路径信息：')
        logger.info(f'  配置文件位置: {config_path}')
        logger.info('  编辑完成后，重新运行本程序即可。')
        logger.info('=' * 60)
        sys.exit(0)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f'配置文件解析失败 ({config_path}): {e}')
        logger.error('请检查配置文件是否为合法的 JSON 格式，或删除该文件后重新运行以生成默认配置。')
        sys.exit(1)
    except Exception as e:
        logger.error(f'读取配置文件失败 ({config_path}): {e}')
        sys.exit(1)

    json_path = resolve_path(config.get('json_path', ''))
    workshop_dir = resolve_path(config.get('workshop_dir', ''))

    if not json_path:
        logger.error(f'配置项 "json_path" 未设置或为空，请编辑配置文件: {config_path}')
        sys.exit(1)
    if not workshop_dir:
        logger.error(f'配置项 "workshop_dir" 未设置或为空，请编辑配置文件: {config_path}')
        sys.exit(1)

    logger.info(f'配置加载成功: {config_path}')
    return json_path, workshop_dir


def get_dir_size(path):
    """递归计算目录总大小（字节）"""
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def format_size(size_bytes):
    """将字节数转为人类可读的格式"""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size_bytes < 1024:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.2f} PB'


def main():
    logger.info('========== wallpaper-cleaner 开始执行 ==========')

    json_path, workshop_dir = load_config()

    # 读取json，获取所有已订阅的workshopid及其信息
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f'已读取订阅缓存: {json_path}')
    except Exception as e:
        logger.error(f'读取JSON文件失败: {e}')
        return

    workshop_info = {}
    wallpapers = data.get('wallpapers', [])
    for wp in wallpapers:
        wid = str(wp.get('workshopid', ''))
        title = wp.get('title', '未知')
        size = wp.get('filesizelabel', '未知')
        if wid:
            workshop_info[wid] = {'title': title, 'size': size}

    workshop_ids = set(workshop_info.keys())
    logger.info(f'已订阅壁纸数量: {len(workshop_ids)}')

    # 遍历目标文件夹
    if not os.path.isdir(workshop_dir):
        logger.error(f'workshop目录不存在: {workshop_dir}')
        return

    all_folders = os.listdir(workshop_dir)
    total_folders = sum(1 for f in all_folders if os.path.isdir(os.path.join(workshop_dir, f)))
    logger.info(f'workshop目录下文件夹总数: {total_folders}')

    deleted_count = 0
    retained_count = 0
    total_freed_bytes = 0

    for folder in all_folders:
        folder_path = os.path.join(workshop_dir, folder)
        if not os.path.isdir(folder_path):
            continue

        if folder not in workshop_ids:
            dir_size = get_dir_size(folder_path)
            try:
                shutil.rmtree(folder_path)
                total_freed_bytes += dir_size
                deleted_count += 1
                logger.info(f'已删除: {folder} ({format_size(dir_size)})')
            except Exception as e:
                logger.error(f'删除失败 {folder}: {e}')
        else:
            info = workshop_info.get(folder, {})
            dir_size = get_dir_size(folder_path)
            retained_count += 1
            logger.debug(
                f'保留: {folder} | 标题: {info.get("title", "未知")} | '
                f'标注大小: {info.get("size", "未知")} | '
                f'实际大小: {format_size(dir_size)}'
            )

    logger.info('========== 执行完成 ==========')
    logger.info(f'扫描文件夹总数: {total_folders}')
    logger.info(f'保留文件夹数量: {retained_count}')
    logger.info(f'删除文件夹数量: {deleted_count}')
    logger.info(f'释放存储空间: {format_size(total_freed_bytes)}')


if __name__ == '__main__':
    main()
