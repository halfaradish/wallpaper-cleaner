"""通过本机的 steam_api64.dll 连接运行中的 Steam 客户端，读写创意工坊订阅

原理：Steamworks 的 dll 与具体游戏无关，任何一份 steam_api64.dll 都能以
Wallpaper Engine 的 AppID（431960）连上本机的 Steam 客户端——这与 Wallpaper
Engine 自己跟 Steam 通信走的是同一条通道。拿到 ISteamUGC 接口之后就能：

1. 读出账号当前的订阅列表（比本地 ACF / workshopcache.json 更权威）
2. 取消订阅 / 重新订阅指定物品，Steam 会异步删除或重新下载对应内容

写入边界：优先使用 Wallpaper Engine 自带的 dll 与它同目录的 steam_appid.txt
（零写入）；只有当 dll 同目录没有正确的 appid 文件时，才在程序自己的数据目录
下写一份 steam_ctx/steam_appid.txt。绝不往第三方游戏目录写任何东西。

已知副作用：初始化期间 Steam 会认为「Wallpaper Engine 正在运行」（好友状态、
库页面），这是 Steamworks 的固有行为。因此一次操作结束后立即 Shutdown，不做
常驻连接，把窗口压到最短。

思路参考 xiaoyuyu6420/wallpaper-engine-cleaner 的 steamapi 模块（MIT）；
本实现改用 WE 自带的 dll、用锁串行化调用，并以轮询订阅列表代替"信任返回值"。
"""

import ctypes
import locale
import os
import subprocess
import sys
import threading
import time
from ctypes import c_bool, c_int, c_uint, c_uint64, c_void_p, POINTER
from datetime import datetime

from . import core

WE_APPID = '431960'
APPID_FILE = 'steam_appid.txt'
STEAM_PROCESS = 'steam.exe'

# 接口获取器按版本号从高往低探测：不同年代的 SDK 导出的版本不同，
# 取第一个存在的即可（新版本仍是同一套方法）。
_UGC_VERSIONS = tuple(f'SteamAPI_SteamUGC_v{n:03d}' for n in range(30, 10, -1))

_REQUIRED_EXPORTS = (
    'SteamAPI_Shutdown',
    'SteamAPI_ISteamUGC_GetNumSubscribedItems',
    'SteamAPI_ISteamUGC_GetSubscribedItems',
    'SteamAPI_ISteamUGC_UnsubscribeItem',
    'SteamAPI_ISteamUGC_SubscribeItem',
)

# 失败原因码：面板据此给出"下一步"，而不是一句"连接失败"
REASON_OK = 'ok'
REASON_UNSUPPORTED = 'unsupported'
REASON_STEAM_MISSING = 'steam_not_found'
REASON_STEAM_OFFLINE = 'steam_not_running'
REASON_NOT_LOGGED_IN = 'not_logged_in'
REASON_NO_DLL = 'no_dll'
REASON_DLL_INCOMPATIBLE = 'dll_incompatible'
REASON_INIT_FAILED = 'init_failed'
REASON_UGC_UNAVAILABLE = 'ugc_unavailable'

REASON_HINTS = {
    REASON_UNSUPPORTED: '该功能仅支持 Windows',
    REASON_STEAM_MISSING: '没有找到 Steam 安装目录，请确认 Steam 已安装',
    REASON_STEAM_OFFLINE: 'Steam 客户端没有在运行，启动并登录后即可使用',
    REASON_NOT_LOGGED_IN: 'Steam 已启动但还没登录，请在客户端里登录后重试',
    REASON_NO_DLL: '没有找到可用的 steam_api64.dll，本机至少需要装有一个 Steam 游戏',
    REASON_DLL_INCOMPATIBLE: '找到的 steam_api64.dll 无法使用，已自动尝试其它文件',
    REASON_INIT_FAILED: 'Steam 拒绝了这次连接',
    REASON_UGC_UNAVAILABLE: '拿不到创意工坊接口，可能是这份 steam_api64.dll 版本过旧',
}

# Steamworks 的接口指针与 dll 句柄都是进程级的，所有调用都用同一把锁串行化
_lock = threading.RLock()
_DLL = None
_UGC = None
_ACTIVE_DLL_PATH = ''


def supported():
    """该功能是否在当前系统上可用"""
    return sys.platform == 'win32'


def _load_dll(path):
    """加载 dll；单独抽出来是为了让测试能替换掉真实的 ctypes.CDLL"""
    return ctypes.CDLL(path)


def _read_appid(path):
    """读取 appid 文件的内容，读不到返回空字符串"""
    try:
        with open(path, 'r', encoding='ascii', errors='replace') as f:
            return f.read().strip()
    except OSError:
        return ''


def _we_bin_dir(json_path, workshop_dir):
    """推导 Wallpaper Engine 的 bin 目录

    两条线索：缓存文件本身就在 WE 的 bin 里；内容目录则是
    <库>\\steamapps\\workshop\\content\\431960，往上三层就是 <库>\\steamapps。
    """
    if json_path:
        directory = os.path.dirname(os.path.normpath(json_path))
        if os.path.basename(directory).lower() == 'bin':
            return directory
    if workshop_dir:
        normalized = os.path.normpath(workshop_dir)
        if os.path.basename(normalized) == WE_APPID:
            steamapps = os.path.dirname(os.path.dirname(os.path.dirname(normalized)))
            return os.path.join(steamapps, 'common', 'wallpaper_engine', 'bin')
    return ''


def find_dll_candidates(json_path='', workshop_dir='', override=''):
    """按优先级列出可用的 steam_api64.dll

    1. 配置里手动指定的（steam_dll_path，可以是文件也可以是目录）
    2. Wallpaper Engine 自带的那份：同产品、同 appid，且目录里已有正确的 appid 文件
    3. 其它已安装游戏自带的（兜底，搜索范围：游戏根目录、bin\\、一层子目录）
    """
    found = []
    seen = set()

    def add(path):
        if not path:
            return
        candidate = os.path.normpath(path)
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, 'steam_api64.dll')
        if not os.path.isfile(candidate):
            return
        key = os.path.normcase(candidate)
        if key in seen:
            return
        seen.add(key)
        found.append(candidate)

    add(override)
    we_bin = _we_bin_dir(json_path, workshop_dir)
    if we_bin:
        add(os.path.join(we_bin, 'steam_api64.dll'))

    for library in core.get_steam_libraries():
        common = os.path.join(library, 'steamapps', 'common')
        if not os.path.isdir(common):
            continue
        try:
            games = sorted(os.listdir(common))
        except OSError:
            continue
        for game in games:
            base = os.path.join(common, game)
            if not os.path.isdir(base):
                continue
            add(os.path.join(base, 'steam_api64.dll'))
            add(os.path.join(base, 'bin', 'steam_api64.dll'))
            try:
                subs = sorted(os.listdir(base))
            except OSError:
                continue
            for sub in subs:
                add(os.path.join(base, sub, 'steam_api64.dll'))
    return found


def _appid_context(dll_path):
    """准备一个含正确 steam_appid.txt 的工作目录，返回 (目录, 说明)

    优先复用 dll 同目录已有的正确文件（Wallpaper Engine 自带的就是），零写入；
    否则在程序自己的数据目录下写一份 steam_ctx/steam_appid.txt。
    两种情况都只动我们自己的东西——第三方游戏目录永远不碰。
    """
    dll_dir = os.path.dirname(os.path.abspath(dll_path))
    if _read_appid(os.path.join(dll_dir, APPID_FILE)) == WE_APPID:
        return dll_dir, ''

    ctx = os.path.join(core.script_dir, 'steam_ctx')
    try:
        core.ensure_dir(ctx)
        marker = os.path.join(ctx, APPID_FILE)
        if _read_appid(marker) != WE_APPID:
            with open(marker, 'w', encoding='ascii') as f:
                f.write(WE_APPID + '\n')
    except OSError as e:
        return '', f'无法准备 appid 文件: {e}'
    return ctx, ''


def steam_running():
    """Steam 客户端进程是否在运行；查询不了时按"可能在运行"处理，免得误导用户"""
    if not supported():
        return False
    try:
        output = subprocess.run(
            ['tasklist', '/FO', 'CSV', '/NH'],
            # 打包后的 exe（console=False）自己没有控制台，这时拉起 tasklist 这类控制台程序，
            # Windows 会给它新建一个终端窗口：每次检测都在屏幕上闪一下，还会把前台焦点抢走。
            # 加个创建标志就行，判定不受影响——输出走管道，本来就不需要那个控制台。
            # 该常量只有 Windows 上有，安全的前提是上面的 supported() 已经拦住了其它平台。
            creationflags=subprocess.CREATE_NO_WINDOW,
            # 必须显式给 encoding 与 errors：tasklist 按系统 OEM 代码页输出，
            # 而 text=True 默认按 UTF-8 解（本机 Python 开了 UTF-8 模式），进程名里
            # 只要有一个非 ASCII 字符就会在读取线程里抛 UnicodeDecodeError。
            # 那个异常发生在子线程，主线程只会拿到空输出，表现为"偶尔检测不到 Steam"，
            # 同时日志里每次留一段看不懂的回溯。按本地代码页解、坏字节替换掉即可——
            # 这里只做 ASCII 子串匹配，替换不影响判定。
            capture_output=True, text=True,
            encoding=locale.getpreferredencoding(False), errors='replace',
            timeout=15,
        ).stdout or ''
    except Exception:
        return True
    return f'"{STEAM_PROCESS}"' in output.lower()


def _init_api(dll):
    """初始化 Steamworks，返回 (是否成功, 失败说明)

    按 InitFlat → InitSafe 的顺序尝试：InitFlat 能把 Steam 给出的错误原文带回来
    （面板据此说清"为什么连不上"），但**不是每份 dll 都认它**——实测 Wallpaper
    Engine 自带的那份 InitFlat 直接返回失败、InitSafe 才成功，所以同一份 dll 里
    也要继续往下试，任何一个成功就算连上，全失败时才报第一条说明。

    不用 SteamAPI_Init：它在失败时会弹一个模态对话框，会把没有控制台的进程卡住。
    """
    errors = []

    init_flat = getattr(dll, 'SteamAPI_InitFlat', None)
    if init_flat is not None:
        buffer = ctypes.create_string_buffer(1024)
        init_flat.restype = c_bool
        # char* 用 POINTER(c_char) 而不是 c_char_p：前者按地址传缓冲区，
        # Steam 写进来的错误原文才能被下面读出来
        init_flat.argtypes = [ctypes.POINTER(ctypes.c_char)]
        if init_flat(buffer):
            return True, ''
        message = buffer.value.decode('utf-8', 'replace').strip()
        errors.append(message or 'SteamAPI_InitFlat 返回失败')

    init_safe = getattr(dll, 'SteamAPI_InitSafe', None)
    if init_safe is not None:
        init_safe.restype = c_bool
        init_safe.argtypes = []
        if init_safe():
            return True, ''
        errors.append('SteamAPI_InitSafe 返回失败')

    return False, errors[0] if errors else '这份 dll 没有可用的初始化接口'


def _shutdown_dll(dll):
    try:
        shutdown = getattr(dll, 'SteamAPI_Shutdown', None)
        if shutdown is not None:
            shutdown.restype = None
            shutdown.argtypes = []
            shutdown()
    except Exception:
        pass


def _bind_signatures(dll):
    """按 flat API 的原型绑定参数与返回值类型

    ctypes 默认把返回值当 32 位 int，句柄/指针会被截断，必须逐个显式声明。
    """
    dll.SteamAPI_ISteamUGC_GetNumSubscribedItems.restype = c_uint
    dll.SteamAPI_ISteamUGC_GetNumSubscribedItems.argtypes = [c_void_p]
    dll.SteamAPI_ISteamUGC_GetSubscribedItems.restype = c_uint
    dll.SteamAPI_ISteamUGC_GetSubscribedItems.argtypes = [c_void_p, POINTER(c_uint64), c_uint]
    dll.SteamAPI_ISteamUGC_UnsubscribeItem.restype = c_bool
    dll.SteamAPI_ISteamUGC_UnsubscribeItem.argtypes = [c_void_p, c_uint64]
    dll.SteamAPI_ISteamUGC_SubscribeItem.restype = c_bool
    dll.SteamAPI_ISteamUGC_SubscribeItem.argtypes = [c_void_p, c_uint64]


_INIT_FUNCTIONS = ('SteamAPI_InitFlat', 'SteamAPI_InitSafe')

# Steam 初始化失败时返回的原文里出现这些词，基本就是"没人登录"
_LOGIN_HINTS = ('log in', 'logged in', 'loggedin', 'no user', 'no valid user', 'user')


def _try_connect(dll_path):
    """尝试用一份 dll 连上 Steam，返回 (是否成功, 原因码, 说明)"""
    global _DLL, _UGC

    ctx_dir, problem = _appid_context(dll_path)
    if not ctx_dir:
        return False, REASON_INIT_FAILED, problem

    name = os.path.basename(dll_path)
    previous_cwd = os.getcwd()
    try:
        # 初始化阶段 dll 会从工作目录找 steam_appid.txt；环境变量作为额外保险
        os.environ['SteamAppId'] = WE_APPID
        os.chdir(ctx_dir)
        try:
            dll = _load_dll(dll_path)
        except OSError as e:
            return False, REASON_DLL_INCOMPATIBLE, f'无法加载 {name}：{e}'

        missing = [item for item in _REQUIRED_EXPORTS if getattr(dll, item, None) is None]
        if missing:
            return False, REASON_DLL_INCOMPATIBLE, f'{name} 缺少接口：{", ".join(missing[:3])}'
        if not any(getattr(dll, item, None) is not None for item in _INIT_FUNCTIONS):
            return False, REASON_DLL_INCOMPATIBLE, f'{name} 里没有可用的初始化接口'

        ok, detail = _init_api(dll)
        if not ok:
            return False, REASON_INIT_FAILED, f'{name}：{detail}'

        accessor = None
        for version in _UGC_VERSIONS:
            accessor = getattr(dll, version, None)
            if accessor is not None:
                break
        if accessor is None:
            _shutdown_dll(dll)
            return False, REASON_UGC_UNAVAILABLE, f'{name} 里没有可用的 ISteamUGC 接口'

        accessor.restype = c_void_p
        accessor.argtypes = []
        ugc = accessor()
        if not ugc:
            _shutdown_dll(dll)
            return False, REASON_UGC_UNAVAILABLE, '拿不到 ISteamUGC 接口指针'

        _bind_signatures(dll)
        _DLL, _UGC = dll, ugc
        return True, REASON_OK, dll_path
    finally:
        try:
            os.chdir(previous_cwd)
        except OSError:
            pass


def connect(json_path='', workshop_dir='', dll_override=''):
    """连接运行中的 Steam，返回 (是否成功, 原因码, 说明)

    重复调用安全：已经连上时直接返回成功。任何一步失败都会继续试下一份 dll，
    全部失败后再判断"Steam 根本没运行"这类更贴近用户的原因。
    """
    global _ACTIVE_DLL_PATH

    if not supported():
        return False, REASON_UNSUPPORTED, REASON_HINTS[REASON_UNSUPPORTED]

    with _lock:
        if _UGC:
            return True, REASON_OK, _ACTIVE_DLL_PATH

        candidates = find_dll_candidates(json_path, workshop_dir, dll_override)
        if not candidates:
            try:
                libraries = core.get_steam_libraries()
            except Exception:
                libraries = []
            if not libraries:
                return False, REASON_STEAM_MISSING, REASON_HINTS[REASON_STEAM_MISSING]
            detail = REASON_HINTS[REASON_NO_DLL]
            if dll_override:
                detail = f'配置里指定的 steam_dll_path 不可用：{dll_override}'
            return False, REASON_NO_DLL, detail

        reason, detail = REASON_DLL_INCOMPATIBLE, ''
        for path in candidates:
            ok, reason, detail = _try_connect(path)
            if ok:
                _ACTIVE_DLL_PATH = path
                core.logger.debug('已连接 Steam（使用 %s）', path)
                return True, REASON_OK, path

        if not steam_running():
            return False, REASON_STEAM_OFFLINE, REASON_HINTS[REASON_STEAM_OFFLINE]
        if reason == REASON_INIT_FAILED and any(
                hint in detail.lower() for hint in _LOGIN_HINTS):
            return False, REASON_NOT_LOGGED_IN, f'{REASON_HINTS[REASON_NOT_LOGGED_IN]}（{detail}）'
        return False, reason, detail or REASON_HINTS.get(reason, '连接失败')


def shutdown():
    """断开与 Steam 的连接（Steam 随之不再认为本程序在运行 WE）"""
    global _DLL, _UGC, _ACTIVE_DLL_PATH
    with _lock:
        dll, _DLL, _UGC, _ACTIVE_DLL_PATH = _DLL, None, None, ''
        if dll is not None:
            _shutdown_dll(dll)


def _require_connected():
    if not _UGC:
        raise RuntimeError('尚未连接 Steam')


def _as_id(wid):
    """把 workshop ID 转成整数，非法返回 None"""
    try:
        return c_uint64(int(str(wid)))
    except (TypeError, ValueError):
        return None


def get_subscribed():
    """账号当前的订阅 ID 集合（来自运行中的 Steam，比本地记录权威）"""
    with _lock:
        _require_connected()
        total = int(_DLL.SteamAPI_ISteamUGC_GetNumSubscribedItems(_UGC))
        if total <= 0:
            return set()
        array = (c_uint64 * total)()
        got = int(_DLL.SteamAPI_ISteamUGC_GetSubscribedItems(_UGC, array, total))
        return {str(array[i]) for i in range(min(got, total))}


def subscribed_count():
    """当前订阅数量；未连接或查询失败返回 None"""
    try:
        return len(get_subscribed())
    except Exception:
        return None


def _call_item(func_name, wid):
    with _lock:
        _require_connected()
        item = _as_id(wid)
        if item is None:
            return False
        return bool(getattr(_DLL, func_name)(_UGC, item))


def unsubscribe(wid):
    """取消订阅。返回 True 只代表 Steam 受理了这次调用，是否生效要靠轮询确认"""
    return _call_item('SteamAPI_ISteamUGC_UnsubscribeItem', wid)


def subscribe(wid):
    """重新订阅。返回 True 只代表 Steam 受理了这次调用，随后会自动下载"""
    return _call_item('SteamAPI_ISteamUGC_SubscribeItem', wid)


def _poll_until(wids, done, timeout, interval):
    """轮询订阅列表直到 done(当前订阅集合) 覆盖全部 wids，或者超时"""
    targets = {str(w) for w in wids}
    if not targets:
        return set()
    deadline = time.monotonic() + max(0.0, timeout)
    settled = set()
    while True:
        try:
            subscribed = get_subscribed()
        except Exception:
            break
        settled |= {wid for wid in targets - settled if done(subscribed, wid)}
        if settled >= targets:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    return settled


def confirm_unsubscribed(wids, timeout=8.0, interval=0.25):
    """轮询确认这些 ID 已经不在订阅列表里，返回确认成功的集合

    UnsubscribeItem 是异步的：返回 true 只表示 Steam 受理了请求。
    """
    return _poll_until(wids, lambda subscribed, wid: wid not in subscribed, timeout, interval)


def confirm_subscribed(wids, timeout=8.0, interval=0.25):
    """轮询确认这些 ID 已经回到订阅列表里，返回确认成功的集合"""
    return _poll_until(wids, lambda subscribed, wid: wid in subscribed, timeout, interval)


def hint_for(reason):
    """原因码对应的中文一句话说明"""
    return REASON_HINTS.get(reason, '连接 Steam 失败')


def probe(json_path='', workshop_dir='', dll_override=''):
    """连接一次并回报可用性，无论成败都立刻断开

    面板据此显示状态徽标：连上了就顺带读出订阅数量——那是"确实连到了你的
    账号"最直接的证据；连不上则给出原因码与说明，供面板展示"下一步"。

    返回 {'status', 'reason', 'detail', 'hint', 'subscribed_count', 'dll_path', 'checked_at'}
    """
    result = {
        'status': 'unavailable',
        'reason': REASON_INIT_FAILED,
        'detail': '',
        'hint': '',
        'subscribed_count': None,
        'dll_path': '',
        'checked_at': datetime.now().strftime('%H:%M:%S'),
    }
    try:
        ok, reason, detail = connect(json_path, workshop_dir, dll_override)
        if ok:
            count = subscribed_count()
            if count is None:
                ok, reason, detail = False, REASON_UGC_UNAVAILABLE, '连接到 Steam 后读不出订阅列表'
            else:
                result.update({
                    'status': 'ok',
                    'reason': REASON_OK,
                    'detail': '',
                    'subscribed_count': count,
                    'dll_path': detail,
                })
        if not ok:
            result.update({
                'status': 'unavailable',
                'reason': reason,
                'detail': detail,
                'hint': hint_for(reason),
            })
    except Exception as e:  # 探测本身绝不能把面板带崩
        result.update({'status': 'unavailable', 'reason': REASON_INIT_FAILED,
                       'detail': str(e), 'hint': hint_for(REASON_INIT_FAILED)})
        core.logger.debug('Steam 探测失败: %s', e)
    finally:
        shutdown()
    return result
