"""核心逻辑：路径检测、配置读写、扫描、删除

本模块只负责"做事"，不负责"表现"：
- 不打印、不退出，所有错误以异常抛出，由 cli.py / web.py 决定如何呈现与退出码
- 所有返回值为纯 dict / list，可直接 JSON 序列化
"""

import json
import os
import re
import shutil
import stat
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 本包所在目录（打包后位于 PyInstaller 解压出的临时目录里）
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def is_frozen():
    """是否运行在打包出的可执行文件里"""
    return bool(getattr(sys, 'frozen', False))


def bundle_dir():
    """打包运行时数据文件的根目录（static 等被打包资源都在这里）"""
    return getattr(sys, '_MEIPASS', PACKAGE_DIR)


def dir_is_writable(path):
    """目录是否真的能写：往里写一个临时文件再删掉

    不能用 os.access(path, os.W_OK)：Windows 上它只检查目录的只读属性，而
    Program Files 这类目录通常并没有设只读属性，只是当前用户没有写权限——
    os.access 会给出"可写"的错误答案，然后第一次写配置时才失败。
    真写一个文件是唯一可靠的判断。
    """
    if not os.path.isdir(path):
        return False
    # 带上进程号：两个实例同时启动时不会互相踩到对方的探针文件
    probe = os.path.join(path, f'.wc-write-probe-{os.getpid()}')
    try:
        with open(probe, 'w', encoding='ascii') as f:
            f.write('ok')
    except OSError:
        return False
    try:
        os.remove(probe)
    except OSError:
        pass
    return True


def _appdata_home():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'wallpaper-cleaner')


# (目录, 是否退回了 %APPDATA%)，进程内只算一次：可写性探测要真写文件，
# 不该每次有人问就探一遍
_home_cache = None


def _compute_home():
    override = os.environ.get('WALLPAPER_CLEANER_HOME')
    if override:
        return os.path.abspath(override), False
    if not is_frozen():
        return os.path.dirname(PACKAGE_DIR), False
    # 单文件模式下 sys.executable 是 exe 的真实路径，而 sys._MEIPASS 是随进程
    # 消失的解压目录——要落盘在旁边，只能用前者
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if dir_is_writable(exe_dir):
        return exe_dir, False
    return _appdata_home(), True


def app_home_dir():
    """配置与日志的存放目录

    - 源码运行：项目根目录，与旧版一致
    - 打包运行：exe 所在目录。便携优先——解压出来的那个文件夹就是它的全部家当，
      换台机器或想彻底卸载都只是挪动或删掉一个文件夹
    - exe 目录不可写时（放进 Program Files、只读介质）退回 %APPDATA%\\wallpaper-cleaner，
      这件事由 home_dir_fallback() 报出来，界面上的「关于」会如实说明
    - 可用 WALLPAPER_CLEANER_HOME 环境变量覆盖，测试与特殊部署用
    """
    global _home_cache
    if _home_cache is None:
        _home_cache = _compute_home()
    return _home_cache[0]


def home_dir_fallback():
    """配置目录是不是"exe 旁边写不进去，退回了 %APPDATA%"的结果

    界面上的「关于」用它说明配置到底在哪。用户以为配置在 exe 旁边、实际在别处
    而没有任何提示，是最容易让人以为"设置没保存"的情形。
    """
    global _home_cache
    if _home_cache is None:
        _home_cache = _compute_home()
    return _home_cache[1]


def has_console():
    """是否存在可用的控制台

    打包成 --noconsole 的 exe 后 sys.stdout / sys.stderr 都是 None，
    此时不能注册 logging.StreamHandler，否则每次写日志都会抛错。
    """
    return sys.stdout is not None and sys.stderr is not None


def ensure_dir(path):
    """尽力创建目录，失败时留给后续的写入操作去报错"""
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass


# 配置与日志都放在 app_home_dir 下
script_dir = app_home_dir()
config_path = os.path.join(script_dir, 'config.yml')
legacy_config_path = os.path.join(script_dir, 'config.json')
log_dir = os.path.join(script_dir, 'logs')
# 界面偏好（目前只有主题）单独放一个文件，不和 config.yml 混在一起：
# config.yml 回答的是"去哪儿找 Wallpaper Engine"，界面偏好是另一回事；
# 而且它会被面板随时改写，不该去动用户手写的那些注释
prefs_path = os.path.join(script_dir, 'prefs.json')

logger = logging.getLogger('WallpaperCleaner')

_logger_ready = False


class ConfigError(Exception):
    """配置读取或解析失败"""


class ConfigMissingError(ConfigError):
    """配置文件不存在（首次运行）"""


class SubscriptionError(Exception):
    """workshopcache.json 读取或解析失败"""


class ScanError(Exception):
    """扫描或删除过程中的可预期错误"""


def setup_logger():
    """初始化日志：控制台 INFO（有控制台时）+ 文件 DEBUG（单文件 5MB，保留 5 个备份）

    重复调用安全（CLI 与面板可能都会触发）。
    """
    global _logger_ready
    if _logger_ready:
        return logger

    ensure_dir(log_dir)

    logger.setLevel(logging.DEBUG)

    # 非 UTF-8 控制台直接输出中文会抛 UnicodeEncodeError，降级为替换字符而不是丢日志
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    # 打包成 --noconsole 的 exe 时没有控制台，此时注册 StreamHandler
    # 会让每次写日志都抛错（sys.stderr 是 None），必须跳过
    if has_console():
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        console_handler.setFormatter(console_fmt)
        logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        current_log_path(), maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    _logger_ready = True
    return logger


def current_log_path():
    """当天日志文件路径：wallpaper-cleaner_YYYYMMDD.log"""
    return os.path.join(log_dir, f'wallpaper-cleaner_{datetime.now().strftime("%Y%m%d")}.log')


def latest_log_file():
    """返回 logs/ 下最新的日志文件路径，没有则返回 None"""
    if not os.path.isdir(log_dir):
        return None
    candidates = [
        os.path.join(log_dir, name)
        for name in os.listdir(log_dir)
        if name.startswith('wallpaper-cleaner_') and name.endswith('.log')
    ]
    if candidates:
        return max(candidates, key=os.path.getmtime)
    legacy = os.path.join(log_dir, 'wallpaper_cleaner.log')
    return legacy if os.path.exists(legacy) else None


def read_log_tail(max_lines=200):
    """读取最新日志文件的末尾若干行，供面板日志抽屉展示"""
    path = latest_log_file()
    if not path:
        return {'path': None, 'lines': [], 'error': None}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError as e:
        return {'path': path, 'lines': [], 'error': str(e)}
    lines = content.splitlines()
    return {
        'path': path,
        'lines': lines[-max_lines:] if max_lines > 0 else lines,
        'error': None,
    }


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
    """将配置中的路径解析为绝对路径：绝对路径直接返回，相对路径基于项目根目录解析"""
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
# steam_dll_path: 取消订阅/重新订阅用的 steam_api64.dll（可填文件或所在目录），
#                 留空则自动查找（优先用 Wallpaper Engine 自带的那份）
# 路径支持绝对路径和相对路径（相对路径基于本脚本所在目录）
#
# 示例（去掉行首的 # 即可生效）：
# json_path: D:\Steam\steamapps\common\wallpaper_engine\bin\workshopcache.json
# workshop_dir: D:\Steam\steamapps\workshop\content\431960
# steam_dll_path: D:\Steam\steamapps\common\wallpaper_engine\bin\steam_api64.dll

json_path: ""
workshop_dir: ""
steam_dll_path: ""
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
    """读取并解析配置文件，失败时抛出 ConfigError"""
    try:
        # utf-8-sig 兼容带 BOM 的文件（部分 Windows 编辑器保存 UTF-8 时会加 BOM）
        with open(path, 'r', encoding='utf-8-sig') as f:
            return parse_config_text(f.read())
    except ValueError as e:
        raise ConfigError(
            f'配置文件解析失败 ({path}): {e}\n'
            '请检查配置文件格式，或删除该文件后重新运行以生成默认配置。'
        )
    except OSError as e:
        raise ConfigError(f'读取配置文件失败 ({path}): {e}')


# ---------- 界面偏好 ----------
#
# 与 config.yml 刻意分开：那是一份需要用户手写、要保留注释的配置；界面偏好是程序
# 自己读写的小状态。两者混在一起的话，面板每次改主题都要重写用户的配置文件。
#
# 这里的读写一律不抛异常：读不出来就回退默认值，写不进去就只记日志。一个显示偏好
# 不该让面板打不开，也不该让"切换主题"这个动作失败得像是程序坏了。

def load_prefs():
    """读取界面偏好，返回 dict；文件缺失或损坏都返回空 dict"""
    try:
        with open(prefs_path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_prefs(prefs):
    """写入界面偏好，成功返回 True；失败只记日志并返回 False"""
    ensure_dir(script_dir)
    try:
        with open(prefs_path, 'w', encoding='utf-8') as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
        return True
    except OSError as e:
        logger.warning('界面偏好写入失败 (%s): %s', prefs_path, e)
        return False


def write_default_config():
    """生成带注释说明的默认配置文件，返回写入路径"""
    ensure_dir(script_dir)
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_CONFIG_TEMPLATE)
    except OSError as e:
        raise ConfigError(f'自动生成配置文件失败: {e}')
    return config_path


def find_config_file():
    """定位当前生效的配置文件，返回 (路径, 是否为旧版 json)；都不存在返回 (None, False)"""
    if os.path.exists(config_path):
        return config_path, False
    if os.path.exists(legacy_config_path):
        return legacy_config_path, True
    return None, False


def load_config():
    """加载配置并返回完整信息

    返回 {'config_file', 'config_name', 'is_legacy_json', 'raw', 'json_path', 'workshop_dir'}

    - 配置文件不存在 → ConfigMissingError
    - 解析失败或必填项为空 → ConfigError
    """
    config_file, is_legacy_json = find_config_file()
    if not config_file:
        raise ConfigMissingError('未检测到配置文件')

    config = read_config_file(config_file)
    json_path = resolve_path(config.get('json_path', ''))
    workshop_dir = resolve_path(config.get('workshop_dir', ''))
    # 取消订阅/重新订阅用的 dll 是可选配置：留空表示自动查找
    steam_dll_path = resolve_path(config.get('steam_dll_path', ''))

    if not json_path:
        raise ConfigError(f'配置项 "json_path" 未设置或为空，请编辑配置文件: {config_file}')
    if not workshop_dir:
        raise ConfigError(f'配置项 "workshop_dir" 未设置或为空，请编辑配置文件: {config_file}')

    return {
        'config_file': config_file,
        'config_name': os.path.basename(config_file),
        'is_legacy_json': is_legacy_json,
        'raw': config,
        'json_path': json_path,
        'workshop_dir': workshop_dir,
        'steam_dll_path': steam_dll_path,
    }


def describe_config(auto_detect=None):
    """汇总配置状态供面板展示，任何情况下都不抛异常

    auto_detect: 可选的 (json_path, workshop_dir) 预探测结果，避免每次轮询都去查注册表
    """
    result = {
        'config_file': None,
        'config_name': None,
        'is_legacy_json': False,
        'exists': False,
        'json_path': '',
        'workshop_dir': '',
        'steam_dll_path': '',
        'auto_json_path': '',
        'auto_workshop_dir': '',
        'error': None,
    }

    if auto_detect is None:
        try:
            auto_detect = auto_detect_paths()
        except Exception:
            auto_detect = (None, None)
    result['auto_json_path'] = (auto_detect[0] or '')
    result['auto_workshop_dir'] = (auto_detect[1] or '')

    config_file, is_legacy_json = find_config_file()
    if config_file:
        result['config_file'] = config_file
        result['config_name'] = os.path.basename(config_file)
        result['is_legacy_json'] = is_legacy_json
        result['exists'] = True
        try:
            config = read_config_file(config_file)
            result['json_path'] = resolve_path(config.get('json_path', ''))
            result['workshop_dir'] = resolve_path(config.get('workshop_dir', ''))
            result['steam_dll_path'] = resolve_path(config.get('steam_dll_path', ''))
        except ConfigError as e:
            result['error'] = str(e)

    return result


def _format_config_value(value):
    """按解析器规则决定是否给值加引号：含 # 或首尾空白时加引号，其余裸写便于直接编辑"""
    value = str(value)
    if value == '' or value != value.strip() or '#' in value:
        return '"' + value + '"'
    return value


def _atomic_write(path, text):
    """先写临时文件再原子替换，避免写入中断损坏配置"""
    ensure_dir(os.path.dirname(path))
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise ConfigError(f'写入配置文件失败 ({path}): {e}')


def write_config_values(updates, config_file=None):
    """就地更新配置文件中的键值，尽量保留原有注释与行尾符，返回实际写入的文件路径

    - YAML 配置按行替换匹配键，其余内容原样保留；缺失的键追加到末尾
    - JSON 配置（旧版）整体重写为 JSON，保持格式不变
    """
    if config_file is None:
        config_file, _ = find_config_file()
        if config_file is None:
            config_file = config_path

    if config_file.lower().endswith('.json'):
        try:
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError('配置顶层必须是键值对结构')
        except FileNotFoundError:
            data = {}
        except ValueError as e:
            raise ConfigError(f'配置文件解析失败 ({config_file}): {e}')
        except OSError as e:
            raise ConfigError(f'读取配置文件失败 ({config_file}): {e}')
        data.update(updates)
        _atomic_write(config_file, json.dumps(data, indent=4, ensure_ascii=False) + '\n')
        return config_file

    try:
        # newline='' 关闭换行符转换，否则 CRLF 会在读取时被统一成 LF，回写后文件行尾全变
        with open(config_file, 'r', encoding='utf-8-sig', newline='') as f:
            original = f.read()
    except FileNotFoundError:
        original = DEFAULT_CONFIG_TEMPLATE
    except OSError as e:
        raise ConfigError(f'读取配置文件失败 ({config_file}): {e}')

    remaining = dict(updates)
    out = []
    for line in original.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        ending = line[len(body):]
        match = re.match(r'^(\s*)([^#:\s][^:]*?)(\s*):\s*(.*)$', body)
        if match and match.group(2).strip() in remaining:
            key = match.group(2).strip()
            out.append(
                f'{match.group(1)}{key}: {_format_config_value(remaining.pop(key))}{ending}'
            )
        else:
            out.append(line)

    if remaining:
        if out and not out[-1].endswith(('\n', '\r')):
            out[-1] += '\n'
        for key, value in remaining.items():
            out.append(f'{key}: {_format_config_value(value)}\n')

    _atomic_write(config_file, ''.join(out))
    return config_file


def load_subscriptions(json_path):
    """读取 workshopcache.json，返回 {workshopid: {'title':…, 'size':…}}，失败抛 SubscriptionError"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SubscriptionError(f'订阅缓存文件不存在: {json_path}')
    except json.JSONDecodeError as e:
        raise SubscriptionError(f'订阅缓存文件不是合法的 JSON: {json_path} ({e})')
    except OSError as e:
        raise SubscriptionError(f'读取订阅缓存文件失败: {json_path} ({e})')

    if not isinstance(data, dict):
        raise SubscriptionError(f'订阅缓存格式异常（顶层不是对象）: {json_path}')

    subscriptions = {}
    # 键缺失（而不是空数组）说明这个文件不是完整的 WE 缓存，多半正在被重写。
    # 此时若当成"零订阅"，磁盘上所有目录都会被判成残留，必须报错而不是放行。
    wallpapers = data.get('wallpapers')
    if not isinstance(wallpapers, list):
        raise SubscriptionError(
            f'订阅缓存里没有 wallpapers 数组，可能正在被 Wallpaper Engine 重写，请稍后重试: {json_path}'
        )

    for wp in wallpapers:
        if not isinstance(wp, dict):
            continue
        wid = str(wp.get('workshopid', ''))
        if wid:
            subscriptions[wid] = {
                'title': wp.get('title', '未知'),
                'size': wp.get('filesizelabel', '未知'),
            }
    return subscriptions


def steam_acf_path(workshop_dir):
    """由内容目录推导 Steam 的安装记录文件路径

    workshop_dir 形如 <库>\\steamapps\\workshop\\content\\431960，
    Steam 把已经装好的创意工坊内容记在 <库>\\steamapps\\workshop\\appworkshop_431960.acf。
    目录结构不标准时推出来的路径会指向不存在的位置，读不到就跳过交叉核对。
    """
    if not workshop_dir:
        return ''
    normalized = os.path.normpath(workshop_dir)
    appid = os.path.basename(normalized)
    if not appid:
        return ''
    workshop_root = os.path.dirname(os.path.dirname(normalized))
    return os.path.join(workshop_root, f'appworkshop_{appid}.acf')


# ACF 小节名（大小写不敏感）。订阅记录与内容记录是两回事：
# WorkshopItemsSubscribed / WorkshopItemDetails 是订阅记录，取消订阅后条目会被移除；
# WorkshopItemsInstalled 是内容记录，内容还在磁盘上就留着——取消订阅的残留正属于这一类，
# 所以它绝不能当订阅用。Wokshop... 那个拼写是旧版 Steam 的。
ACF_SUBSCRIBED_SECTIONS = ('workshopitemssubscribed',)
ACF_DETAILS_SECTIONS = ('workshopitemdetails',)
ACF_INSTALLED_SECTIONS = ('workshopitemsinstalled', 'wokshopitemsinstalled')

# 匹配 VDF/KeyValues 的 token：带引号的字符串、花括号、裸词
_vdf_token_re = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|([^\s{}"]+)')


def parse_vdf(text):
    """把 Valve KeyValues（VDF/ACF）文本解析成嵌套 dict

    只做只读提取，不校验格式：文本被截断（Steam 正在重写）时返回已经解析到的部分，
    不抛异常。同名键重复出现时后面的覆盖前面的。
    """
    root = {}
    stack = [root]
    pending = None
    for match in _vdf_token_re.finditer(text):
        token = match.group(1)
        if token is None:
            token = match.group(3)
        brace = match.group(2)
        if brace == '{':
            if pending is None:
                node = stack[-1]  # 文件直接以 { 开头（没有包裹的键名）时就地展开
            else:
                node = {}
                stack[-1][pending] = node
            stack.append(node)
            pending = None
        elif brace == '}':
            if len(stack) > 1:
                stack.pop()
            pending = None
        elif pending is None:
            pending = token
        else:
            # 上一个 token 是键，这个就是它的值，成对消费掉
            stack[-1][pending] = token
            pending = None
    return root


def _sections(app):
    """把子节点按小写名索引，便于大小写不敏感地找小节"""
    return {str(key).lower(): value for key, value in app.items() if isinstance(value, dict)}


def _first_section(sections, names):
    for name in names:
        if name in sections:
            return sections[name]
    return {}


def parse_acf_record(text):
    """从 ACF 文本里分离出「订阅记录」与「内容记录」，返回 (subscribed, installed)

    subscribed：WorkshopItemsSubscribed 的键（存在时）∪ WorkshopItemDetails 里
    subscribedby 非空且不为 '0' 的条目 —— 这才是"还在订阅"的依据。

    installed：WorkshopItemsInstalled 的条目，只说明内容还在磁盘上（取消订阅后
    残留的目录就属于这一类），仅供显示与诊断，绝不参与订阅判断。
    """
    root = parse_vdf(text)
    app = root.get('AppWorkshop')
    if not isinstance(app, dict):
        app = root
    sections = _sections(app)

    subscribed = {str(wid) for wid in _first_section(sections, ACF_SUBSCRIBED_SECTIONS)}
    for wid, fields in _first_section(sections, ACF_DETAILS_SECTIONS).items():
        if not isinstance(fields, dict):
            continue
        by = str(fields.get('subscribedby') or '').strip()
        if by and by != '0':
            subscribed.add(str(wid))

    installed = {}
    for wid, fields in _first_section(sections, ACF_INSTALLED_SECTIONS).items():
        if isinstance(fields, dict):
            installed[str(wid)] = fields
    return subscribed, installed


def load_steam_record(acf_path):
    """读取 Steam 的 appworkshop ACF，返回 {'subscribed', 'installed', 'mtime', 'ok'}

    读不到（文件不存在、Steam 正在重写、内容被截断）时返回空记录并置 ok=False，
    由调用方决定如何降级——这里绝不抛异常。
    """
    record = {'subscribed': set(), 'installed': {}, 'mtime': 0.0, 'ok': False}
    if not acf_path or not os.path.isfile(acf_path):
        return record
    try:
        record['mtime'] = os.path.getmtime(acf_path)
        with open(acf_path, 'r', encoding='utf-8-sig', errors='replace') as f:
            text = f.read()
    except OSError as e:
        logger.debug('读取 Steam 记录失败 (%s): %s', acf_path, e)
        return record

    record['subscribed'], record['installed'] = parse_acf_record(text)
    record['ok'] = True
    logger.debug(
        'Steam 记录 %s: 订阅 %d 条，安装 %d 条',
        acf_path, len(record['subscribed']), len(record['installed']),
    )
    return record


def installed_sizes(record):
    """从内容记录里取每个 ID 的占用字节数（只用于显示），解析不了的跳过"""
    sizes = {}
    for wid, fields in (record.get('installed') or {}).items():
        try:
            sizes[str(wid)] = int(str(fields.get('size') or '').strip())
        except (AttributeError, TypeError, ValueError):
            continue
    return sizes


def load_subscription_context(json_path, workshop_dir):
    """读齐扫描/删除所需的订阅信息，「谁更新就信谁」的策略集中在这里

    返回 {'subscriptions', 'protected', 'disputed', 'complete', 'installed_sizes',
          'steam', 'cache_mtime', 'steam_fresh'}

    protected = workshopcache.json 的订阅 ∪ 可采信的 Steam 订阅记录。Steam 的 ACF 只在
    它比 WE 缓存更新时才被采信：更旧的 ACF 里那条 subscribedby 已经过期（刚取消订阅
    就是这个状态，Steam 还没重写文件），此时以缓存为准，否则残留会被一直当成已订阅。

    complete = 内容记录里有的 ID，说明下载早已装完，删除时不必再按"可能正在下载"等一轮。
    它只要求 ACF 读得到，不要求它比缓存新——"装完"是过去发生的事，不会因为 WE 重写了
    缓存而失效（条目只在内容被删时才消失）。「谁更新就信谁」只适用于订阅记录。
    """
    subscriptions = load_subscriptions(json_path)
    steam = load_steam_record(steam_acf_path(workshop_dir))
    try:
        cache_mtime = os.path.getmtime(json_path)
    except OSError:
        cache_mtime = 0.0

    steam_fresh = bool(steam['ok']) and steam['mtime'] >= cache_mtime
    protected = set(subscriptions)
    disputed = set()
    if steam_fresh:
        protected |= steam['subscribed']
    else:
        disputed = steam['subscribed'] - set(subscriptions)

    return {
        'subscriptions': subscriptions,
        'protected': protected,
        'disputed': disputed,
        'complete': set(steam['installed']) if steam['ok'] else set(),
        'installed_sizes': installed_sizes(steam),
        'steam': steam,
        'cache_mtime': cache_mtime,
        'steam_fresh': steam_fresh,
    }


def describe_steam_record(context):
    """把 Steam 记录的状态整理成给用户看的日志行 [(level, message), ...]"""
    steam = context['steam']
    subscriptions = context['subscriptions']
    lines = []

    if not steam['ok']:
        lines.append(('warn', '读不到 Steam 的订阅记录（appworkshop ACF），本次不做交叉核对'))
    elif context['steam_fresh']:
        pending = steam['subscribed'] - set(subscriptions)
        lines.append((
            'info',
            f"Steam 订阅记录: {len(steam['subscribed'])} 条"
            + (f'，其中 {len(pending)} 条还没进订阅缓存' if pending else ''),
        ))
    else:
        lines.append(('info', 'Steam 记录比 Wallpaper Engine 缓存旧，本次以缓存为准'))
        if context['disputed']:
            lines.append((
                'warn',
                f"有 {len(context['disputed'])} 个目录 Steam 记录仍标记为已订阅，"
                '但 Steam 记录较旧，已按缓存判为待清理',
            ))

    leftovers = set(steam['installed']) - set(subscriptions) - steam['subscribed']
    if leftovers:
        lines.append(('debug', f'Steam 安装记录里有 {len(leftovers)} 条内容已不在订阅列表'))
    return lines


PROJECT_JSON_MAX_BYTES = 1024 * 1024

# 预览图：作者随内容一起发布的图片就躺在壁纸目录里，面板直接把字节发给浏览器，
# 由浏览器解码、缩放、播放动画——不需要 Pillow，也不用联网问 Steam 要图。
# 8 MB 是给「一张预览图」的常识边界，超过就当成没有，免得面板去读一个畸形大文件。
PREVIEW_MAX_BYTES = 8 * 1024 * 1024
PREVIEW_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif')
PREVIEW_FALLBACK_NAMES = ('preview.jpg', 'preview.png', 'preview.jpeg', 'preview.gif')

# Wallpaper Engine 自己还缓存了一份浏览用的缩略图：<WE>\ui\thumbnails\ws_<ID>_thumb.jpg。
# 目录里的预览图被 Steam 连着内容一起删掉后，这份常常还在，就拿它兜底——同样是读本地
# 文件，仍然不联网问 Steam 要图。它是给列表用的小图，放大看会糊，所以只在没有原图时用。
WE_THUMB_DIR_PARTS = ('ui', 'thumbnails')
WE_THUMB_NAME_TEMPLATE = 'ws_{wid}_thumb.jpg'


def read_project_meta(folder):
    """从壁纸目录的 project.json 读标题、类型、作者声明的预览图，以及它还在不在

    返回 {'title', 'type', 'preview', 'present'}：

    - project.json 是作者随内容一起发布的清单文件，就躺在目录里，与订阅状态无关——
      已取消订阅的残留也能读到名字。present 表示这个文件在不在：Steam 把内容清理掉
      之后，目录里往往只剩 Wallpaper Engine 自己写的缓存，清单随之消失，这就是
      「内容已缺失」最可靠的标志。
    - 文件在但读不懂（坏 JSON、超大、不是对象）仍算 present，只是字段全空：
      那是这个文件有问题，不该说成内容被清掉了。
    - preview 是文件名字段，这里只原样读出来，是否可用交给 find_preview 判断。
    """
    path = os.path.join(folder, 'project.json')
    empty = {
        'title': '', 'type': '', 'preview': '',
        'present': os.path.isfile(path),
    }
    try:
        if os.path.getsize(path) > PROJECT_JSON_MAX_BYTES:
            return empty
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    return {
        'title': str(data.get('title') or '').strip(),
        'type': str(data.get('type') or '').strip(),
        'preview': str(data.get('preview') or '').strip(),
        'present': True,
    }


def _preview_name_ok(name):
    """预览图名字必须是单层文件名且是图片扩展名（project.json 的内容不可全信）"""
    if not name or not isinstance(name, str):
        return False
    if name in ('.', '..') or '/' in name or '\\' in name:
        return False
    return os.path.splitext(name)[1].lower() in PREVIEW_EXTENSIONS


def resolve_preview_path(folder, name):
    """把预览图文件名解析为绝对路径并复核，不合格返回空字符串

    复核的是「扫描之后目录被人动过」的情况：文件名必须是单层图片名，真实路径
    必须落在壁纸目录里（挡住被换成指向目录外的软链接），且不能大得离谱。
    """
    if not _preview_name_ok(name):
        return ''
    path = os.path.join(folder, name)
    try:
        if not os.path.isfile(path):
            return ''
        if os.path.getsize(path) > PREVIEW_MAX_BYTES:
            return ''
        base = os.path.realpath(folder)
        real = os.path.realpath(path)
    except OSError:
        return ''
    if os.path.dirname(real) != base:
        return ''
    return path


def find_preview(folder, declared=''):
    """找出目录里可用的预览图，返回文件名（不含路径）；没有则返回空字符串

    project.json 的 preview 字段是作者声明的预览图，优先用它；字段不可信或文件
    不在时按固定候选名找。静态图排在 GIF 前面，这样只有作者指定动图时才播动画。
    """
    candidates = []
    if _preview_name_ok(declared):
        candidates.append(declared)
    candidates.extend(PREVIEW_FALLBACK_NAMES)
    for name in candidates:
        if resolve_preview_path(folder, name):
            return name
    return ''


def we_thumbnail_dir(json_path):
    """由 WE 缓存文件的路径推导它的缩略图缓存目录，目录不存在时返回空字符串

    json_path 形如 <WE>\\bin\\workshopcache.json，缩略图在 <WE>\\ui\\thumbnails。
    路径不标准（例如用户把缓存文件复制到了别处）时推出来的目录不存在，那就当作没有兜底图，
    不报错也不去找别的地方。
    """
    if not json_path:
        return ''
    we_root = os.path.dirname(os.path.dirname(os.path.normpath(json_path)))
    path = os.path.join(we_root, *WE_THUMB_DIR_PARTS)
    return path if os.path.isdir(path) else ''


def we_thumbnail_path(json_path, wid):
    """找 WE 缩略图缓存里这个 ID 的图，返回绝对路径；没有或不合格返回空字符串

    文件名由 ID 精确拼出，不扫描目录、也不接受调用方给的路径。读之前再复核一次：
    是文件、不太大、真实路径确实落在缩略图目录里（挡住被换成指向别处的软链接）。
    """
    if not is_safe_wid(wid):
        return ''
    directory = we_thumbnail_dir(json_path)
    if not directory:
        return ''
    path = os.path.join(directory, WE_THUMB_NAME_TEMPLATE.format(wid=wid))
    try:
        if not os.path.isfile(path):
            return ''
        if os.path.getsize(path) > PREVIEW_MAX_BYTES:
            return ''
        if os.path.dirname(os.path.realpath(path)) != os.path.realpath(directory):
            return ''
    except OSError:
        return ''
    return path


def thumb_source(preview_name, json_path, wid):
    """这条扫描结果的缩略图从哪来：'folder'（目录里的预览图）、'we'（WE 缓存）、''（没有）"""
    if preview_name:
        return 'folder'
    return 'we' if we_thumbnail_path(json_path, wid) else ''


# 目录里的内容在这么久之内被改动过就不参与删除，兜住"正在下载、两边都还没有记录"的窗口。
# 取 30 分钟是因为 WE 缓存的滞后可能长达几十分钟（实测有 37 分钟才刷新的），
# 而误跳过只是让残留晚一轮清理，误删则要找回收站。
# Steam 记录里已写明内容装完的不受此限（见 load_subscription_context 的 complete）。
FRESH_DOWNLOAD_GRACE_SECONDS = 1800


def newest_content_mtime(path):
    """目录里内容最近一次变动的时间（秒）；目录为空时退回目录自身的时间，读不到返回 None

    只看一层条目就够：Steam 下载时项目文件、预览图、内容包都写在壁纸目录的顶层，
    Wallpaper Engine 写的 shader 缓存也是一层子目录（它的 mtime 随缓存写入更新）。
    """
    newest = None
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    stamp = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if newest is None or stamp > newest:
                    newest = stamp
    except OSError:
        return None
    if newest is not None:
        return newest
    # 空目录没有内容可看，只能看它自己：Steam 刚为一次下载建好目录时就是这种状态
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def is_freshly_downloaded(path, grace_seconds=None):
    """目录是否还在「刚下载」保护期内（按目录里内容的修改时间判断）

    Steam 下载时会不断往目录里写文件，所以正在下载的目录，里面条目的修改时间很新。
    之所以不直接看目录自身的 mtime：Steam **删除**内容时同样会刷新它，而残留目录的
    内容时间还停在下载那一刻——只看目录的话，"刚被 Steam 清空的残留"会被误判成
    "正在下载"，白等一轮保护期。

    这只是兜底判断，主判据是 Steam 写好的安装记录；读不到目录返回 False，
    让删除流程照常去报它自己的错误，而不是无限期保护下去。
    """
    grace = FRESH_DOWNLOAD_GRACE_SECONDS if grace_seconds is None else grace_seconds
    if grace <= 0:
        return False
    stamp = newest_content_mtime(path)
    if stamp is None:
        return False
    return (time.time() - stamp) < grace


_cluster_size_cache = {}


def volume_cluster_size(path):
    """返回 path 所在卷的簇大小（字节），取不到返回 0

    磁盘实际占用是"每个文件向上取整到簇"，所以要先知道簇有多大。只查一次、按卷缓存：
    一次扫描里的目录都落在同一个卷上。非 Windows 与调用失败都返回 0，调用方据此降级。
    """
    if sys.platform != 'win32':
        return 0
    root = os.path.splitdrive(os.path.abspath(path))[0] + '\\'
    if root in _cluster_size_cache:
        return _cluster_size_cache[root]

    size = 0
    try:
        import ctypes
        sectors = ctypes.c_uint32()
        bytes_per_sector = ctypes.c_uint32()
        free_clusters = ctypes.c_uint32()
        total_clusters = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetDiskFreeSpaceW(
            ctypes.c_wchar_p(root), ctypes.byref(sectors), ctypes.byref(bytes_per_sector),
            ctypes.byref(free_clusters), ctypes.byref(total_clusters),
        ):
            size = sectors.value * bytes_per_sector.value
    except Exception as e:
        logger.debug('读取卷簇大小失败 (%s): %s', root, e)
        size = 0

    _cluster_size_cache[root] = size
    return size


def get_dir_usage(path, cluster_size=0):
    """一次遍历算出目录的两个口径，返回 (文件字节数, 磁盘占用字节数)

    - 文件字节数：目录里所有文件的 st_size 之和，就是"这批内容有多大"的精确值，
      内容完整时与 Steam/WE 标注的大小一致。
    - 磁盘占用：文件系统按簇分配空间，每个文件至少吃一个簇，目录自身也要占，所以
      小文件多的目录会明显大于文件字节数。这里按 cluster_size 向上取整，并给每个
      子目录算一个簇——接近资源管理器里那个「占用空间」。它是估算：NTFS 压缩与稀疏
      文件会高估，资源管理器还会算目录索引与 MFT，所以两边不会逐字节相等。
      cluster_size <= 0（拿不到簇大小）时磁盘占用返回 0，由调用方退回文件字节数。

    权限不足的子项跳过，与实际能读到的内容保持一致。
    """
    total = 0
    allocated = 0
    stack = [path]
    while stack:
        current = stack.pop()
        if cluster_size > 0 and current != path:
            allocated += cluster_size  # 子目录自身也要占一个簇
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            total += size
                            if cluster_size > 0:
                                allocated += -(-size // cluster_size) * cluster_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total, allocated


def get_dir_size(path):
    """递归计算目录的文件字节数合计，权限不足的子项跳过"""
    return get_dir_usage(path)[0]


def usage_bytes(item):
    """对外报「占了多少 / 释放多少」用的数字：磁盘实际占用优先，测不出时退回文件字节数

    删除释放的是磁盘上真实占用的空间，所以以磁盘占用为准；拿不到簇大小（非 Windows 或
    查询失败）时退回文件字节数，至少不会把数字报成 0。
    """
    return item.get('alloc_bytes') or item.get('size_bytes') or 0


def format_size(size_bytes):
    """将字节数转为人类可读的格式"""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size_bytes < 1024:
            return f'{size_bytes:.2f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.2f} PB'


def item_label(item):
    """给日志与提示用的名字：`ID（标题）`，没有标题时只有 ID"""
    title = (item.get('title') or '').strip()
    wid = str(item.get('wid', ''))
    return f'{wid}（{title}）' if title else wid


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


def open_folder(path):
    """用系统文件管理器打开目录（Windows 下就是资源管理器），失败抛 OSError

    面板「点标题打开目录」用的就是它：把路径交给系统去打开，不读取、不解析、
    不改动目录里的任何东西。
    """
    if sys.platform != 'win32':
        raise OSError('当前系统不支持打开文件夹')
    os.startfile(path)


def open_url(url):
    """把链接交给系统打开，失败抛 OSError

    steam:// 这类协议由 Steam 客户端自己注册并处理：面板里的「启动 Steam」与
    「在 Steam 中手动取消订阅」都靠它跳转。和 open_folder 一样，只发起打开，
    不读、不改任何东西。
    """
    if sys.platform != 'win32':
        raise OSError('当前系统不支持打开链接')
    os.startfile(url)


def send_to_recycle_bin(path):
    """把文件/目录移入回收站（Windows Shell API，纯标准库），成功返回 True

    目录超出回收站单卷容量限制时 Shell 会拒绝，此时返回 False，由调用方决定是否回落为永久删除。
    """
    if sys.platform != 'win32':
        return False

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('wFunc', wintypes.UINT),
            ('pFrom', wintypes.LPCWSTR),
            ('pTo', wintypes.LPCWSTR),
            ('fFlags', ctypes.c_uint16),
            ('fAnyOperationsAborted', wintypes.BOOL),
            ('hNameMappings', ctypes.c_void_p),
            ('lpszProgressTitle', wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    # pFrom 需要以双 \0 结尾表示列表结束
    op.pFrom = os.path.abspath(path) + '\0\0'
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = False

    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception:
        return False
    # 部分系统上 API 返回 0 但操作被标记为中止
    return result == 0 and not op.fAnyOperationsAborted


_wid_re = re.compile(r'^[0-9A-Za-z_.-]+$')


def is_safe_wid(wid):
    """校验 workshop ID 是否为安全的单层目录名（不含路径分隔符或上跳片段）"""
    if not wid or not isinstance(wid, str):
        return False
    if not _wid_re.match(wid):
        return False
    return wid not in ('.', '..')


def resolve_target_path(workshop_dir, wid):
    """把 workshop ID 解析为 workshop_dir 下的绝对路径；越界或非法 ID 抛 ScanError"""
    if not is_safe_wid(wid):
        raise ScanError(f'非法的 workshop ID: {wid!r}')
    base = os.path.abspath(workshop_dir)
    target = os.path.abspath(os.path.join(base, wid))
    if os.path.dirname(target) != base:
        raise ScanError(f'路径越界，已拒绝: {wid!r}')
    return target


def _emit(on_progress, done, total, message, level='info'):
    """统一的进度回调：on_progress(done, total, message, level)"""
    if on_progress:
        on_progress(done, total, message, level)


def declared_size_info(wid, info, sizes):
    """算一条壁纸的「标注大小」，返回 (字节数, 展示字符串)

    标注是别人记下的内容字节数，不是我们量出来的，所以它可能缺失、也可能落后于最新版本：

    - Steam 内容记录（appworkshop ACF 的 WorkshopItemsInstalled.size）优先：它是字节数，
      我们自己格式化，两张表的精度与格式才一致；
    - 其次是 WE 缓存里的标注字符串（filesizelabel），它只在 Steam 记录缺失时兜底；
    - 都没有就返回 (0, '未知')——残留目录被 Steam 删掉内容记录后就是这种情况。

    字节数 0 表示没有记录，供「重新订阅会下载多大」这类合计使用。
    """
    recorded = sizes.get(str(wid)) or 0
    if recorded:
        return recorded, format_size(recorded)
    label = (info or {}).get('size') or ''
    return 0, label or '未知'


def scan(workshop_dir, subscriptions, json_path='', config_file='', on_progress=None, max_workers=8,
         extra_subscribed=None, extra_sizes=None):
    """扫描 workshop 目录并按订阅状态分类，不删除任何内容

    分类规则：
    - subscribed: 在订阅列表中（保留）
    - orphans:    纯数字目录名且未订阅（典型的取消订阅残留）
    - unknown:    非纯数字目录名且未订阅（来源不明，默认不勾选）
    - missing:    订阅列表中但磁盘上没有对应目录

    extra_subscribed 是额外的「已订阅」来源（可采信的 Steam 订阅记录），用于兜住刚下载、
    WE 缓存还没收录的壁纸；extra_sizes 是它对应的内容字节数（Steam 内容记录），既用于
    补齐「标注大小」，也是删除时判定"内容已装完"的依据。

    每个条目还带这些显示用字段：
    - preview / thumb_source / content_missing：见 thumb_source() 与 read_project_meta()
    - size_bytes：目录里文件的字节数合计（精确值）
    - alloc_bytes：磁盘实际占用（按簇对齐的估算），0 表示拿不到簇大小、退回 size_bytes
    - declared_bytes / declared_size：标注大小的字节数与展示字符串，见 declared_size_info()
    """
    if not os.path.isdir(workshop_dir):
        raise ScanError(f'workshop目录不存在: {workshop_dir}')

    extra = set(extra_subscribed or ())
    sizes = extra_sizes or {}

    subscribed, orphans, unknown = [], [], []
    for name in os.listdir(workshop_dir):
        path = os.path.join(workshop_dir, name)
        if not os.path.isdir(path):
            continue
        # 每个目录都读一次 project.json：标题、类型、作者声明的预览图都来自它，
        # 缓存里还没有这个目录（刚下载）时更是只能靠它
        meta = read_project_meta(path)
        preview = find_preview(path, meta['preview'])
        if name in subscriptions or name in extra:
            info = subscriptions.get(name) or {}
            title = info.get('title') or meta['title'] or ''
            declared_bytes, declared_size = declared_size_info(name, info, sizes)
            subscribed.append({
                'wid': name,
                'path': path,
                'size_bytes': 0,
                'alloc_bytes': 0,
                'kind': 'subscribed',
                'title': title or '未知',
                'declared_size': declared_size,
                'declared_bytes': declared_bytes,
                'wp_type': meta['type'],
                'preview': preview,
                'thumb_source': thumb_source(preview, json_path, name),
                'content_missing': not meta['present'],
            })
        elif name.isdigit():
            # 残留目录里也有作者发布的 project.json，用它把标题补上，别只显示一串数字
            declared_bytes, declared_size = declared_size_info(name, None, sizes)
            orphans.append({
                'wid': name, 'path': path, 'size_bytes': 0, 'alloc_bytes': 0, 'kind': 'orphan',
                'title': meta['title'], 'declared_size': declared_size,
                'declared_bytes': declared_bytes, 'wp_type': meta['type'],
                'preview': preview,
                'thumb_source': thumb_source(preview, json_path, name),
                'content_missing': not meta['present'],
            })
        else:
            declared_bytes, declared_size = declared_size_info(name, None, sizes)
            unknown.append({
                'wid': name, 'path': path, 'size_bytes': 0, 'alloc_bytes': 0, 'kind': 'unknown',
                'title': meta['title'], 'declared_size': declared_size,
                'declared_bytes': declared_bytes, 'wp_type': meta['type'],
                'preview': preview,
                'thumb_source': thumb_source(preview, json_path, name),
                'content_missing': not meta['present'],
            })

    # 目录大小计算是纯磁盘 I/O，用线程池并行（os.scandir 不持有 GIL）。
    # 两个口径同一次遍历算出来，不额外多走一遍目录。
    cluster = volume_cluster_size(workshop_dir)
    targets = subscribed + orphans + unknown
    total = len(targets)
    _emit(on_progress, 0, total, f'开始计算 {total} 个目录的大小…')
    if total:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(get_dir_usage, item['path'], cluster): item for item in targets}
            for index, future in enumerate(as_completed(futures), 1):
                item = futures[future]
                try:
                    item['size_bytes'], item['alloc_bytes'] = future.result()
                except Exception:
                    item['size_bytes'] = item['alloc_bytes'] = 0
                _emit(on_progress, index, total, f'计算大小 {index}/{total}：{item["wid"]}', 'debug')

    # 默认顺序与服务端排序都按磁盘占用（表里那个可点的列），拿不到簇大小时退回文件字节数
    orphans.sort(key=lambda i: (-usage_bytes(i), i['wid']))
    unknown.sort(key=lambda i: (-usage_bytes(i), i['wid']))
    subscribed.sort(key=lambda i: i['wid'])

    on_disk = {item['wid'] for item in targets}
    missing = sorted(wid for wid in set(subscriptions) | extra if wid not in on_disk)

    return {
        'json_path': json_path,
        'config_file': config_file,
        'workshop_dir': workshop_dir,
        'scanned_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_folders': len(targets),
        'subscribed': subscribed,
        'orphans': orphans,
        'unknown': unknown,
        'missing': missing,
        # *_bytes 是文件字节数（精确值），*_usage_bytes 是磁盘占用（删掉后真正腾出来的量）
        'orphan_bytes': sum(i['size_bytes'] for i in orphans),
        'unknown_bytes': sum(i['size_bytes'] for i in unknown),
        'orphan_usage_bytes': sum(usage_bytes(i) for i in orphans),
        'unknown_usage_bytes': sum(usage_bytes(i) for i in unknown),
    }


def delete_folders(items, workshop_dir, to_recycle_bin=False, on_progress=None):
    """删除给定的目录项

    items 应来自 scan() 的结果；workshop_dir 为其所属的 workshop 目录。
    每个 wid 都会重新做一次路径校验，单项失败不影响其余项。

    报出来的大小走 usage_bytes()：磁盘实际占用优先（删除真正腾出来的就是这些簇），
    拿不到簇大小时退回文件字节数。删除移入回收站时空间要等清空回收站才真正还给系统，
    数字仍按占用报，别让"释放了多少"看起来像已经落袋。

    返回 {'deleted': [...], 'failed': [...], 'freed_bytes': int}
    """
    deleted, failed = [], []
    freed_bytes = 0
    total = len(items)

    for index, item in enumerate(items, 1):
        wid = item.get('wid', '')
        size_bytes = usage_bytes(item)
        try:
            path = resolve_target_path(workshop_dir, wid)
            if not os.path.exists(path):
                raise ScanError('目录已不存在')

            if to_recycle_bin and send_to_recycle_bin(path):
                action = 'recycle'
                if os.path.exists(path):
                    raise ScanError('回收站操作未生效，目录仍然存在')
            else:
                action = 'delete'
                # 请求了回收站但失败（例如超出回收站容量限制）时回落为永久删除
                force_rmtree(path)

            freed_bytes += size_bytes
            deleted.append({'wid': wid, 'size_bytes': size_bytes, 'action': action})
            if action == 'delete' and to_recycle_bin:
                _emit(on_progress, index, total,
                      f'已删除: {wid} ({format_size(size_bytes)})'
                      '　⚠ 回收站拒绝该目录，已永久删除', 'warn')
            else:
                label = '已移入回收站' if action == 'recycle' else '已删除'
                _emit(on_progress, index, total,
                      f'{label}: {wid} ({format_size(size_bytes)})')
        except Exception as e:
            failed.append({'wid': wid, 'error': str(e)})
            _emit(on_progress, index, total, f'删除失败 {wid}: {e}', 'error')

    return {'deleted': deleted, 'failed': failed, 'freed_bytes': freed_bytes}
