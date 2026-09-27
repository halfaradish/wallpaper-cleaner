"""core 模块单元测试：全部在临时目录沙箱内进行，不触碰真实配置与真实 Steam 目录

运行：python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import core


# 仿真的 appworkshop_431960.acf。
# 关键语义：WorkshopItemsInstalled 是"内容还在磁盘上"的记录，WorkshopItemDetails 里带
# subscribedby 的才是"还在订阅"。3115163440 只在安装记录里 —— 它正是取消订阅后的残留；
# 9999999999 只在明细里 —— 已订阅但内容还没装完。
ACF_SAMPLE = '''"AppWorkshop"
{
\t"appid"\t\t"431960"
\t"SizeOnDisk"\t\t"2744644209"
\t"WorkshopItemsInstalled"
\t{
\t\t"826336550"
\t\t{
\t\t\t"size"\t\t"2420536"
\t\t\t"timeupdated"\t\t"1482753697"
\t\t\t"manifest"\t\t"6751562254374569917"
\t\t}
\t\t"3115163440"
\t\t{
\t\t\t"size"\t\t"1536"
\t\t\t"manifest"\t\t"1"
\t\t}
\t}
\t"WorkshopItemDetails"
\t{
\t\t"826336550"
\t\t{
\t\t\t"manifest"\t\t"6751562254374569917"
\t\t\t"timetouched"\t\t"1790413993"
\t\t\t"subscribedby"\t\t"1508410657"
\t\t}
\t\t"9999999999"
\t\t{
\t\t\t"timetouched"\t\t"1790413993"
\t\t\t"subscribedby"\t\t"1508410657"
\t\t}
\t}
}
'''

ACF_INSTALLED_IDS = {'826336550', '3115163440'}
ACF_SUBSCRIBED_IDS = {'826336550', '9999999999'}

# 部分 Steam 版本会额外写一个订阅清单小节，这个也要认
ACF_WITH_SUBSCRIBED_SECTION = '''"AppWorkshop"
{
\t"appid"\t\t"431960"
\t"WorkshopItemsSubscribed"
\t{
\t\t"1111111111"
\t\t{
\t\t\t"timeupdated"\t\t"1700000000"
\t\t}
\t}
\t"WorkshopItemsInstalled"
\t{
\t\t"1111111111"
\t\t{
\t\t\t"size"\t\t"1024"
\t\t}
\t\t"2222222222"
\t\t{
\t\t\t"size"\t\t"2048"
\t\t}
\t}
}
'''


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

    def test_missing_wallpapers_key_raises(self):
        """WE 正在重写缓存时不能把"没有 wallpapers 键"当成零订阅，否则所有目录都会变成残留"""
        path = os.path.join(self.tmp, 'rewriting.json')
        self.write(path, json.dumps({'user': 1, 'version': 2}))
        with self.assertRaises(core.SubscriptionError):
            core.load_subscriptions(path)

    def test_empty_wallpapers_is_allowed(self):
        path = os.path.join(self.tmp, 'empty.json')
        self.write(path, json.dumps({'user': 1, 'version': 2, 'wallpapers': []}))
        self.assertEqual(core.load_subscriptions(path), {})

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

    def test_orphan_gets_title_from_project_json(self):
        """残留目录里也有作者发布的 project.json，要显示标题而不是只有一串数字"""
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['2250930375'])
        self.write(
            os.path.join(workshop_dir, '2250930375', 'project.json'),
            json.dumps({'title': 'Ahegao Collage 4k Ultimate', 'type': 'scene'}),
        )

        result = core.scan(workshop_dir, subscriptions)

        entry = result['orphans'][0]
        self.assertEqual(entry['title'], 'Ahegao Collage 4k Ultimate')
        self.assertEqual(entry['wp_type'], 'scene')

    def test_unknown_folder_gets_title_too(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(unknown=['backup-old'])
        self.write(
            os.path.join(workshop_dir, 'backup-old', 'project.json'),
            json.dumps({'title': '手动备份的壁纸', 'type': 'video'}),
        )

        result = core.scan(workshop_dir, subscriptions)

        self.assertEqual(result['unknown'][0]['title'], '手动备份的壁纸')
        self.assertEqual(result['unknown'][0]['wp_type'], 'video')

    def test_orphan_without_project_json_has_empty_title(self):
        """内容被 Steam 清理过的残留：标题取不到，但也要标出内容已缺失"""
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['3333333333'])
        result = core.scan(workshop_dir, subscriptions)
        self.assertEqual(result['orphans'][0]['title'], '')
        self.assertEqual(result['orphans'][0]['wp_type'], '')
        self.assertTrue(result['orphans'][0]['content_missing'])

    def test_orphan_with_project_json_is_not_marked_missing(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['3333333333'])
        self.write(
            os.path.join(workshop_dir, '3333333333', 'project.json'),
            json.dumps({'title': '某张壁纸', 'type': 'scene'}),
        )
        result = core.scan(workshop_dir, subscriptions)
        self.assertFalse(result['orphans'][0]['content_missing'])

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


class TestSteamAcf(SandboxTestCase):
    def test_parses_nested_sections(self):
        root = core.parse_vdf(ACF_SAMPLE)
        app = root['AppWorkshop']
        self.assertEqual(app['appid'], '431960')
        self.assertEqual(app['WorkshopItemsInstalled']['826336550']['size'], '2420536')
        self.assertEqual(app['WorkshopItemDetails']['9999999999']['subscribedby'], '1508410657')

    def test_subscription_comes_from_details_not_installed(self):
        """订阅记录 = 明细里的 subscribedby；安装记录里多出来的那条不算已订阅"""
        subscribed, installed = core.parse_acf_record(ACF_SAMPLE)
        self.assertEqual(subscribed, ACF_SUBSCRIBED_IDS)
        self.assertEqual(set(installed), ACF_INSTALLED_IDS)
        self.assertNotIn('3115163440', subscribed)  # 只在安装记录里 → 残留
        self.assertIn('3115163440', installed)

    def test_subscribed_section_is_supported(self):
        subscribed, installed = core.parse_acf_record(ACF_WITH_SUBSCRIBED_SECTION)
        self.assertEqual(subscribed, {'1111111111'})
        self.assertEqual(set(installed), {'1111111111', '2222222222'})

    def test_subscribedby_zero_is_ignored(self):
        text = ACF_SAMPLE.replace('"subscribedby"\t\t"1508410657"', '"subscribedby"\t\t"0"')
        subscribed, _installed = core.parse_acf_record(text)
        self.assertEqual(subscribed, set())

    def test_legacy_misspelled_installed_section(self):
        """旧版 Steam 把安装记录小节拼成了 WokshopItemsInstalled"""
        text = ACF_SAMPLE.replace('WorkshopItemsInstalled', 'WokshopItemsInstalled')
        _subscribed, installed = core.parse_acf_record(text)
        self.assertEqual(set(installed), ACF_INSTALLED_IDS)

    def test_derives_path_from_workshop_dir(self):
        workshop_dir = os.path.join(self.tmp, 'steamapps', 'workshop', 'content', '431960')
        self.assertEqual(
            core.steam_acf_path(workshop_dir),
            os.path.join(self.tmp, 'steamapps', 'workshop', 'appworkshop_431960.acf'),
        )

    def test_empty_path_returns_empty(self):
        self.assertEqual(core.steam_acf_path(''), '')

    def test_reads_file_with_bom_and_crlf(self):
        workshop_dir = os.path.join(self.tmp, 'steamapps', 'workshop', 'content', '431960')
        os.makedirs(workshop_dir)
        acf_path = core.steam_acf_path(workshop_dir)
        self.write(acf_path, '\ufeff' + ACF_SAMPLE.replace('\n', '\r\n'))

        record = core.load_steam_record(acf_path)
        self.assertTrue(record['ok'])
        self.assertEqual(record['subscribed'], ACF_SUBSCRIBED_IDS)
        self.assertGreater(record['mtime'], 0)

    def test_missing_file_returns_empty_record(self):
        record = core.load_steam_record(os.path.join(self.tmp, 'nope.acf'))
        self.assertFalse(record['ok'])
        self.assertEqual(record['subscribed'], set())
        self.assertEqual(record['installed'], {})
        self.assertFalse(core.load_steam_record('')['ok'])

    def test_truncated_file_does_not_raise(self):
        """Steam 正在重写这个文件时读到的可能是半截内容，不能因此中断扫描"""
        cut = ACF_SAMPLE[:ACF_SAMPLE.index('"9999999999"')]  # 断在明细小节中间
        subscribed, installed = core.parse_acf_record(cut)
        self.assertEqual(subscribed, {'826336550'})
        self.assertEqual(set(installed), ACF_INSTALLED_IDS)

        dangling = '"AppWorkshop"\n{\n\t"WorkshopItemsInstalled"\n\t{\n\t\t"111"\n\t\t{\n'
        _subscribed, installed = core.parse_acf_record(dangling)
        self.assertEqual(set(installed), {'111'})

    def test_garbage_returns_empty(self):
        self.assertEqual(core.parse_acf_record('not a vdf at all'), (set(), {}))
        self.assertEqual(core.parse_acf_record(''), (set(), {}))

    def test_installed_sizes_are_parsed(self):
        _subscribed, installed = core.parse_acf_record(ACF_SAMPLE)
        self.assertEqual(core.installed_sizes({'installed': installed})['3115163440'], 1536)
        # 解析不了的 size 直接跳过，不影响其它条目
        self.assertEqual(core.installed_sizes({'installed': {'1': {'size': 'abc'}, '2': {}}}), {})


class TestSubscriptionContext(SandboxTestCase):
    """「谁更新就信谁」：Steam 的 ACF 只在比 WE 缓存新时才被采信"""

    def make_layout(self, cache_ids=(), acf_text=None, acf_age=0, cache_age=0):
        """按 Steam 的真实层级搭沙箱，返回 (workshop_dir, json_path)"""
        workshop_dir = os.path.join(self.tmp, 'steamapps', 'workshop', 'content', '431960')
        os.makedirs(workshop_dir, exist_ok=True)
        json_path = os.path.join(self.tmp, 'workshopcache.json')
        payload = {
            'user': 1,
            'version': 2,
            'wallpapers': [
                {'workshopid': i, 'title': f'壁纸 {i}', 'filesizelabel': '1 MB'}
                for i in cache_ids
            ],
        }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)

        acf_path = core.steam_acf_path(workshop_dir)
        if acf_text is not None:
            self.write(acf_path, acf_text)
            now = time.time()
            os.utime(json_path, (now - cache_age, now - cache_age))
            os.utime(acf_path, (now - acf_age, now - acf_age))
        return workshop_dir, json_path

    def test_newer_steam_record_is_trusted(self):
        """刚下载完：Steam 记录比缓存新，用它兜住还没进缓存的壁纸"""
        workshop_dir, json_path = self.make_layout(
            cache_ids=['826336550'], acf_text=ACF_SAMPLE, acf_age=0, cache_age=600)
        context = core.load_subscription_context(json_path, workshop_dir)

        self.assertTrue(context['steam_fresh'])
        self.assertIn('9999999999', context['protected'])  # 只在 Steam 明细里
        self.assertEqual(context['disputed'], set())

    def test_older_steam_record_is_ignored(self):
        """刚取消订阅：Steam 记录比缓存旧，里面的订阅字段已过期，以缓存为准"""
        workshop_dir, json_path = self.make_layout(
            cache_ids=['826336550'], acf_text=ACF_SAMPLE, acf_age=600, cache_age=0)
        context = core.load_subscription_context(json_path, workshop_dir)

        self.assertFalse(context['steam_fresh'])
        self.assertEqual(context['protected'], {'826336550'})
        self.assertIn('9999999999', context['disputed'])

    def test_installed_only_id_is_never_protected(self):
        """回归（神里绫华）：只有安装记录的目录是残留，不能被当成已订阅保护起来"""
        workshop_dir, json_path = self.make_layout(
            cache_ids=[], acf_text=ACF_SAMPLE, acf_age=0, cache_age=600)
        context = core.load_subscription_context(json_path, workshop_dir)

        self.assertNotIn('3115163440', context['protected'])
        self.assertIn('9999999999', context['protected'])
        self.assertEqual(context['installed_sizes']['3115163440'], 1536)

    def test_complete_survives_a_stale_acf(self):
        """回归：ACF 比缓存旧时，内容记录仍然算数——"装完"不会因为缓存被重写而失效

        否则刚取消订阅（WE 刚重写缓存、Steam 还没重写 ACF）时，30 分钟新鲜度守卫
        会对早已下载完的残留生效，出现"清理 0 项、跳过 N 个"。
        """
        workshop_dir, json_path = self.make_layout(
            cache_ids=['826336550'], acf_text=ACF_SAMPLE, acf_age=600, cache_age=0)
        context = core.load_subscription_context(json_path, workshop_dir)

        self.assertFalse(context['steam_fresh'])
        self.assertEqual(context['complete'], ACF_INSTALLED_IDS)

    def test_complete_is_empty_without_acf(self):
        workshop_dir, json_path = self.make_layout(cache_ids=['826336550'], acf_text=None)
        context = core.load_subscription_context(json_path, workshop_dir)
        self.assertEqual(context['complete'], set())

    def test_leftover_is_reported_as_orphan(self):
        """端到端回归：WE 缓存已移除 + Steam 记录较旧 → 必须报成待清理"""
        workshop_dir, json_path = self.make_layout(
            cache_ids=['826336550'], acf_text=ACF_SAMPLE, acf_age=600, cache_age=0)
        for wid in ('826336550', '3115163440'):
            os.makedirs(os.path.join(workshop_dir, wid))
        context = core.load_subscription_context(json_path, workshop_dir)

        result = core.scan(
            workshop_dir, context['subscriptions'],
            extra_subscribed=context['protected'], extra_sizes=context['installed_sizes'],
        )
        self.assertEqual([i['wid'] for i in result['orphans']], ['3115163440'])
        self.assertEqual([i['wid'] for i in result['subscribed']], ['826336550'])

    def test_missing_acf_degrades_to_cache_only(self):
        workshop_dir, json_path = self.make_layout(cache_ids=['826336550'], acf_text=None)
        context = core.load_subscription_context(json_path, workshop_dir)

        self.assertFalse(context['steam']['ok'])
        self.assertEqual(context['protected'], {'826336550'})
        lines = core.describe_steam_record(context)
        self.assertTrue(any(level == 'warn' for level, _msg in lines))

    def test_describe_mentions_stale_record_and_leftovers(self):
        workshop_dir, json_path = self.make_layout(
            cache_ids=['826336550'], acf_text=ACF_SAMPLE, acf_age=600, cache_age=0)
        context = core.load_subscription_context(json_path, workshop_dir)

        text = ' '.join(msg for _level, msg in core.describe_steam_record(context))
        self.assertIn('以缓存为准', text)
        self.assertIn('安装记录里有 1 条内容已不在订阅列表', text)


class TestProjectMeta(SandboxTestCase):
    def make_folder(self, text=None):
        folder = os.path.join(self.tmp, 'wp')
        os.makedirs(folder, exist_ok=True)
        if text is not None:
            self.write(os.path.join(folder, 'project.json'), text)
        return folder

    def test_reads_title_and_type(self):
        folder = self.make_folder(json.dumps({'title': '神里绫华-X-Ray-ほうき星', 'type': 'scene'}))
        self.assertEqual(
            core.read_project_meta(folder),
            {'title': '神里绫华-X-Ray-ほうき星', 'type': 'scene', 'preview': '', 'present': True},
        )

    def test_reads_declared_preview(self):
        folder = self.make_folder(
            json.dumps({'title': 'x', 'type': 'scene', 'preview': 'preview.gif'}))
        self.assertEqual(core.read_project_meta(folder)['preview'], 'preview.gif')

    def test_missing_file_returns_empty(self):
        """清单文件不在：内容多半已被 Steam 清理，present 为 False"""
        self.assertEqual(
            core.read_project_meta(self.make_folder()),
            {'title': '', 'type': '', 'preview': '', 'present': False},
        )

    def test_broken_json_returns_empty(self):
        """文件在但读不懂：字段空着，但不算「内容已缺失」——那是文件自己的问题"""
        self.assertEqual(
            core.read_project_meta(self.make_folder('{ not json')),
            {'title': '', 'type': '', 'preview': '', 'present': True},
        )

    def test_non_dict_json_returns_empty(self):
        self.assertEqual(
            core.read_project_meta(self.make_folder('[1, 2]')),
            {'title': '', 'type': '', 'preview': '', 'present': True},
        )

    def test_oversized_file_is_skipped(self):
        folder = self.make_folder()
        path = os.path.join(folder, 'project.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('x' * (core.PROJECT_JSON_MAX_BYTES + 1))
        self.assertEqual(
            core.read_project_meta(folder),
            {'title': '', 'type': '', 'preview': '', 'present': True},
        )


class TestPreview(SandboxTestCase):
    """预览图：优先用作者在 project.json 里声明的那个，声明的不可信时按固定候选名找"""

    def make_folder(self, files=(), declared=''):
        folder = os.path.join(self.tmp, 'wp')
        os.makedirs(folder, exist_ok=True)
        if declared:
            self.write(
                os.path.join(folder, 'project.json'),
                json.dumps({'title': 'x', 'type': 'scene', 'preview': declared}),
            )
        for name in files:
            self.write(os.path.join(folder, name), 'image-bytes')
        return folder

    def test_declared_preview_wins(self):
        folder = self.make_folder(['preview.jpg', 'preview.gif'], declared='preview.gif')
        self.assertEqual(core.find_preview(folder, 'preview.gif'), 'preview.gif')

    def test_falls_back_to_static_before_gif(self):
        """作者没声明时静态图排在 GIF 前面，免得一屏壁纸全在播动画"""
        folder = self.make_folder(['preview.gif', 'preview.png'])
        self.assertEqual(core.find_preview(folder), 'preview.png')

    def test_declared_path_traversal_is_rejected(self):
        folder = self.make_folder(['preview.jpg'], declared='../../config.json')
        self.assertEqual(core.find_preview(folder, '../../config.json'), 'preview.jpg')

    def test_declared_subdir_is_rejected(self):
        folder = self.make_folder(['preview.jpg'], declared='sub/preview.png')
        self.assertEqual(core.find_preview(folder, 'sub/preview.png'), 'preview.jpg')

    def test_declared_non_image_is_rejected(self):
        folder = self.make_folder(['preview.jpg'], declared='video.mp4')
        self.assertEqual(core.find_preview(folder, 'video.mp4'), 'preview.jpg')

    def test_declared_file_missing_falls_back(self):
        folder = self.make_folder(['preview.png'], declared='preview.gif')
        self.assertEqual(core.find_preview(folder, 'preview.gif'), 'preview.png')

    def test_no_preview_at_all(self):
        self.assertEqual(core.find_preview(self.make_folder(['scene.pkg'])), '')

    def test_oversized_preview_is_ignored(self):
        folder = self.make_folder()
        with open(os.path.join(folder, 'preview.jpg'), 'wb') as f:
            f.write(b'x' * (core.PREVIEW_MAX_BYTES + 1))
        self.assertEqual(core.find_preview(folder), '')
        self.assertEqual(core.resolve_preview_path(folder, 'preview.jpg'), '')

    def test_file_outside_the_folder_is_rejected(self):
        """扫描之后目录被换成指向外面的软链接，也不给读"""
        folder = self.make_folder()
        outside = os.path.join(self.tmp, 'outside.jpg')
        self.write(outside, 'secret')
        try:
            os.symlink(outside, os.path.join(folder, 'preview.jpg'))
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('当前环境不允许创建符号链接')
        self.assertEqual(core.resolve_preview_path(folder, 'preview.jpg'), '')


class TestWeThumbnail(SandboxTestCase):
    """目录里的预览图被 Steam 连着内容一起删掉后，回退到 WE 缓存的浏览缩略图"""

    def make_we(self, wid='3764725758', size=1024):
        """按 WE 的真实布局搭沙箱：<tmp>\\wallpaper_engine\\bin\\workshopcache.json"""
        we_root = os.path.join(self.tmp, 'wallpaper_engine')
        thumbs = os.path.join(we_root, 'ui', 'thumbnails')
        os.makedirs(thumbs)
        json_path = os.path.join(we_root, 'bin', 'workshopcache.json')
        os.makedirs(os.path.dirname(json_path))
        self.write(json_path, '{}')
        thumb = os.path.join(thumbs, f'ws_{wid}_thumb.jpg')
        with open(thumb, 'wb') as f:
            f.write(b'x' * size)
        return json_path, thumb

    def test_finds_cached_thumbnail(self):
        json_path, thumb = self.make_we()
        self.assertEqual(core.we_thumbnail_path(json_path, '3764725758'), thumb)

    def test_missing_thumbnail_is_empty(self):
        json_path, _thumb = self.make_we(wid='1111111111')
        self.assertEqual(core.we_thumbnail_path(json_path, '3764725758'), '')

    def test_unstandard_layout_is_empty(self):
        """缓存文件被放到了别处时推不出缩略图目录：当作没有兜底图，不报错"""
        elsewhere = os.path.join(self.tmp, 'somewhere', 'workshopcache.json')
        os.makedirs(os.path.dirname(elsewhere))
        self.write(elsewhere, '{}')
        self.assertEqual(core.we_thumbnail_path(elsewhere, '3764725758'), '')
        self.assertEqual(core.we_thumbnail_path('', '3764725758'), '')
        self.assertEqual(core.we_thumbnail_dir(''), '')

    def test_unsafe_wid_is_rejected(self):
        json_path, _thumb = self.make_we()
        for wid in ('..', 'a/b', '', 'a\\b', '3764725758/../other'):
            self.assertEqual(core.we_thumbnail_path(json_path, wid), '')

    def test_oversized_thumbnail_is_ignored(self):
        json_path, _thumb = self.make_we(size=core.PREVIEW_MAX_BYTES + 1)
        self.assertEqual(core.we_thumbnail_path(json_path, '3764725758'), '')

    def test_file_outside_the_dir_is_rejected(self):
        """缩略图被换成指向别处的软链接时不给读"""
        json_path, thumb = self.make_we()
        outside = os.path.join(self.tmp, 'outside.jpg')
        self.write(outside, 'secret')
        os.remove(thumb)
        try:
            os.symlink(outside, thumb)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest('当前环境不允许创建符号链接')
        self.assertEqual(core.we_thumbnail_path(json_path, '3764725758'), '')

    def test_thumb_source_prefers_the_folder(self):
        """目录里有作者发的原图就用原图，没有才回退到 WE 缓存"""
        json_path, _thumb = self.make_we()
        self.assertEqual(core.thumb_source('preview.jpg', json_path, '3764725758'), 'folder')
        self.assertEqual(core.thumb_source('', json_path, '3764725758'), 'we')
        self.assertEqual(core.thumb_source('', json_path, '9999999999'), '')


class TestOpenFolder(unittest.TestCase):
    """点标题打开目录最终落到系统文件管理器上，这里只验证这层交接"""

    def test_hands_the_path_to_the_system_file_manager(self):
        with mock.patch.object(core.os, 'startfile', create=True) as startfile:
            core.open_folder(r'C:\some\dir')
        startfile.assert_called_once_with(r'C:\some\dir')

    def test_unsupported_platform_raises_instead_of_silently_doing_nothing(self):
        with mock.patch.object(core.sys, 'platform', 'linux'):
            with self.assertRaises(OSError):
                core.open_folder('/some/dir')


class TestOpenUrl(unittest.TestCase):
    """steam:// 这类协议交给系统处理，面板的「启动 Steam」「手动取消订阅」都走它"""

    def test_hands_the_url_to_the_system(self):
        with mock.patch.object(core.os, 'startfile', create=True) as startfile:
            core.open_url('steam://open/main')
        startfile.assert_called_once_with('steam://open/main')

    def test_workshop_item_url(self):
        with mock.patch.object(core.os, 'startfile', create=True) as startfile:
            core.open_url('steam://url/CommunityFilePage/3115163440')
        startfile.assert_called_once_with('steam://url/CommunityFilePage/3115163440')

    def test_unsupported_platform_raises_instead_of_silently_doing_nothing(self):
        with mock.patch.object(core.sys, 'platform', 'linux'):
            with self.assertRaises(OSError):
                core.open_url('steam://open/main')


class TestItemLabel(unittest.TestCase):
    def test_with_and_without_title(self):
        self.assertEqual(core.item_label({'wid': '123', 'title': '神里绫华'}), '123（神里绫华）')
        self.assertEqual(core.item_label({'wid': '123', 'title': ''}), '123')
        self.assertEqual(core.item_label({'wid': '123', 'title': '   '}), '123')
        self.assertEqual(core.item_label({'wid': '123'}), '123')


class TestScanDisplayFallback(SandboxTestCase):
    def test_title_and_size_fall_back_to_folder_and_steam_record(self):
        """缓存里还没有标题时，用壁纸自己的 project.json 和 Steam 记录的占用大小补上"""
        workshop_dir, json_path, subscriptions = self.make_workshop(subscribed_ids=['1111111111'])
        wid = '3115163440'
        folder = os.path.join(workshop_dir, wid)
        os.makedirs(folder)
        self.write(
            os.path.join(folder, 'project.json'),
            json.dumps({'title': '神里绫华-X-Ray-ほうき星', 'type': 'scene'}),
        )

        result = core.scan(workshop_dir, subscriptions, extra_subscribed={wid},
                           extra_sizes={wid: 54567690})
        entry = [i for i in result['subscribed'] if i['wid'] == wid][0]
        self.assertEqual(entry['title'], '神里绫华-X-Ray-ほうき星')
        self.assertEqual(entry['wp_type'], 'scene')
        self.assertEqual(entry['declared_size'], core.format_size(54567690))
        self.assertEqual(result['orphans'], [])

    def test_broken_project_json_falls_back_to_unknown(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(subscribed_ids=['1111111111'])
        wid = '3115163440'
        folder = os.path.join(workshop_dir, wid)
        os.makedirs(folder)
        self.write(os.path.join(folder, 'project.json'), '{ not json')

        result = core.scan(workshop_dir, subscriptions, extra_subscribed={wid})
        entry = [i for i in result['subscribed'] if i['wid'] == wid][0]
        self.assertEqual(entry['title'], '未知')
        self.assertEqual(entry['declared_size'], '未知')

    def test_scan_reports_preview_file(self):
        """三类条目都带上目录里找到的预览图文件名，前端据此决定显示图还是占位框"""
        workshop_dir, json_path, subscriptions = self.make_workshop(
            subscribed_ids=['1111111111'], orphans=['2222222222'], unknown=['my-wallpaper'])
        for wid in ('1111111111', '2222222222', 'my-wallpaper'):
            folder = os.path.join(workshop_dir, wid)
            self.write(
                os.path.join(folder, 'project.json'),
                json.dumps({'title': f'壁纸 {wid}', 'type': 'scene', 'preview': 'preview.gif'}),
            )
            self.write(os.path.join(folder, 'preview.gif'), 'gif-bytes')

        result = core.scan(workshop_dir, subscriptions, json_path=json_path)
        by_wid = {i['wid']: i for i in
                  result['subscribed'] + result['orphans'] + result['unknown']}
        for wid in ('1111111111', '2222222222', 'my-wallpaper'):
            self.assertEqual(by_wid[wid]['preview'], 'preview.gif')
            self.assertEqual(by_wid[wid]['thumb_source'], 'folder')
            self.assertFalse(by_wid[wid]['content_missing'])
        # 缓存里有标题也照样读 project.json：类型和预览图都只有它写着
        self.assertEqual(by_wid['1111111111']['title'], '壁纸 1111111111')
        self.assertEqual(by_wid['1111111111']['wp_type'], 'scene')

    def test_scan_without_preview_reports_empty(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['2222222222'])
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)
        self.assertEqual(result['orphans'][0]['preview'], '')
        self.assertEqual(result['orphans'][0]['thumb_source'], '')
        self.assertTrue(result['orphans'][0]['content_missing'])

    def test_scan_falls_back_to_we_thumbnail(self):
        """只剩缓存文件的空壳：预览图没有了，缩略图指向 WE 缓存里那张"""
        workshop_dir, _json_path, subscriptions = self.make_workshop(orphans=['2222222222'])
        we_root = os.path.join(self.tmp, 'wallpaper_engine')
        thumbs = os.path.join(we_root, 'ui', 'thumbnails')
        os.makedirs(thumbs)
        json_path = os.path.join(we_root, 'bin', 'workshopcache.json')
        os.makedirs(os.path.dirname(json_path))
        self.write(json_path, '{}')
        self.write(os.path.join(thumbs, 'ws_2222222222_thumb.jpg'), 'jpeg-bytes')

        result = core.scan(workshop_dir, subscriptions, json_path=json_path)

        entry = result['orphans'][0]
        self.assertEqual(entry['thumb_source'], 'we')
        self.assertTrue(entry['content_missing'])

    def test_scan_reports_both_size_measures(self):
        """每个条目都给两个口径：文件字节数（精确）与磁盘占用（簇对齐的估算）"""
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['2222222222'])
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)

        entry = result['orphans'][0]
        self.assertEqual(entry['size_bytes'], 128)  # 精确值不受口径变化影响
        self.assertGreaterEqual(entry['alloc_bytes'], entry['size_bytes'])
        # 汇总也对称地给两份：文件字节数与磁盘占用
        self.assertEqual(result['orphan_bytes'], 128)
        self.assertEqual(result['orphan_usage_bytes'],
                         sum(core.usage_bytes(i) for i in result['orphans']))
        self.assertEqual(result['unknown_usage_bytes'], 0)

    def test_orphan_declared_size_comes_from_steam_record(self):
        """残留如果还留着 Steam 的内容记录，就有「标注大小」可看（重新订阅前估流量用）"""
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['2222222222'])

        result = core.scan(workshop_dir, subscriptions, json_path=json_path,
                           extra_sizes={'2222222222': 15692193})

        entry = result['orphans'][0]
        self.assertEqual(entry['declared_bytes'], 15692193)
        self.assertEqual(entry['declared_size'], core.format_size(15692193))

    def test_orphan_without_steam_record_has_no_declared_size(self):
        """内容记录被 Steam 删掉后就只剩「未知」——这张表里不会拿实测值冒充标注值"""
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['2222222222'])
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)
        self.assertEqual(result['orphans'][0]['declared_size'], '未知')
        self.assertEqual(result['orphans'][0]['declared_bytes'], 0)

    def test_steam_record_wins_over_we_label(self):
        """两张表的标注大小用同一套判定：Steam 记录的字节数优先，WE 的标注字符串只作回退"""
        workshop_dir, json_path, subscriptions = self.make_workshop(subscribed_ids=['1111111111'])
        result = core.scan(workshop_dir, subscriptions, json_path=json_path,
                           extra_sizes={'1111111111': 54567690})
        entry = result['subscribed'][0]
        self.assertEqual(entry['declared_bytes'], 54567690)
        self.assertEqual(entry['declared_size'], core.format_size(54567690))

        # 没有 Steam 记录时退回 WE 缓存里的标注字符串（夹具里是 '1.0 MB'）
        result = core.scan(workshop_dir, subscriptions, json_path=json_path)
        entry = result['subscribed'][0]
        self.assertEqual(entry['declared_size'], '1.0 MB')
        self.assertEqual(entry['declared_bytes'], 0)


class TestScanWithSteamRecord(SandboxTestCase):
    def test_steam_subscribed_id_is_not_orphan(self):
        """回归：刚下载完、还没写进 WE 缓存的壁纸，不能显示成「已取消订阅」"""
        workshop_dir, json_path, subscriptions = self.make_workshop(
            subscribed_ids=['1111111111'], orphans=['3115163440'])
        result = core.scan(workshop_dir, subscriptions, json_path=json_path,
                           extra_subscribed={'3115163440'})

        self.assertEqual(result['orphans'], [])
        self.assertEqual([i['wid'] for i in result['subscribed']],
                         ['1111111111', '3115163440'])
        # 缓存里还没有它的标题和标注大小，按未知展示，其余字段与普通订阅项一致
        entry = [i for i in result['subscribed'] if i['wid'] == '3115163440'][0]
        self.assertEqual(entry['kind'], 'subscribed')
        self.assertEqual(entry['title'], '未知')
        self.assertEqual(entry['declared_size'], '未知')
        self.assertNotIn('3115163440', result['missing'])

    def test_installed_but_not_on_disk_is_missing(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(subscribed_ids=['1111111111'])
        result = core.scan(workshop_dir, subscriptions, extra_subscribed={'9999999999'})
        self.assertEqual(result['missing'], ['9999999999'])

    def test_extra_ids_never_add_to_cleanup_list(self):
        """额外来源只能保护，不能把别的目录变成待清理项"""
        workshop_dir, json_path, subscriptions = self.make_workshop(
            subscribed_ids=['1111111111'], orphans=['3333333333'], unknown=['backup-old'])
        before = core.scan(workshop_dir, subscriptions)
        after = core.scan(workshop_dir, subscriptions, extra_subscribed={'3115163440'})

        self.assertEqual([i['wid'] for i in before['orphans']], ['3333333333'])
        self.assertEqual([i['wid'] for i in after['orphans']], ['3333333333'])
        self.assertEqual([i['wid'] for i in before['unknown']], ['backup-old'])
        self.assertEqual([i['wid'] for i in after['unknown']], ['backup-old'])

    def test_default_keeps_old_behaviour(self):
        workshop_dir, json_path, subscriptions = self.make_workshop(orphans=['3115163440'])
        result = core.scan(workshop_dir, subscriptions)
        self.assertEqual([i['wid'] for i in result['orphans']], ['3115163440'])


class TestFreshDownloadGuard(SandboxTestCase):
    """新鲜度看的是目录里内容的修改时间，不是目录自身的 mtime

    Steam 删除内容时同样会刷新目录的 mtime，只看目录的话，「刚被清空的残留」会被
    误判成「正在下载」，白等一轮保护期。
    """

    def backdate(self, path, seconds):
        """把目录连同里面的内容一起回拨到过去"""
        stamp = time.time() - seconds
        for dirpath, dirnames, filenames in os.walk(path):
            for name in filenames:
                os.utime(os.path.join(dirpath, name), (stamp, stamp))
            for name in dirnames:
                os.utime(os.path.join(dirpath, name), (stamp, stamp))
        os.utime(path, (stamp, stamp))

    def test_recent_folder_is_protected(self):
        workshop_dir, _json_path, _subs = self.make_workshop(orphans=['3333333333'])
        path = os.path.join(workshop_dir, '3333333333')
        self.assertTrue(core.is_freshly_downloaded(path))
        self.assertTrue(core.is_freshly_downloaded(path, grace_seconds=60))

    def test_old_folder_is_not_protected(self):
        workshop_dir, _json_path, _subs = self.make_workshop(orphans=['3333333333'])
        path = os.path.join(workshop_dir, '3333333333')
        self.backdate(path, core.FRESH_DOWNLOAD_GRACE_SECONDS + 60)
        self.assertFalse(core.is_freshly_downloaded(path))

    def test_folder_emptied_by_steam_is_not_protected(self):
        """Steam 刚清空过的残留：目录 mtime 是新的，剩下的内容却是旧的"""
        workshop_dir, _json_path, _subs = self.make_workshop(orphans=['3333333333'])
        path = os.path.join(workshop_dir, '3333333333')
        # 内容被删光，只剩 WE 写的着色器缓存（真实场景里就是 shaders/blobsSM40/*.dxs）
        os.remove(os.path.join(path, 'data.bin'))
        cache_dir = os.path.join(path, 'shaders')
        os.makedirs(cache_dir)
        self.write(os.path.join(cache_dir, 'blob.dxs'), 'cache')
        self.backdate(path, core.FRESH_DOWNLOAD_GRACE_SECONDS + 60)
        os.utime(path, None)  # Steam 的删除动作只刷新目录自身

        self.assertFalse(core.is_freshly_downloaded(path))

    def test_empty_folder_falls_back_to_its_own_time(self):
        """空目录没有内容可看（Steam 刚建好、还没开始写），仍按目录自身的时间保护"""
        path = os.path.join(self.tmp, 'empty-shell')
        os.makedirs(path)
        self.assertTrue(core.is_freshly_downloaded(path))

    def test_zero_grace_disables_guard(self):
        workshop_dir, _json_path, _subs = self.make_workshop(orphans=['3333333333'])
        path = os.path.join(workshop_dir, '3333333333')
        self.assertFalse(core.is_freshly_downloaded(path, grace_seconds=0))

    def test_missing_path_is_not_protected(self):
        self.assertFalse(core.is_freshly_downloaded(os.path.join(self.tmp, 'nope')))


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
        # 释放量按磁盘占用报（拿不到簇大小时退回文件字节数），所以这里跟 scan 给的那一项对齐，
        # 不写死 128——小文件按 4 KB 簇对齐会变成 4096，写死就把平台差异钉进断言了
        self.assertEqual(outcome['freed_bytes'], core.usage_bytes(result['orphans'][0]))
        self.assertGreaterEqual(outcome['freed_bytes'], 128)

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

    def test_steam_dll_path_is_optional(self):
        """取消订阅/重新订阅用的 dll 可以留空（自动查找），也可以指定文件或目录"""
        self.write(core.config_path, 'json_path: a.json\nworkshop_dir: ws\n')
        self.assertEqual(core.load_config()['steam_dll_path'], '')

        self.write(core.config_path,
                   'json_path: a.json\nworkshop_dir: ws\nsteam_dll_path: dlls\\steam_api64.dll\n')
        self.assertEqual(
            core.load_config()['steam_dll_path'],
            os.path.join(self.tmp, 'dlls', 'steam_api64.dll'),
        )

    def test_default_template_mentions_the_new_key(self):
        """新配置模板要带上这个键并解释用途，用户才知道它是干什么的"""
        self.assertIn('steam_dll_path:', core.DEFAULT_CONFIG_TEMPLATE)
        self.assertIn('取消订阅', core.DEFAULT_CONFIG_TEMPLATE)


class TestFormatSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(core.format_size(0), '0.00 B')
        self.assertEqual(core.format_size(1023), '1023.00 B')
        self.assertEqual(core.format_size(1024), '1.00 KB')
        self.assertEqual(core.format_size(1024 * 1024 * 3), '3.00 MB')
        self.assertEqual(core.format_size(1024 ** 4 * 2), '2.00 TB')


class TestDirUsage(SandboxTestCase):
    """一次遍历算出两个口径：文件字节数（精确）与磁盘占用（按簇对齐的估算）"""

    def make_tree(self):
        folder = os.path.join(self.tmp, 'wp')
        sub = os.path.join(folder, 'shaders')
        os.makedirs(sub)
        self.write(os.path.join(folder, 'preview.jpg'), 'x' * 100)
        self.write(os.path.join(folder, 'scene.pkg'), 'x' * 5000)
        self.write(os.path.join(sub, 'blob.dxs'), 'x' * 100)
        return folder

    def test_both_numbers_with_cluster_size(self):
        folder = self.make_tree()
        exact, allocated = core.get_dir_usage(folder, cluster_size=4096)
        self.assertEqual(exact, 100 + 5000 + 100)
        # 100 B→4096、5000 B→8192、子目录自身→4096、子目录里的 100 B→4096
        self.assertEqual(allocated, 4096 + 8192 + 4096 + 4096)

    def test_without_cluster_size_only_exact(self):
        """拿不到簇大小时磁盘占用为 0，由调用方退回文件字节数"""
        folder = self.make_tree()
        self.assertEqual(core.get_dir_usage(folder), (5200, 0))
        self.assertEqual(core.get_dir_size(folder), 5200)

    def test_matches_old_get_dir_size(self):
        folder = os.path.join(self.tmp, 'empty')
        os.makedirs(folder)
        self.assertEqual(core.get_dir_usage(folder, cluster_size=4096), (0, 0))


class TestVolumeClusterSize(unittest.TestCase):
    def test_non_windows_returns_zero(self):
        with mock.patch.object(core.sys, 'platform', 'linux'):
            core._cluster_size_cache.clear()
            self.assertEqual(core.volume_cluster_size('D:\\anything'), 0)

    def test_real_volume_does_not_raise(self):
        """真机上要么拿到簇大小（2 的幂），要么拿不到返回 0，都不该抛异常"""
        core._cluster_size_cache.clear()
        size = core.volume_cluster_size(os.path.abspath(os.sep))
        core._cluster_size_cache.clear()
        self.assertGreaterEqual(size, 0)
        if size:
            self.assertEqual(size & (size - 1), 0, f'簇大小应当是 2 的幂: {size}')


class TestUsageBytes(unittest.TestCase):
    def test_prefers_disk_usage(self):
        self.assertEqual(core.usage_bytes({'size_bytes': 128, 'alloc_bytes': 4096}), 4096)

    def test_falls_back_to_file_bytes(self):
        self.assertEqual(core.usage_bytes({'size_bytes': 128, 'alloc_bytes': 0}), 128)
        self.assertEqual(core.usage_bytes({'size_bytes': 128}), 128)

    def test_missing_item_is_zero(self):
        self.assertEqual(core.usage_bytes({}), 0)


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
