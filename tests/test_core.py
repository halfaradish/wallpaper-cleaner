"""core 模块单元测试：全部在临时目录沙箱内进行，不触碰真实配置与真实 Steam 目录

运行：python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import core


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


class SandboxTestCase(unittest.TestCase):
    """把 core 的路径全局量重定向到临时目录，保证测试不会写到项目里"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wc-test-')
        self._saved = {}
        for attr in ('script_dir', 'config_path', 'legacy_config_path', 'log_dir'):
            self._saved[attr] = getattr(core, attr)
        core.script_dir = self.tmp
        core.config_path = os.path.join(self.tmp, 'config.yml')
        core.legacy_config_path = os.path.join(self.tmp, 'config.json')
        core.log_dir = os.path.join(self.tmp, 'logs')

    def tearDown(self):
        for attr, value in self._saved.items():
            setattr(core, attr, value)
        rmtree(self.tmp)

    # --- 沙箱辅助 ---

    def make_workshop(self, subscribed_ids=(), orphans=(), unknown=()):
        """构造伪造的 workshop 目录树，返回 (workshop_dir, json_path, subscriptions)"""
        workshop_dir = os.path.join(self.tmp, '431960')
        os.makedirs(workshop_dir)
        for wid in list(subscribed_ids) + list(orphans) + list(unknown):
            folder = os.path.join(workshop_dir, wid)
            os.makedirs(folder)
            with open(os.path.join(folder, 'data.bin'), 'wb') as f:
                f.write(b'x' * 128)

        subscriptions = {
            wid: {'title': f'壁纸 {wid}', 'size': '1.0 MB'} for wid in subscribed_ids
        }
        json_path = os.path.join(self.tmp, 'workshopcache.json')
        payload = {
            'wallpapers': [
                {'workshopid': wid, 'title': info['title'], 'filesizelabel': info['size']}
                for wid, info in subscriptions.items()
            ]
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return workshop_dir, json_path, subscriptions

    def write(self, path, text, newline=''):
        with open(path, 'w', encoding='utf-8', newline=newline) as f:
            f.write(text)


class TestConfigParsing(SandboxTestCase):
    def test_flat_yaml_with_comments_and_quotes(self):
        text = (
            '# 顶部注释\n'
            '\n'
            'json_path: D:\\Steam\\steamapps\\common\\wallpaper_engine\\bin\\workshopcache.json\n'
            'workshop_dir: "D:\\Program Files\\Steam\\431960"\n'
            "quoted: 'a:b#c'\n"
            'inline: C:\\x\\y  # 行内注释\n'
        )
        config = core.parse_config_text(text)
        self.assertEqual(
            config['json_path'],
            r'D:\Steam\steamapps\common\wallpaper_engine\bin\workshopcache.json',
        )
        self.assertEqual(config['workshop_dir'], r'D:\Program Files\Steam\431960')
        # 引号内不处理转义，原样返回
        self.assertEqual(config['quoted'], 'a:b#c')
        self.assertEqual(config['inline'], r'C:\x\y')

    def test_json_still_supported(self):
        text = '{"json_path": "a.json", "workshop_dir": "b", "_comment": "忽略"}'
        config = core.parse_config_text(text)
        self.assertEqual(config['json_path'], 'a.json')
        self.assertEqual(config['workshop_dir'], 'b')

    def test_broken_yaml_raises(self):
        with self.assertRaises(ValueError):
            core.parse_config_text('json_path ??? oops\n')

    def test_unclosed_quote_raises(self):
        with self.assertRaises(ValueError):
            core.parse_config_text('json_path: "unterminated\n')

    def test_relative_and_forward_slash_paths(self):
        self.assertEqual(
            core.resolve_path('sub/dir'),
            os.path.normpath(os.path.join(self.tmp, 'sub', 'dir')),
        )
        self.assertEqual(core.resolve_path(r'D:\abs\path'), os.path.normpath(r'D:\abs\path'))
        self.assertEqual(core.resolve_path(''), '')


class TestWriteConfig(SandboxTestCase):
    def test_preserves_comments_and_crlf(self):
        path = core.config_path
        with open(path, 'wb') as f:
            f.write('# 这是注释\r\njson_path: old.json\r\nworkshop_dir: old-dir\r\n# 结尾注释\r\n'.encode('utf-8'))

        core.write_config_values({'json_path': r'D:\new\wc.json'}, path)

        with open(path, 'r', encoding='utf-8', newline='') as f:
            text = f.read()
        self.assertIn('# 这是注释\r\n', text)
        self.assertIn('# 结尾注释\r\n', text)
        self.assertIn('json_path: D:\\new\\wc.json\r\n', text)
        self.assertIn('workshop_dir: old-dir\r\n', text)
        self.assertNotIn('old.json', text)

    def test_appends_missing_key(self):
        path = core.config_path
        self.write(path, '# 注释\njson_path: a.json\n')
        core.write_config_values({'workshop_dir': r'D:\ws'}, path)
        config = core.read_config_file(path)
        self.assertEqual(config['json_path'], 'a.json')
        self.assertEqual(config['workshop_dir'], r'D:\ws')

    def test_comment_lines_are_not_rewritten(self):
        """注释里的示例键不能被当成真实配置改掉"""
        path = core.config_path
        self.write(
            path,
            '# json_path: D:\\example\\workshopcache.json\n'
            'json_path: ""\n'
            'workshop_dir: ""\n',
        )
        core.write_config_values({'json_path': r'D:\real.json'}, path)
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        self.assertIn('# json_path: D:\\example\\workshopcache.json', text)
        self.assertIn('json_path: D:\\real.json', text)
        # 注释里 1 次 + 实际配置 1 次（模板行被替换掉了，不应残留在文件里）
        self.assertEqual(text.count('json_path'), 2)
        self.assertNotIn('json_path: ""', text)

    def test_quotes_value_containing_hash(self):
        path = core.config_path
        self.write(path, 'json_path: ""\nworkshop_dir: ""\n')
        core.write_config_values({'json_path': r'D:\a#b\wc.json'}, path)
        config = core.read_config_file(path)
        self.assertEqual(config['json_path'], r'D:\a#b\wc.json')

    def test_json_config_written_back_as_json(self):
        path = core.legacy_config_path
        self.write(path, json.dumps({'json_path': 'old.json', 'workshop_dir': 'old'}))
        core.write_config_values({'workshop_dir': r'D:\new-ws'}, path)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertEqual(data['json_path'], 'old.json')
        self.assertEqual(data['workshop_dir'], r'D:\new-ws')

    def test_missing_file_uses_template(self):
        path = os.path.join(self.tmp, 'brand-new.yml')
        core.write_config_values({'json_path': r'D:\x.json', 'workshop_dir': r'D:\ws'}, path)
        config = core.read_config_file(path)
        self.assertEqual(config['json_path'], r'D:\x.json')
        self.assertEqual(config['workshop_dir'], r'D:\ws')


class TestSubscriptions(SandboxTestCase):
    def test_reads_workshop_ids(self):
        _, json_path, subscriptions = self.make_workshop(subscribed_ids=['111', '222'])
        loaded = core.load_subscriptions(json_path)
        self.assertEqual(set(loaded), {'111', '222'})
        self.assertEqual(loaded['111']['title'], '壁纸 111')
        self.assertEqual(loaded['111']['size'], '1.0 MB')

    def test_missing_file_raises(self):
        with self.assertRaises(core.SubscriptionError):
            core.load_subscriptions(os.path.join(self.tmp, 'nope.json'))

    def test_broken_json_raises(self):
        path = os.path.join(self.tmp, 'broken.json')
        self.write(path, '{ not json')
        with self.assertRaises(core.SubscriptionError):
            core.load_subscriptions(path)

    def test_wallpapers_not_a_list_raises(self):
        path = os.path.join(self.tmp, 'weird.json')
        self.write(path, json.dumps({'wallpapers': {'a': 1}}))
        with self.assertRaises(core.SubscriptionError):
            core.load_subscriptions(path)

    def test_entries_without_id_are_skipped(self):
        path = os.path.join(self.tmp, 'partial.json')
        self.write(path, json.dumps({'wallpapers': [
            {'title': '无 id'},
            {'workshopid': '', 'title': '空 id'},
            'not-a-dict',
            {'workshopid': 333, 'title': '正常'},
        ]}))
        loaded = core.load_subscriptions(path)
        self.assertEqual(set(loaded), {'333'})


class TestScan(SandboxTestCase):
    def test_classification(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(
            subscribed_ids=['1111111111', '2222222222'],
            orphans=['3333333333'],
            unknown=['backup-old'],
        )
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)

        self.assertEqual(result['total_folders'], 4)
        self.assertEqual([i['wid'] for i in result['subscribed']], ['1111111111', '2222222222'])
        self.assertEqual([i['wid'] for i in result['orphans']], ['3333333333'])
        self.assertEqual([i['wid'] for i in result['unknown']], ['backup-old'])
        self.assertEqual(result['orphan_bytes'], 128)
        self.assertEqual(result['unknown_bytes'], 128)
        self.assertEqual(result['missing'], [])
        # 订阅项带上缓存里的标题与标注大小
        self.assertEqual(result['subscribed'][0]['title'], '壁纸 1111111111')

    def test_missing_reported(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(
            subscribed_ids=['1111111111'],
        )
        subscriptions['9999999999'] = {'title': '已订阅但磁盘没有', 'size': '1 MB'}
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)
        self.assertEqual(result['missing'], ['9999999999'])

    def test_orphans_sorted_by_size_desc(self):
        workshop_dir = os.path.join(self.tmp, '431960')
        os.makedirs(workshop_dir)
        for wid, files in (('111', 1), ('222', 5), ('333', 3)):
            folder = os.path.join(workshop_dir, wid)
            os.makedirs(folder)
            for i in range(files):
                with open(os.path.join(folder, f'{i}.bin'), 'wb') as f:
                    f.write(b'x' * 1024)
        result = core.scan(workshop_dir, {})
        self.assertEqual([i['wid'] for i in result['orphans']], ['222', '333', '111'])

    def test_files_in_workshop_dir_ignored(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['111'])
        self.write(os.path.join(workshop_dir, 'loose.txt'), 'hi')
        result = core.scan(workshop_dir, subscriptions)
        self.assertEqual(result['total_folders'], 1)

    def test_missing_dir_raises(self):
        with self.assertRaises(core.ScanError):
            core.scan(os.path.join(self.tmp, 'nope'), {})

    def test_progress_callback_receives_sizes(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['111', '222'])
        events = []
        core.scan(workshop_dir, subscriptions, on_progress=lambda *a: events.append(a))
        self.assertTrue(events)
        # 回调签名 (done, total, message, level)，最后一个事件应当 done == total
        self.assertTrue(any(e[0] == e[1] and e[1] == 2 for e in events))


class TestDelete(SandboxTestCase):
    def test_deletes_only_requested(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(
            subscribed_ids=['1111111111'],
            orphans=['3333333333', '4444444444'],
        )
        result = core.scan(workshop_dir, subscriptions)

        outcome = core.delete_folders(result['orphans'][:1], workshop_dir)
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertEqual(outcome['failed'], [])
        self.assertEqual(outcome['freed_bytes'], 128)

        remaining = sorted(os.listdir(workshop_dir))
        self.assertIn('1111111111', remaining)
        self.assertEqual(len(remaining), 2)

    def test_readonly_files_are_deleted(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['3333333333'])
        folder = os.path.join(workshop_dir, '3333333333')
        target = os.path.join(folder, 'data.bin')
        os.chmod(target, stat.S_IREAD)

        result = core.scan(workshop_dir, subscriptions)
        outcome = core.delete_folders(result['orphans'], workshop_dir)
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertFalse(os.path.exists(folder))

    def test_already_missing_folder_reports_failure(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['3333333333'])
        result = core.scan(workshop_dir, subscriptions)
        rmtree(os.path.join(workshop_dir, '3333333333'))

        outcome = core.delete_folders(result['orphans'], workshop_dir)
        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(len(outcome['failed']), 1)
        self.assertIn('已不存在', outcome['failed'][0]['error'])

    def test_traversal_ids_are_rejected(self):
        workshop_dir = os.path.join(self.tmp, '431960')
        os.makedirs(workshop_dir)
        outside = os.path.join(self.tmp, 'outside')
        os.makedirs(outside)

        for bad in ('..', '.', 'a/b', 'a\\b', '', '../outside'):
            self.assertFalse(core.is_safe_wid(bad), bad)
            with self.assertRaises(core.ScanError, msg=bad):
                core.resolve_target_path(workshop_dir, bad)

        self.assertEqual(
            core.resolve_target_path(workshop_dir, '12345'),
            os.path.join(os.path.abspath(workshop_dir), '12345'),
        )

    def test_delete_does_not_escape_workshop_dir(self):
        """即使传入越界 wid，也不允许碰到 workshop_dir 之外的内容"""
        workshop_dir, _json_path, _subs = self.make_workshop(orphans=['3333333333'])
        outside = os.path.join(self.tmp, 'important')
        os.makedirs(outside)
        with open(os.path.join(outside, 'keep.txt'), 'w', encoding='utf-8') as f:
            f.write('keep me')

        outcome = core.delete_folders(
            [{'wid': '../important', 'size_bytes': 0}], workshop_dir
        )
        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(len(outcome['failed']), 1)
        self.assertTrue(os.path.exists(os.path.join(outside, 'keep.txt')))


class TestDescribeConfig(SandboxTestCase):
    def test_no_config_reports_missing_without_error(self):
        info = core.describe_config(auto_detect=('auto.json', 'auto-dir'))
        self.assertFalse(info['exists'])
        self.assertIsNone(info['error'])
        self.assertEqual(info['auto_json_path'], 'auto.json')
        self.assertEqual(info['auto_workshop_dir'], 'auto-dir')
        self.assertEqual(info['json_path'], '')

    def test_reports_resolved_paths(self):
        workshop_dir, json_path, _subs = self.make_workshop(subscribed_ids=['111'])
        self.write(
            core.config_path,
            f'# 面板配置\njson_path: {json_path}\nworkshop_dir: {workshop_dir}\n',
        )
        info = core.describe_config(auto_detect=(None, None))
        self.assertTrue(info['exists'])
        self.assertEqual(info['config_name'], 'config.yml')
        self.assertFalse(info['is_legacy_json'])
        self.assertEqual(info['json_path'], os.path.normpath(json_path))
        self.assertEqual(info['workshop_dir'], os.path.normpath(workshop_dir))
        self.assertIsNone(info['error'])

    def test_broken_config_reports_error_without_raising(self):
        self.write(core.config_path, 'json_path ??? oops\n')
        info = core.describe_config(auto_detect=(None, None))
        self.assertTrue(info['exists'])
        self.assertIsNotNone(info['error'])

    def test_legacy_json_flagged(self):
        self.write(core.legacy_config_path, json.dumps({'json_path': 'a', 'workshop_dir': 'b'}))
        info = core.describe_config(auto_detect=(None, None))
        self.assertTrue(info['is_legacy_json'])
        self.assertEqual(info['config_name'], 'config.json')


class TestLoadConfig(SandboxTestCase):
    def test_missing_raises_config_missing(self):
        with self.assertRaises(core.ConfigMissingError):
            core.load_config()

    def test_empty_fields_raise_config_error(self):
        self.write(core.config_path, 'json_path: ""\nworkshop_dir: ""\n')
        with self.assertRaises(core.ConfigError):
            core.load_config()

    def test_loads_both_fields(self):
        self.write(core.config_path, 'json_path: a.json\nworkshop_dir: ws\n')
        config = core.load_config()
        self.assertEqual(config['json_path'], os.path.join(self.tmp, 'a.json'))
        self.assertEqual(config['workshop_dir'], os.path.join(self.tmp, 'ws'))


class TestFormatSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(core.format_size(0), '0.00 B')
        self.assertEqual(core.format_size(1023), '1023.00 B')
        self.assertEqual(core.format_size(1024), '1.00 KB')
        self.assertEqual(core.format_size(1024 * 1024 * 3), '3.00 MB')
        self.assertEqual(core.format_size(1024 ** 4 * 2), '2.00 TB')


class TestLogTail(SandboxTestCase):
    def test_reads_latest_log(self):
        os.makedirs(core.log_dir)
        path = os.path.join(core.log_dir, 'wallpaper-cleaner_20250101.log')
        self.write(path, 'line1\nline2\nline3\n')
        tail = core.read_log_tail(max_lines=2)
        self.assertEqual(tail['lines'], ['line2', 'line3'])
        self.assertEqual(tail['path'], path)

    def test_no_logs_returns_empty(self):
        tail = core.read_log_tail()
        self.assertIsNone(tail['path'])
        self.assertEqual(tail['lines'], [])


if __name__ == '__main__':
    unittest.main()
