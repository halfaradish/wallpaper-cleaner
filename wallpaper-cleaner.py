import json
import os
import re
import sys
import shutil
import stat
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# 路径设置
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, 'config.yml')
legacy_config_path = os.path.join(script_dir, 'config.json')

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


def get_steam_libraries():
    """尝试通过注册表和 libraryfolders.vdf 获取所有 Steam 库文件夹路径"""
    steam_path = None
    libraries = []

    # 1. 尝试 Windows 注册表
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam')
        steam_path = winreg.QueryValueEx(key, 'SteamPath')[0]
        winreg.CloseKey(key)
    except Exception:
        pass

    # 2. 注册表失败则尝试常见安装路径
    if not steam_path:
        for p in [
            r'C:\Program Files (x86)\Steam',
            r'D:\Steam',
            r'E:\Steam',
            r'F:\Steam',
        ]:
            if os.path.exists(os.path.join(p, 'steam.exe')):
                steam_path = p
                break

    if not steam_path or not os.path.exists(steam_path):
        return libraries

    # 注册表返回的路径是正斜杠形式（如 d:/games/steam），统一规范化
    steam_path = os.path.normpath(steam_path)
    libraries.append(steam_path)
    # VDF 与注册表可能以不同大小写/斜杠指向同一库，用 normcase 去重
    seen = {os.path.normcase(steam_path)}

    # 3. 解析 libraryfolders.vdf 获取额外的库文件夹
    vdf_path = os.path.join(steam_path, 'steamapps', 'libraryfolders.vdf')
    if not os.path.exists(vdf_path):
        return libraries

    try:
        with open(vdf_path, 'r', encoding='utf-8') as f:
            content = f.read()
        # VDF 格式中所有库路径都以 "path" 键标识
        for raw in re.findall(r'"path"\s+"([^"]+)"', content):
            p = os.path.normpath(raw.replace('\\\\', '\\'))
            if os.path.exists(p) and os.path.normcase(p) not in seen:
                seen.add(os.path.normcase(p))
                libraries.append(p)
    except Exception:
        pass

    return libraries


def auto_detect_paths():
    """在各 Steam 库中自动搜索 Wallpaper Engine 的缓存文件和内容目录"""
    for lib in get_steam_libraries():
        workshopcache = os.path.join(
            lib, 'steamapps', 'common', 'wallpaper_engine', 'bin', 'workshopcache.json'
        )
        if os.path.exists(workshopcache):
            workshop_dir = os.path.join(lib, 'steamapps', 'workshop', 'content', '431960')
            return workshopcache, workshop_dir

    return None, None


def resolve_path(raw_path):
    """将配置中的路径解析为绝对路径：绝对路径直接返回，相对路径基于脚本目录解析"""
    if not raw_path:
        return ''
    if os.path.isabs(raw_path):
        return os.path.normpath(raw_path)
    return os.path.normpath(os.path.join(script_dir, raw_path))


# 默认配置模板（带注释说明，首次运行时生成）
DEFAULT_CONFIG_TEMPLATE = r'''# Wallpaper Cleaner 配置文件（支持以 # 开头的注释行）
#
# json_path: Wallpaper Engine 的 workshop 订阅缓存文件路径
# workshop_dir: workshop 壁纸内容存放目录
# 路径支持绝对路径和相对路径（相对路径基于本脚本所在目录）
#
# 示例（去掉行首的 # 即可生效）：
# json_path: D:\Steam\steamapps\common\wallpaper_engine\bin\workshopcache.json
# workshop_dir: D:\Steam\steamapps\workshop\content\431960

json_path: ""
workshop_dir: ""
'''


def parse_config_value(value, lineno, raw_line):
    """解析单个配置值：支持成对引号与 " #" 形式的行内注释；不处理转义序列，便于直接书写 Windows 路径"""
    if not value:
        return ''
    quote = value[0]
    if quote in ('"', "'"):
        end = value.find(quote, 1)
        if end == -1:
            raise ValueError(f'第 {lineno} 行引号未闭合: {raw_line}')
        return value[1:end]
    if ' #' in value:
        value = value.split(' #', 1)[0].rstrip()
    return value


def parse_config_text(text):
    """解析配置内容并返回字典：优先按 JSON 解析（兼容旧配置），失败则按扁平 YAML（key: value + # 注释）解析"""
    try:
        config = json.loads(text)
        if not isinstance(config, dict):
            raise ValueError('配置顶层必须是键值对结构')
        return config
    except json.JSONDecodeError:
        pass

    config = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped in ('{', '}', '},'):
            continue
        if ':' not in stripped:
            raise ValueError(f'第 {lineno} 行无法解析: {stripped}')
        key, _, value = stripped.partition(':')
        key = key.strip().strip("\"'")
        if not key:
            raise ValueError(f'第 {lineno} 行缺少键名: {stripped}')
        config[key] = parse_config_value(value.strip(), lineno, stripped)
    return config


def read_config_file(path):
    """读取并解析配置文件，失败时打印错误并退出"""
    try:
        # utf-8-sig 兼容带 BOM 的文件（部分 Windows 编辑器保存 UTF-8 时会加 BOM）
        with open(path, 'r', encoding='utf-8-sig') as f:
            return parse_config_text(f.read())
    except ValueError as e:
        logger.error(f'配置文件解析失败 ({path}): {e}')
        logger.error('请检查配置文件格式，或删除该文件后重新运行以生成默认配置。')
        sys.exit(1)
    except Exception as e:
        logger.error(f'读取配置文件失败 ({path}): {e}')
        sys.exit(1)


def create_default_config():
    """生成带注释说明的默认配置文件，提示用户编辑后退出"""
    logger.warning('未检测到配置文件，正在自动生成默认配置文件...')
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_CONFIG_TEMPLATE)
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


def load_config():
    """加载配置：优先 config.yml，兼容旧版 config.json；两者都不存在时生成默认 config.yml"""
    config_file = config_path
    if os.path.exists(config_path):
        config = read_config_file(config_path)
    elif os.path.exists(legacy_config_path):
        config_file = legacy_config_path
        config = read_config_file(legacy_config_path)
        logger.info('已读取旧版 config.json；建议重命名为 config.yml，即可使用 # 注释')
    else:
        create_default_config()

    json_path = resolve_path(config.get('json_path', ''))
    workshop_dir = resolve_path(config.get('workshop_dir', ''))

    if not json_path:
        logger.error(f'配置项 "json_path" 未设置或为空，请编辑配置文件: {config_file}')
        sys.exit(1)
    if not workshop_dir:
        logger.error(f'配置项 "workshop_dir" 未设置或为空，请编辑配置文件: {config_file}')
        sys.exit(1)

    logger.info(f'配置加载成功: {config_file}')
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


def ask_yes_no(prompt, ascii_prompt):
    """读取 y/n 输入。EOF/Ctrl+C 视为拒绝；非中文 locale 下重定向输出时中文提示无法编码，回退 ASCII 提示"""
    try:
        return input(prompt).strip().lower()
    except UnicodeEncodeError:
        pass
    except (EOFError, KeyboardInterrupt):
        return 'n'
    try:
        return input(ascii_prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return 'n'


def force_rmtree(path):
    """删除目录树前先清除只读属性（Windows 下只读位会令默认 rmtree 报拒绝访问）"""
    for dirpath, _dirnames, filenames in os.walk(path):
        for p in [dirpath] + [os.path.join(dirpath, f) for f in filenames]:
            try:
                os.chmod(p, stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path)


def main():
    logger.info('========== wallpaper-cleaner 开始执行 ==========')

    json_path, workshop_dir = None, None

    # 优先尝试自动检测
    auto_json, auto_workshop = auto_detect_paths()
    if auto_json and auto_workshop:
        logger.info('自动检测到 Wallpaper Engine 路径:')
        logger.info(f'  workshopcache 文件: {auto_json}')
        logger.info(f'  workshop 内容目录: {auto_workshop}')
        logger.info('')
        user_input = ask_yes_no('是否使用自动检测的路径? (y/n，默认 y): ', '(y/n, default y): ')
        if user_input in ('', 'y', 'yes'):
            json_path, workshop_dir = auto_json, auto_workshop
            logger.info('已采用自动检测的路径')
        else:
            logger.info('用户选择不使用自动检测路径，回退到配置文件')
    else:
        logger.info('未能自动检测到 Wallpaper Engine 路径，回退到配置文件')

    # 自动检测失败或用户拒绝时，从配置文件加载
    if not json_path or not workshop_dir:
        json_path, workshop_dir = load_config()

    # 读取json，获取所有已订阅的workshopid及其信息
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f'已读取订阅缓存: {json_path}')
    except Exception as e:
        logger.error(f'读取JSON文件失败: {e}')
        sys.exit(1)

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
        sys.exit(1)

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
                force_rmtree(folder_path)
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
