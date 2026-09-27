"""steamapi 模块单元测试：全部离线，用假 dll 替换真实的 ctypes.CDLL

不碰真实 Steam、不加载真实 dll、不写任何第三方目录——这里验证的是：
- 候选 dll 的优先级与去重；
- appid 上下文的阶梯（优先复用 dll 同目录已有的文件，绝不写别人的目录）；
- 连接失败的原因分类（面板靠它给出"下一步"）；
- 订阅列表读取与取消/重新订阅的调用参数、轮询确认。

运行：python -m unittest discover -s tests -v
"""

import ctypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import core, steamapi


def rmtree(path):
    """清掉只读位再删（Windows 下 rmtree 遇到只读文件会失败）"""
    if not os.path.exists(path):
        return
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in [dirpath] + [os.path.join(dirpath, f) for f in filenames]:
            try:
                os.chmod(name, stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


class FakeFunc:
    """假的 flat API 函数：可设 restype/argtypes，调用记录参数并返回固定值"""

    def __init__(self, value=True):
        self.value = value
        self.restype = None
        self.argtypes = None
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        return self.value


class FakeSubscribedItems(FakeFunc):
    """GetSubscribedItems：把 ID 填进调用方给的数组，返回写入条数"""

    def __init__(self, ids=()):
        super().__init__(0)
        self.ids = [int(i) for i in ids]

    def __call__(self, ugc, array, capacity):
        self.calls.append((ugc, array, capacity))
        limit = min(len(self.ids), int(capacity))
        for index in range(limit):
            array[index] = self.ids[index]
        return limit


class FakeInitFlat:
    """SteamAPI_InitFlat：按 C 的约定把错误原文写进调用方的缓冲区"""

    def __init__(self, ok=True, message=''):
        self.ok = ok
        self.message = message
        self.restype = None
        self.argtypes = None
        self.calls = 0

    def __call__(self, buffer):
        self.calls += 1
        if self.message:
            buffer.value = self.message.encode('utf-8')
        return self.ok


class FakeUgcAccessor:
    """SteamAPI_SteamUGC_vXXX：返回一个非空的"接口指针" """

    def __init__(self, pointer=0x1234):
        self.pointer = pointer
        self.restype = None
        self.argtypes = None

    def __call__(self):
        return self.pointer


class FakeDLL:
    """假 dll：只有被显式给出的导出存在，其余 getattr 抛 AttributeError"""

    def __init__(self, exports):
        self._exports = exports
        self.unloaded = False

    def __getattr__(self, name):
        try:
            return self._exports[name]
        except KeyError:
            raise AttributeError(name)


def make_dll(ids=(), init_ok=True, init_message='', with_init='flat', ugc_pointer=0x1234,
             init_safe_ok=None):
    """造一份"能用"的假 dll

    init_safe_ok 用来模拟"InitFlat 失败但 InitSafe 成功"——Wallpaper Engine
    自带的那份 dll 实测就是这种：同一份 dll 里必须继续往下试。
    """
    exports = {
        'SteamAPI_Shutdown': FakeFunc(None),
        'SteamAPI_ISteamUGC_GetNumSubscribedItems': FakeFunc(len(ids)),
        'SteamAPI_ISteamUGC_GetSubscribedItems': FakeSubscribedItems(ids),
        'SteamAPI_ISteamUGC_UnsubscribeItem': FakeFunc(True),
        'SteamAPI_ISteamUGC_SubscribeItem': FakeFunc(True),
        'SteamAPI_SteamUGC_v020': FakeUgcAccessor(ugc_pointer),
    }
    if with_init == 'flat':
        exports['SteamAPI_InitFlat'] = FakeInitFlat(init_ok, init_message)
    elif with_init == 'safe':
        exports['SteamAPI_InitSafe'] = FakeFunc(init_ok)
    elif with_init == 'both':
        exports['SteamAPI_InitFlat'] = FakeInitFlat(init_ok, init_message)
        exports['SteamAPI_InitSafe'] = FakeFunc(
            init_ok if init_safe_ok is None else init_safe_ok)
    return FakeDLL(exports)


class SteamApiSandbox(unittest.TestCase):
    """临时目录 + core 全局路径重定向 + 模块状态清理"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wc-steamapi-test-')
        self._saved_script_dir = core.script_dir
        core.script_dir = self.tmp
        self.we_bin = os.path.join(
            self.tmp, 'steamapps', 'common', 'wallpaper_engine', 'bin')
        os.makedirs(self.we_bin)

    def tearDown(self):
        steamapi.shutdown()
        core.script_dir = self._saved_script_dir
        rmtree(self.tmp)

    # --- 沙箱辅助 ---

    def write_dll(self, directory, name='steam_api64.dll'):
        """放一个占位 dll 文件（内容无关紧要，加载被 patch 掉了）"""
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, 'wb') as f:
            f.write(b'MZ')
        return path

    def write_appid(self, directory, value=steamapi.WE_APPID):
        path = os.path.join(directory, steamapi.APPID_FILE)
        with open(path, 'w', encoding='ascii') as f:
            f.write(value + '\n')
        return path

    def workshop_dir(self):
        """造一个像样的 workshop 内容目录，供反推 WE 的 bin"""
        path = os.path.join(self.tmp, 'steamapps', 'workshop', 'content', steamapi.WE_APPID)
        os.makedirs(path, exist_ok=True)
        return path


class TestDllDiscovery(SteamApiSandbox):
    def test_we_bin_dir_from_workshop_dir_and_cache_path(self):
        workshop = self.workshop_dir()
        self.assertEqual(steamapi._we_bin_dir('', workshop), self.we_bin)

        cache = os.path.join(self.we_bin, 'workshopcache.json')
        self.assertEqual(steamapi._we_bin_dir(cache, ''), self.we_bin)
        self.assertEqual(steamapi._we_bin_dir('', ''), '')

    def test_candidates_order_is_override_then_we_then_other_games(self):
        override_dir = os.path.join(self.tmp, 'my-dll')
        override = self.write_dll(override_dir)
        we_dll = self.write_dll(self.we_bin)
        other_dll = self.write_dll(
            os.path.join(self.tmp, 'steamapps', 'common', 'some-game', 'bin'))

        with mock.patch.object(core, 'get_steam_libraries', return_value=[self.tmp]):
            found = steamapi.find_dll_candidates('', self.workshop_dir(), override)

        self.assertEqual(found[0], override)
        self.assertEqual(found[1], we_dll)
        self.assertIn(other_dll, found)

    def test_candidates_skip_missing_and_dedupe(self):
        we_dll = self.write_dll(self.we_bin)
        with mock.patch.object(core, 'get_steam_libraries', return_value=[self.tmp]):
            found = steamapi.find_dll_candidates(
                '', self.workshop_dir(), os.path.join(self.tmp, 'nope', 'steam_api64.dll'))

        self.assertEqual(found, [we_dll])

    def test_candidates_accept_a_directory_as_override(self):
        override = self.write_dll(os.path.join(self.tmp, 'my-dll'))
        with mock.patch.object(core, 'get_steam_libraries', return_value=[]):
            found = steamapi.find_dll_candidates('', '', os.path.dirname(override))
        self.assertEqual(found, [override])


class TestAppidContext(SteamApiSandbox):
    def test_reuses_appid_file_next_to_the_dll(self):
        """Wallpaper Engine 自带的那份 appid 文件是现成的：零写入"""
        dll = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)

        ctx, problem = steamapi._appid_context(dll)

        self.assertEqual(ctx, self.we_bin)
        self.assertEqual(problem, '')
        self.assertEqual(os.listdir(self.tmp), ['steamapps'])  # 没有新建任何东西

    def test_writes_into_our_own_directory_when_dll_dir_has_no_appid(self):
        """没有现成的就写在自己数据目录下，绝不往别人的目录写"""
        other = os.path.join(self.tmp, 'steamapps', 'common', 'some-game')
        dll = self.write_dll(other)
        before = sorted(os.listdir(other))

        ctx, problem = steamapi._appid_context(dll)

        self.assertEqual(problem, '')
        self.assertEqual(ctx, os.path.join(self.tmp, 'steam_ctx'))
        self.assertEqual(sorted(os.listdir(other)), before)
        with open(os.path.join(ctx, steamapi.APPID_FILE), encoding='ascii') as f:
            self.assertEqual(f.read().strip(), steamapi.WE_APPID)

    def test_wrong_appid_in_dll_dir_is_not_reused(self):
        other = os.path.join(self.tmp, 'some-game')
        dll = self.write_dll(other)
        self.write_appid(other, '480')

        ctx, _problem = steamapi._appid_context(dll)

        self.assertEqual(ctx, os.path.join(self.tmp, 'steam_ctx'))
        with open(os.path.join(other, steamapi.APPID_FILE), encoding='ascii') as f:
            self.assertEqual(f.read().strip(), '480')  # 别人那份没被改


class TestConnect(SteamApiSandbox):
    def connect(self, dlls, libraries=None, running=True):
        """按顺序把 dlls 交给 connect；返回 (结果, 加载过的路径列表)"""
        loaded = []

        def loader(path):
            loaded.append(path)
            item = dlls.get(path)
            if item is None:
                raise OSError('不是有效的 dll')
            return item

        with mock.patch.object(steamapi, '_load_dll', side_effect=loader), \
                mock.patch.object(core, 'get_steam_libraries',
                                  return_value=libraries if libraries is not None else [self.tmp]), \
                mock.patch.object(steamapi, 'steam_running', return_value=running):
            result = steamapi.connect('', self.workshop_dir())
        return result, loaded

    def test_connects_with_the_we_dll(self):
        dll_path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)

        (ok, reason, detail), loaded = self.connect({dll_path: make_dll(['111'])}, running=False)

        self.assertTrue(ok)
        self.assertEqual(reason, steamapi.REASON_OK)
        self.assertEqual(detail, dll_path)
        self.assertEqual(loaded, [dll_path])
        self.assertEqual(steamapi.get_subscribed(), {'111'})

    def test_no_steam_installed_is_reported_separately(self):
        (ok, reason, _detail), loaded = self.connect({}, libraries=[])

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_STEAM_MISSING)
        self.assertEqual(loaded, [])

    def test_library_without_any_dll(self):
        (ok, reason, _detail), _loaded = self.connect({})

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_NO_DLL)

    def test_dll_missing_exports_falls_through_to_the_next_one(self):
        broken = self.write_dll(self.we_bin)
        good_dir = os.path.join(self.tmp, 'steamapps', 'common', 'some-game')
        good = self.write_dll(good_dir)
        self.write_appid(good_dir)  # 两份 dll 都不需要写任何东西

        partial = FakeDLL({'SteamAPI_Shutdown': FakeFunc(None)})
        (ok, _reason, detail), loaded = self.connect({broken: partial, good: make_dll(['7'])})

        self.assertTrue(ok)
        self.assertEqual(detail, good)
        self.assertEqual(loaded, [broken, good])

    def test_failure_reason_when_dll_cannot_be_loaded_and_steam_is_off(self):
        broken = self.write_dll(self.we_bin)

        (ok, reason, detail), _loaded = self.connect({broken: None}, running=False)

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_STEAM_OFFLINE)
        self.assertIn('没有在运行', detail)

    def test_init_failure_reports_steams_own_message(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        dll = make_dll(init_ok=False, init_message='No Steam user is logged in')

        (ok, reason, detail), _loaded = self.connect({path: dll})

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_NOT_LOGGED_IN)
        self.assertIn('No Steam user is logged in', detail)

    def test_init_failure_without_a_login_hint_stays_generic(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        dll = make_dll(init_ok=False, init_message='some other problem')

        (ok, reason, detail), _loaded = self.connect({path: dll})

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_INIT_FAILED)
        self.assertIn('some other problem', detail)

    def test_old_dll_without_init_flat_still_works(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)

        (ok, _reason, _detail), _loaded = self.connect({path: make_dll(['5'], with_init='safe')})

        self.assertTrue(ok)
        self.assertEqual(steamapi.get_subscribed(), {'5'})

    def test_init_flat_failure_falls_back_to_init_safe_in_the_same_dll(self):
        """回归（真机实测）：WE 自带的那份 dll InitFlat 返回失败、InitSafe 才成功

        同一份 dll 里必须继续往下试，否则会白白跳过最该用的那份，去借别的游戏的。
        """
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        dll = make_dll(['5'], with_init='both', init_ok=False, init_safe_ok=True)

        (ok, reason, detail), loaded = self.connect({path: dll})

        self.assertTrue(ok, f'{reason} {detail}')
        self.assertEqual(loaded, [path])
        self.assertEqual(steamapi.get_subscribed(), {'5'})

    def test_all_init_functions_failing_reports_the_first_message(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        dll = make_dll(with_init='both', init_ok=False, init_message='Steam 未运行',
                       init_safe_ok=False)

        (ok, reason, detail), _loaded = self.connect({path: dll})

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_INIT_FAILED)
        self.assertIn('Steam 未运行', detail)

    def test_dll_without_any_init_function_is_incompatible(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)

        (ok, reason, _detail), _loaded = self.connect({path: make_dll(with_init='none')})

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_DLL_INCOMPATIBLE)
        self.assertTrue(any('初始化接口' in str(d) for d in [_detail]))

    def test_missing_ugc_accessor_is_reported(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        dll = make_dll()
        del dll._exports['SteamAPI_SteamUGC_v020']

        (ok, reason, _detail), _loaded = self.connect({path: dll})

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_UGC_UNAVAILABLE)

    def test_connect_is_idempotent(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        dll = make_dll(['1'])

        self.connect({path: dll})
        (ok, _reason, _detail), loaded = self.connect({path: dll})

        self.assertTrue(ok)
        self.assertEqual(loaded, [])  # 第二次没有再去加载 dll

    def test_unsupported_platform_is_reported(self):
        with mock.patch.object(steamapi.sys, 'platform', 'linux'):
            ok, reason, detail = steamapi.connect()

        self.assertFalse(ok)
        self.assertEqual(reason, steamapi.REASON_UNSUPPORTED)
        self.assertIn('Windows', detail)

    def test_shutdown_clears_the_connection(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        self.connect({path: make_dll(['1'])})

        steamapi.shutdown()

        with self.assertRaises(RuntimeError):
            steamapi.get_subscribed()


@unittest.skipUnless(sys.platform == 'win32', 'tasklist 与 CREATE_NO_WINDOW 都只在 Windows 上')
class TestSteamRunning(unittest.TestCase):
    """steam_running()：从 tasklist 的输出里找 steam.exe"""

    def run_tasklist(self, stdout='', error=None):
        """替掉 subprocess.run，返回 (steam_running 的结果, 那次调用的 mock)"""
        run = mock.Mock(side_effect=error) if error else mock.Mock(
            return_value=mock.Mock(stdout=stdout))
        with mock.patch.object(steamapi.subprocess, 'run', run):
            return steamapi.steam_running(), run

    def test_detects_the_steam_process(self):
        output = '"steam.exe","1234","Console","1","120,000 K"\n"explorer.exe","5678","Console","1","90,000 K"\n'

        running, _run = self.run_tasklist(output)

        self.assertTrue(running)

    def test_reports_not_running_when_steam_is_absent(self):
        running, _run = self.run_tasklist('"explorer.exe","5678","Console","1","90,000 K"\n')

        self.assertFalse(running)

    def test_tasklist_is_spawned_without_a_console_window(self):
        """回归守卫：打包后的 exe 自己没有控制台，没这个标志时 Windows 会给 tasklist
        新建一个终端窗口——每次 Steam 检测都在屏幕上闪一下、还把前台焦点抢走"""
        _running, run = self.run_tasklist('"steam.exe","1234","Console","1","120,000 K"\n')

        self.assertEqual(run.call_args.args[0], ['tasklist', '/FO', 'CSV', '/NH'])
        self.assertEqual(run.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)

    def test_query_failure_counts_as_running(self):
        # 查不出来时说"没在运行"会误导用户去启动 Steam，所以按"可能在运行"处理
        running, _run = self.run_tasklist(error=OSError('找不到 tasklist'))

        self.assertTrue(running)


class TestSubscriptionCalls(SteamApiSandbox):
    def connect_with(self, ids=()):
        dll = make_dll(ids)
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)
        with mock.patch.object(steamapi, '_load_dll', return_value=dll), \
                mock.patch.object(core, 'get_steam_libraries', return_value=[self.tmp]), \
                mock.patch.object(steamapi, 'steam_running', return_value=True):
            ok, reason, _detail = steamapi.connect('', self.workshop_dir())
        assert ok, reason
        return dll

    def test_unsubscribe_passes_a_numeric_id(self):
        dll = self.connect_with()

        self.assertTrue(steamapi.unsubscribe('3611425904'))

        _ugc, item = dll._exports['SteamAPI_ISteamUGC_UnsubscribeItem'].calls[0]
        self.assertEqual(item.value, 3611425904)

    def test_subscribe_passes_a_numeric_id(self):
        dll = self.connect_with()

        self.assertTrue(steamapi.subscribe('3611425904'))

        _ugc, item = dll._exports['SteamAPI_ISteamUGC_SubscribeItem'].calls[0]
        self.assertEqual(item.value, 3611425904)

    def test_non_numeric_id_is_refused_without_calling_steam(self):
        dll = self.connect_with()

        self.assertFalse(steamapi.unsubscribe('not-a-number'))

        self.assertEqual(dll._exports['SteamAPI_ISteamUGC_UnsubscribeItem'].calls, [])

    def test_confirm_unsubscribed_polls_until_the_item_is_gone(self):
        self.connect_with()
        responses = [{'1', '2'}, {'2'}, {'2'}]

        with mock.patch.object(steamapi, 'get_subscribed',
                               side_effect=lambda: set(responses.pop(0))):
            confirmed = steamapi.confirm_unsubscribed(['1'], timeout=1, interval=0)

        self.assertEqual(confirmed, {'1'})

    def test_confirm_unsubscribed_gives_up_after_the_timeout(self):
        self.connect_with()

        with mock.patch.object(steamapi, 'get_subscribed', return_value={'1'}):
            confirmed = steamapi.confirm_unsubscribed(['1'], timeout=0, interval=0)

        self.assertEqual(confirmed, set())

    def test_confirm_subscribed_waits_for_the_item_to_appear(self):
        self.connect_with()
        responses = [set(), {'9'}]

        with mock.patch.object(steamapi, 'get_subscribed',
                               side_effect=lambda: set(responses.pop(0))):
            confirmed = steamapi.confirm_subscribed(['9'], timeout=1, interval=0)

        self.assertEqual(confirmed, {'9'})


class TestProbe(SteamApiSandbox):
    def test_probe_reports_ok_with_the_subscription_count(self):
        path = self.write_dll(self.we_bin)
        self.write_appid(self.we_bin)

        with mock.patch.object(steamapi, '_load_dll', return_value=make_dll(['1', '2', '3'])), \
                mock.patch.object(core, 'get_steam_libraries', return_value=[self.tmp]), \
                mock.patch.object(steamapi, 'steam_running', return_value=True):
            result = steamapi.probe('', self.workshop_dir())

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['subscribed_count'], 3)
        self.assertEqual(result['dll_path'], path)
        self.assertTrue(result['checked_at'])
        # 探测完就断开，不留常驻连接
        with self.assertRaises(RuntimeError):
            steamapi.get_subscribed()

    def test_probe_reports_the_reason_when_steam_is_missing(self):
        with mock.patch.object(core, 'get_steam_libraries', return_value=[]):
            result = steamapi.probe()

        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['reason'], steamapi.REASON_STEAM_MISSING)
        self.assertTrue(result['hint'])

    def test_probe_never_raises(self):
        with mock.patch.object(steamapi, 'connect', side_effect=RuntimeError('boom')):
            result = steamapi.probe()

        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('boom', result['detail'])


class TestReasonHints(unittest.TestCase):
    def test_every_reason_has_a_hint(self):
        for reason in (steamapi.REASON_OK, steamapi.REASON_UNSUPPORTED,
                       steamapi.REASON_STEAM_MISSING, steamapi.REASON_STEAM_OFFLINE,
                       steamapi.REASON_NOT_LOGGED_IN, steamapi.REASON_NO_DLL,
                       steamapi.REASON_DLL_INCOMPATIBLE, steamapi.REASON_INIT_FAILED,
                       steamapi.REASON_UGC_UNAVAILABLE):
            if reason == steamapi.REASON_OK:
                continue
            self.assertTrue(steamapi.hint_for(reason))

    def test_unknown_reason_falls_back(self):
        self.assertTrue(steamapi.hint_for('something-else'))


if __name__ == '__main__':
    unittest.main()
