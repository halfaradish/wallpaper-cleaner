"""界面偏好接口（/api/prefs）的测试

主题偏好存在服务端而不是 localStorage：桌面模式的端口由系统分配，每次启动都不同，
而 localStorage 按来源隔离——打包后的 exe 默认就是桌面模式，只放本地存储等于每次
启动都重置。所以这个接口要保证两件事：存得住，以及读不出来时不让面板打不开。

运行：python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import core, web


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


class PrefsEndpointTestCase(unittest.TestCase):
    """把 prefs.json 关进临时目录：core.prefs_path 是模块级常量，这里整个换掉"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wc-prefs-test-')
        self._saved = core.prefs_path
        core.prefs_path = os.path.join(self.tmp, 'prefs.json')

        self.state = web.PanelState()
        self.server = web.PanelServer(('127.0.0.1', 0), web.PanelHandler, self.state)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        core.prefs_path = self._saved
        rmtree(self.tmp)

    # --- 辅助 ---

    def get(self, path):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}')
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def post(self, path, payload, token=None):
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-Panel-Token'] = token
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}',
            data=json.dumps(payload).encode('utf-8'), method='POST', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def write_prefs(self, text):
        with open(core.prefs_path, 'w', encoding='utf-8') as f:
            f.write(text)


class TestPrefsEndpoint(PrefsEndpointTestCase):
    def test_defaults_to_auto(self):
        status, body = self.get('/api/prefs')
        self.assertEqual(status, 200)
        self.assertEqual(body['theme'], 'auto')
        self.assertEqual(body['themes'], ['auto', 'light', 'dark'])

    def test_saves_and_reads_back(self):
        status, body = self.post('/api/prefs', {'theme': 'dark'}, token=self.state.token)
        self.assertEqual(status, 200)
        self.assertEqual(body['theme'], 'dark')

        _status, body = self.get('/api/prefs')
        self.assertEqual(body['theme'], 'dark')

        # 落到磁盘上才算数：桌面模式换端口就是换来源，只有文件能跨过去
        with open(core.prefs_path, 'r', encoding='utf-8') as f:
            self.assertEqual(json.load(f)['theme'], 'dark')

    def test_cycles_through_all_three(self):
        for theme in ('light', 'dark', 'auto'):
            status, _body = self.post('/api/prefs', {'theme': theme}, token=self.state.token)
            self.assertEqual(status, 200, theme)
            _status, body = self.get('/api/prefs')
            self.assertEqual(body['theme'], theme)

    def test_rejects_an_unknown_theme(self):
        status, body = self.post('/api/prefs', {'theme': 'solarized'}, token=self.state.token)
        self.assertEqual(status, 400)
        self.assertIn('主题', body['error'])
        # 被拒之后不该留下任何东西
        self.assertFalse(os.path.exists(core.prefs_path))

    def test_rejects_a_missing_theme(self):
        status, _body = self.post('/api/prefs', {}, token=self.state.token)
        self.assertEqual(status, 400)

    def test_requires_the_panel_token(self):
        status, _body = self.post('/api/prefs', {'theme': 'dark'})
        self.assertEqual(status, 403)

    def test_corrupt_file_falls_back_instead_of_failing(self):
        """prefs.json 坏了不该让面板打不开——它只是一个显示偏好"""
        self.write_prefs('{ 这不是 JSON')
        status, body = self.get('/api/prefs')
        self.assertEqual(status, 200)
        self.assertEqual(body['theme'], 'auto')

        # 而且还能照常写入，把坏文件覆盖掉
        status, _body = self.post('/api/prefs', {'theme': 'light'}, token=self.state.token)
        self.assertEqual(status, 200)
        _status, body = self.get('/api/prefs')
        self.assertEqual(body['theme'], 'light')

    def test_non_object_json_falls_back(self):
        self.write_prefs('["不是对象"]')
        _status, body = self.get('/api/prefs')
        self.assertEqual(body['theme'], 'auto')

    def test_unknown_stored_value_falls_back(self):
        """手改过的 prefs.json 里放了个没见过的值，也要退到 auto 而不是原样透给前端"""
        self.write_prefs('{"theme": "rainbow"}')
        _status, body = self.get('/api/prefs')
        self.assertEqual(body['theme'], 'auto')

    def test_other_keys_survive_a_write(self):
        """写入是"读出来改一个键再写回"，别把以后加的偏好顺手清掉"""
        self.write_prefs('{"theme": "light", "future": 42}')
        self.post('/api/prefs', {'theme': 'dark'}, token=self.state.token)
        with open(core.prefs_path, 'r', encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'theme': 'dark', 'future': 42})


class TestPrefsStorage(unittest.TestCase):
    """直接测 core 的读写，不经 HTTP"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wc-prefs-core-')
        self._saved_path = core.prefs_path
        self._saved_dir = core.script_dir
        core.prefs_path = os.path.join(self.tmp, 'prefs.json')
        core.script_dir = self.tmp

    def tearDown(self):
        core.prefs_path = self._saved_path
        core.script_dir = self._saved_dir
        rmtree(self.tmp)

    def test_missing_file_returns_empty(self):
        self.assertEqual(core.load_prefs(), {})

    def test_roundtrip(self):
        self.assertTrue(core.save_prefs({'theme': 'dark'}))
        self.assertEqual(core.load_prefs(), {'theme': 'dark'})

    def test_write_failure_returns_false_without_raising(self):
        """目录不可写时只返回 False，不抛异常——调用方要能继续把界面切过去"""
        core.prefs_path = os.path.join(self.tmp, 'no-such-dir', 'prefs.json')
        with mock.patch('os.makedirs', side_effect=OSError('拒绝访问')):
            self.assertFalse(core.save_prefs({'theme': 'dark'}))


class TestThemeInjection(PrefsEndpointTestCase):
    """主题要写进首页的第一帧，否则会先闪一下浅色"""

    def fetch_index(self):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/')
        with urllib.request.urlopen(request, timeout=5) as res:
            return res.read().decode('utf-8')

    def test_default_is_injected(self):
        html = self.fetch_index()
        self.assertNotIn('__PANEL_THEME_VALUE__', html, '占位符没被替换')
        self.assertIn('<html lang="zh-CN" data-theme="auto">', html)

    def test_stored_theme_is_injected(self):
        self.post('/api/prefs', {'theme': 'dark'}, token=self.state.token)
        self.assertIn('<html lang="zh-CN" data-theme="dark">', self.fetch_index())

    def test_a_broken_file_still_yields_a_valid_attribute(self):
        """坏文件不能让首页带出一个 CSS 里没有的 data-theme 值"""
        self.write_prefs('{"theme": "rainbow"}')
        html = self.fetch_index()
        self.assertIn('data-theme="auto"', html)


if __name__ == '__main__':
    unittest.main()
