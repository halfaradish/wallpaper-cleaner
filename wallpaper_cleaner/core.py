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


def app_home_dir():
    """配置与日志的存放目录

    - 源码运行：项目根目录，与旧版一致
    - 打包运行：%APPDATA%\\wallpaper-cleaner。不能用 exe 所在目录（可能被放进
      Program Files 而不可写），也不能用解压目录（单文件模式随进程消失）
    - 可用 WALLPAPER_CLEANER_HOME 环境变量覆盖，便于测试与便携部署
    """
    override = os.environ.get('WALLPAPER_CLEANER_HOME')
    if override:
        return os.path.abspath(override)
    if is_frozen():
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'wallpaper-cleaner')
    return os.path.dirname(PACKAGE_DIR)


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


def read_project_meta(folder):
    """从壁纸目录的 project.json 读标题与类型，返回 {'title', 'type'}

    project.json 是作者随内容一起发布的，就躺在目录里，与订阅状态无关——
    已取消订阅的残留也能读到名字。读不到（缺失、坏文件、过大）时返回空字符串。
    """
    path = os.path.join(folder, 'project.json')
    try:
        if os.path.getsize(path) > PROJECT_JSON_MAX_BYTES:
            return {'title': '', 'type': ''}
        with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {'title': '', 'type': ''}
    if not isinstance(data, dict):
        return {'title': '', 'type': ''}
    return {
        'title': str(data.get('title') or '').strip(),
        'type': str(data.get('type') or '').strip(),
    }


# 目录在这么久之内被改动过就不参与删除，兜住"正在下载、两边都还没有记录"的窗口。
# 取 30 分钟是因为 WE 缓存的滞后可能长达几十分钟（实测有 37 分钟才刷新的），
# 而误跳过只是让残留晚一轮清理，误删则要找回收站。
# Steam 记录里已写明内容装完的不受此限（见 load_subscription_context 的 complete）。
FRESH_DOWNLOAD_GRACE_SECONDS = 1800


def is_freshly_downloaded(path, grace_seconds=None):
    """目录是否还在「刚下载」保护期内（按目录自身的修改时间判断）

    Steam 下载时会不断往目录里写文件，所以正在下载的目录修改时间很新。
    这只是兜底判断，主判据是 Steam 写好的安装记录；stat 失败返回 False，
    让删除流程照常去报它自己的错误，而不是无限期保护下去。
    """
    grace = FRESH_DOWNLOAD_GRACE_SECONDS if grace_seconds is None else grace_seconds
    if grace <= 0:
        return False
    try:
        return (time.time() - os.path.getmtime(path)) < grace
    except OSError:
        return False


def get_dir_size(path):
    """递归计算目录总大小（字节），权限不足的子项跳过"""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
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


def scan(workshop_dir, subscriptions, json_path='', config_file='', on_progress=None, max_workers=8,
         extra_subscribed=None, extra_sizes=None):
    """扫描 workshop 目录并按订阅状态分类，不删除任何内容

    分类规则：
    - subscribed: 在订阅列表中（保留）
    - orphans:    纯数字目录名且未订阅（典型的取消订阅残留）
    - unknown:    非纯数字目录名且未订阅（来源不明，默认不勾选）
    - missing:    订阅列表中但磁盘上没有对应目录

    extra_subscribed 是额外的「已订阅」来源（可采信的 Steam 订阅记录），用于兜住刚下载、
    WE 缓存还没收录的壁纸；extra_sizes 是它对应的占用字节数，仅用于补齐显示。
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
        if name in subscriptions or name in extra:
            info = subscriptions.get(name) or {}
            title = info.get('title') or ''
            wp_type = ''
            if not title:
                # 缓存里还没有它（刚下载）时读壁纸自己的 project.json，大小取 Steam 记录
                meta = read_project_meta(path)
                title, wp_type = meta['title'], meta['type']
            declared = info.get('size') or ''
            if not declared and name in sizes:
                declared = format_size(sizes[name])
            subscribed.append({
                'wid': name,
                'path': path,
                'size_bytes': 0,
                'kind': 'subscribed',
                'title': title or '未知',
                'declared_size': declared or '未知',
                'wp_type': wp_type,
            })
        elif name.isdigit():
            # 残留目录里也有作者发布的 project.json，用它把标题补上，别只显示一串数字
            meta = read_project_meta(path)
            orphans.append({
                'wid': name, 'path': path, 'size_bytes': 0, 'kind': 'orphan',
                'title': meta['title'], 'declared_size': '', 'wp_type': meta['type'],
            })
        else:
            meta = read_project_meta(path)
            unknown.append({
                'wid': name, 'path': path, 'size_bytes': 0, 'kind': 'unknown',
                'title': meta['title'], 'declared_size': '', 'wp_type': meta['type'],
            })

    # 目录大小计算是纯磁盘 I/O，用线程池并行（os.scandir 不持有 GIL）
    targets = subscribed + orphans + unknown
    total = len(targets)
    _emit(on_progress, 0, total, f'开始计算 {total} 个目录的大小…')
    if total:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(get_dir_size, item['path']): item for item in targets}
            for index, future in enumerate(as_completed(futures), 1):
                item = futures[future]
                try:
                    item['size_bytes'] = future.result()
                except Exception:
                    item['size_bytes'] = 0
                _emit(on_progress, index, total, f'计算大小 {index}/{total}：{item["wid"]}', 'debug')

    orphans.sort(key=lambda i: (-i['size_bytes'], i['wid']))
    unknown.sort(key=lambda i: (-i['size_bytes'], i['wid']))
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
        'orphan_bytes': sum(i['size_bytes'] for i in orphans),
        'unknown_bytes': sum(i['size_bytes'] for i in unknown),
    }


def delete_folders(items, workshop_dir, to_recycle_bin=False, on_progress=None):
    """删除给定的目录项

    items 应来自 scan() 的结果；workshop_dir 为其所属的 workshop 目录。
    每个 wid 都会重新做一次路径校验，单项失败不影响其余项。
    返回 {'deleted': [...], 'failed': [...], 'freed_bytes': int}
    """
    deleted, failed = [], []
    freed_bytes = 0
    total = len(items)

    for index, item in enumerate(items, 1):
        wid = item.get('wid', '')
        size_bytes = item.get('size_bytes', 0)
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
