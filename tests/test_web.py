"""面板的单元测试：只在临时沙箱里构造目录树，不碰真实配置与真实 Steam 目录

重点覆盖两类安全边界：
- 删除前的两道校验——刚下载的壁纸即使被扫描列进了待清理，也不能真的被删掉；
- 只读接口（缩略图、打开目录）只认最近一次扫描结果里的目录，不接受请求里的路径。

只读接口用真 HTTP 服务测（临时端口）；删除流程直接调 worker，不经过 HTTP。

运行：python -m unittest discover -s tests -v
"""

import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
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


class DeleteGuardTestCase(unittest.TestCase):
    """按 Steam 的真实目录层级搭一个沙箱：<tmp>\\steamapps\\workshop\\content\\431960"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wc-web-test-')
        self.workshop_dir = os.path.join(
            self.tmp, 'steamapps', 'workshop', 'content', '431960'
        )
        os.makedirs(self.workshop_dir)
        self.json_path = os.path.join(self.tmp, 'workshopcache.json')
        self.write_cache([])

    def tearDown(self):
        rmtree(self.tmp)

    # --- 沙箱辅助 ---

    def write_cache(self, ids):
        payload = {
            'user': 1,
            'version': 2,
            'wallpapers': [
                {'workshopid': i, 'title': f'壁纸 {i}', 'filesizelabel': '1 MB'} for i in ids
            ],
        }
        with open(self.json_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)

    def write_acf(self, installed_ids=(), subscribed_ids=(), age_seconds=0):
        """写一份最小可用的 appworkshop_431960.acf

        installed_ids 写进内容记录（WorkshopItemsInstalled，内容已装完）；
        subscribed_ids 写进订阅记录（WorkshopItemDetails + subscribedby）。
        两者的区别正是「残留」的定义：内容还在、订阅没了。
        """
        lines = ['"AppWorkshop"', '{', '\t"appid"\t\t"431960"', '\t"WorkshopItemsInstalled"', '\t{']
        for wid in installed_ids:
            lines += [f'\t\t"{wid}"', '\t\t{', '\t\t\t"size"\t\t"1536"', '\t\t}']
        lines += ['\t}', '\t"WorkshopItemDetails"', '\t{']
        for wid in subscribed_ids:
            lines += [f'\t\t"{wid}"', '\t\t{', '\t\t\t"subscribedby"\t\t"1508410657"', '\t\t}']
        lines += ['\t}', '}', '']

        path = core.steam_acf_path(self.workshop_dir)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        if age_seconds:
            stamp = time.time() - age_seconds
            os.utime(path, (stamp, stamp))

    def make_folder(self, wid, minutes_old=0):
        """造一个壁纸目录；minutes_old 用来模拟"早就下载好、已经过了保护期"的目录"""
        path = os.path.join(self.workshop_dir, wid)
        os.makedirs(path)
        with open(os.path.join(path, 'data.bin'), 'wb') as f:
            f.write(b'x' * 128)
        if minutes_old:
            stamp = time.time() - minutes_old * 60
            os.utime(path, (stamp, stamp))
        return path

    # --- 流程辅助 ---

    def scan_items(self, wids):
        """按"扫描那一刻"的状态取待清理项（与面板的扫描流程一致）"""
        context = core.load_subscription_context(self.json_path, self.workshop_dir)
        scan = core.scan(
            self.workshop_dir, context['subscriptions'], json_path=self.json_path,
            extra_subscribed=context['protected'], extra_sizes=context['installed_sizes'],
        )
        items = [i for i in scan['orphans'] + scan['unknown'] if i['wid'] in wids]
        return scan, items

    def run_delete(self, scan, items, recycle=False):
        """直接调面板的删除 worker（recycle=False 时是永久删除，所以只在沙箱里用）"""
        state = web.PanelState()
        job = {'lines': []}
        outcome = web._delete_worker(state, job, scan, items, recycle)
        return outcome, job


class TestDeleteGuards(DeleteGuardTestCase):
    def test_newly_downloaded_item_is_never_offered(self):
        """回归：Steam 已记录、WE 缓存还没更新时，扫描就不该把它列为待清理"""
        self.write_cache([])
        self.make_folder('3115163440')
        self.write_acf(installed_ids=['3115163440'], subscribed_ids=['3115163440'])

        scan, items = self.scan_items(['3115163440'])

        self.assertEqual(items, [])
        self.assertEqual([i['wid'] for i in scan['orphans']], [])
        self.assertIn('3115163440', [i['wid'] for i in scan['subscribed']])
        self.assertEqual(scan['missing'], [])

    def test_steam_record_appearing_after_scan_blocks_delete(self):
        """扫描之后才写进 Steam 订阅记录的（重新下载/重新订阅），删除时必须拦下"""
        self.write_cache([])
        folder = self.make_folder('3115163440',
                                 minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        scan, items = self.scan_items(['3115163440'])
        self.assertEqual(len(items), 1)  # 扫描那一刻它还像是残留

        self.write_acf(installed_ids=['3115163440'], subscribed_ids=['3115163440'])
        outcome, job = self.run_delete(scan, items)

        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(outcome['skipped'], ['3115163440'])
        self.assertTrue(os.path.isdir(folder))
        self.assertTrue(any('跳过' in line['text'] for line in job['lines']))

    def test_stale_steam_record_leftover_is_offered_and_deletable(self):
        """回归（神里绫华）：WE 缓存已移除、Steam 记录仍标记为已订阅但更旧 → 必须报成残留"""
        self.write_cache([])
        folder = self.make_folder('3355505516',
                                 minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        self.write_acf(installed_ids=['3355505516'], subscribed_ids=['3355505516'],
                       age_seconds=600)  # Steam 记录比 WE 缓存旧

        scan, items = self.scan_items(['3355505516'])

        self.assertEqual([i['wid'] for i in items], ['3355505516'])
        self.assertEqual(scan['orphans'][0]['kind'], 'orphan')

        outcome, _job = self.run_delete(scan, items)
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertFalse(os.path.exists(folder))

    def test_installed_only_record_is_a_leftover(self):
        """回归：Steam 只在安装记录里留着它（内容还在磁盘上）→ 它是残留，不是已订阅"""
        self.write_cache([])
        folder = self.make_folder('3115163440',
                                 minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        self.write_acf(installed_ids=['3115163440'])  # 只有内容记录，没有订阅记录

        scan, items = self.scan_items(['3115163440'])

        self.assertEqual([i['wid'] for i in items], ['3115163440'])
        outcome, _job = self.run_delete(scan, items)
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertFalse(os.path.exists(folder))

    def test_recent_folder_is_skipped_even_without_steam_record(self):
        """读不到 Steam 记录时的兜底：刚改动过的目录先放过一轮"""
        self.write_cache([])
        folder = self.make_folder('3115163440')  # mtime 就是现在
        scan, items = self.scan_items(['3115163440'])
        self.assertEqual(len(items), 1)

        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(outcome['skipped'], ['3115163440'])
        self.assertTrue(os.path.isdir(folder))

    def test_completed_download_is_not_held_by_grace(self):
        """刚下载完就被取消订阅：Steam 记录已写明内容装完，不该再按"可能正在下载"等 30 分钟"""
        self.write_cache([])
        folder = self.make_folder('3611425904')  # mtime 就是现在
        self.write_acf(installed_ids=['3611425904'])  # 内容记录里有它，订阅记录里没有

        scan, items = self.scan_items(['3611425904'])
        self.assertEqual(len(items), 1)

        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(outcome['skipped'], [])
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertFalse(os.path.exists(folder))

    def test_completion_record_counts_even_when_acf_is_older(self):
        """回归：WE 刚重写缓存、Steam 还没重写 ACF 时，内容记录仍然有效

        这正是"刚取消订阅 → 重新扫描 → 清理选中"报「已清理 0 项、跳过 2 个」的场景：
        ACF 比缓存旧只说明订阅声明过期，不代表内容没装完。
        """
        self.write_cache([])
        folder = self.make_folder('3611425904')  # mtime 就是现在
        self.write_acf(installed_ids=['3611425904'], age_seconds=600)  # ACF 比缓存旧

        scan, items = self.scan_items(['3611425904'])
        self.assertEqual(len(items), 1)

        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(outcome['skipped'], [])
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertFalse(os.path.exists(folder))

    def test_skipped_items_are_reported_by_reason(self):
        """跳过原因要分开报，面板才能说清是「仍在订阅」还是「刚改动过」"""
        self.write_cache([])
        self.make_folder('3611425904')  # 刚改动过、没有完成记录 → 会被守卫拦下
        self.make_folder('2222222222')
        scan, items = self.scan_items(['3611425904', '2222222222'])
        self.assertEqual(len(items), 2)

        self.write_cache(['2222222222'])  # 扫描之后它又被重新订阅了
        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(outcome['skipped_subscribed'], ['2222222222'])
        self.assertEqual(outcome['skipped_fresh'], ['3611425904'])
        self.assertEqual(outcome['skipped'], ['2222222222', '3611425904'])
        self.assertEqual(outcome['deleted'], [])

    def test_folder_resubscribed_after_scan_is_skipped(self):
        self.write_cache([])
        folder = self.make_folder('3115163440',
                                 minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        scan, items = self.scan_items(['3115163440'])

        self.write_cache(['3115163440'])  # 扫描后又被重新订阅
        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(outcome['skipped'], ['3115163440'])
        self.assertTrue(os.path.isdir(folder))

    def test_real_orphan_is_still_deleted(self):
        """保护措施不能把真正的残留也一起放过"""
        self.write_cache([])
        folder = self.make_folder('3115163440',
                                 minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        scan, items = self.scan_items(['3115163440'])
        self.assertEqual(len(items), 1)

        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(outcome['skipped'], [])
        self.assertEqual(len(outcome['deleted']), 1)
        self.assertEqual(outcome['failed'], [])
        self.assertEqual(outcome['freed_bytes'], 128)
        self.assertFalse(os.path.exists(folder))

    def test_other_items_are_not_affected_by_a_protected_one(self):
        """一个被保护，不能连带其它残留也不删"""
        self.write_cache([])
        keep = self.make_folder('3115163440')  # 刚下载，受保护
        stale = self.make_folder('3333333333',
                                minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        scan, items = self.scan_items(['3115163440', '3333333333'])
        self.assertEqual(len(items), 2)

        outcome, _job = self.run_delete(scan, items)

        self.assertEqual([i['wid'] for i in outcome['deleted']], ['3333333333'])
        self.assertEqual(outcome['skipped'], ['3115163440'])
        self.assertTrue(os.path.isdir(keep))
        self.assertFalse(os.path.exists(stale))


class TestEmptyCacheHandling(DeleteGuardTestCase):
    def test_cache_without_wallpapers_key_aborts_scan(self):
        """WE 正在重写缓存时（没有 wallpapers 键）必须报错，而不是把所有目录当成残留"""
        self.write_cache([])
        self.make_folder('3333333333')
        with open(self.json_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'user': 1, 'version': 2}))

        with self.assertRaises(core.SubscriptionError):
            self.scan_items(['3333333333'])

    def test_delete_aborts_when_cache_is_being_rewritten(self):
        self.write_cache([])
        folder = self.make_folder('3333333333',
                                 minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        scan, items = self.scan_items(['3333333333'])
        with open(self.json_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'user': 1, 'version': 2}))

        with self.assertRaises(core.ScanError):
            self.run_delete(scan, items)
        self.assertTrue(os.path.isdir(folder))


class PanelEndpointTestCase(DeleteGuardTestCase):
    """真起一个 HTTP 服务来测只读接口（缩略图、打开目录），用请求验证响应与安全校验"""

    PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'x' * 64

    def setUp(self):
        super().setUp()
        self.state = web.PanelState()
        # 不用 web.create_server：它会初始化全局日志，把日志文件钉在沙箱里，
        # 沙箱一删后面的日志写入就会失败。这里只要一个绑好端口的服务器。
        self.server = web.PanelServer(('127.0.0.1', 0), web.PanelHandler, self.state)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        super().tearDown()

    # --- 沙箱辅助 ---

    def make_preview_folder(self, wid, preview_name='preview.png', declared=''):
        folder = self.make_folder(wid)
        with open(os.path.join(folder, preview_name), 'wb') as f:
            f.write(self.PNG_BYTES)
        if declared:
            with open(os.path.join(folder, 'project.json'), 'w', encoding='utf-8') as f:
                json.dump({'title': f'壁纸 {wid}', 'type': 'scene', 'preview': declared}, f)
        return folder

    def scan_now(self):
        """按"扫描那一刻"的状态跑一次真扫描，并把它放进面板状态（接口只认这份结果）"""
        context = core.load_subscription_context(self.json_path, self.workshop_dir)
        scan = core.scan(
            self.workshop_dir, context['subscriptions'], json_path=self.json_path,
            extra_subscribed=context['protected'], extra_sizes=context['installed_sizes'],
        )
        with self.state.lock:
            self.state.last_scan = scan
        return scan

    def get(self, path, headers=None):
        """返回 (状态码, 响应头, 正文)；4xx 也当正常结果返回，方便断言"""
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}', headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def post(self, path, payload, token=None):
        """发一个 POST；token 传 None 表示不带令牌（用于验证写保护）"""
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-Panel-Token'] = token
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}{path}',
            data=json.dumps(payload).encode('utf-8'), method='POST', headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def token(self):
        return self.state.token


class TestThumbEndpoint(PanelEndpointTestCase):
    def test_serves_the_folders_own_preview(self):
        self.make_preview_folder('3333333333')
        self.scan_now()

        status, headers, body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(body, self.PNG_BYTES)

    def test_gif_preview_keeps_its_type(self):
        """动图按原样发出去，播放交给浏览器"""
        self.make_preview_folder('3333333333', preview_name='preview.gif', declared='preview.gif')
        self.scan_now()

        status, headers, body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/gif')
        self.assertEqual(body, self.PNG_BYTES)

    def test_unknown_wid_is_not_served(self):
        self.make_preview_folder('3333333333')
        self.scan_now()

        status, _headers, body = self.get('/api/thumb?wid=9999999999')

        self.assertEqual(status, 404)
        self.assertIn('最近一次扫描', body.decode('utf-8'))

    def test_item_without_preview_is_not_served(self):
        self.make_folder('3333333333')  # 只有 data.bin，没有预览图
        self.scan_now()

        status, _headers, _body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 404)

    def test_path_traversal_wid_is_rejected(self):
        """指向真实存在的沙箱文件的越界 ID：接口只认扫描结果，不认请求里的路径"""
        self.make_preview_folder('3333333333')
        self.scan_now()

        # 从 workshop 目录往上四级正好是沙箱根，workshopcache.json 就躺在那里
        status, headers, body = self.get(
            '/api/thumb?wid=..%2F..%2F..%2F..%2Fworkshopcache.json')

        self.assertEqual(status, 404)
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertNotIn(b'wallpapers', body)

    def test_request_before_any_scan_is_not_served(self):
        self.make_preview_folder('3333333333')

        status, _headers, _body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 404)

    def test_etag_makes_the_second_request_cheap(self):
        """列表每次重绘都会重建 <img>，有缓存才不用把图重拉一遍"""
        self.make_preview_folder('3333333333')
        self.scan_now()

        _status, headers, _body = self.get('/api/thumb?wid=3333333333')
        etag = headers['ETag']
        self.assertTrue(etag)
        self.assertIn('max-age', headers['Cache-Control'])

        status, _headers, body = self.get(
            '/api/thumb?wid=3333333333', headers={'If-None-Match': etag})

        self.assertEqual(status, 304)
        self.assertEqual(body, b'')

    def test_stale_preview_is_dropped_after_the_file_is_replaced(self):
        """扫描之后文件被删掉，接口不该再去读那个路径"""
        folder = self.make_preview_folder('3333333333')
        self.scan_now()
        os.remove(os.path.join(folder, 'preview.png'))

        status, _headers, _body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 404)


class TestRevealEndpoint(PanelEndpointTestCase):
    """点标题打开目录：只打开最近一次扫描结果里的目录，且必须带令牌"""

    def reveal(self, wid, token=None):
        """调一次接口，返回 (状态码, 正文, open_folder 的 mock)"""
        with mock.patch.object(core, 'open_folder') as opener:
            status, _headers, body = self.post(
                '/api/reveal', {'wid': wid},
                token=self.token() if token is None else token)
        return status, body, opener

    def test_opens_the_folder_of_a_scanned_item(self):
        folder = self.make_folder('3333333333')
        self.scan_now()

        status, body, opener = self.reveal('3333333333')

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode('utf-8')), {'ok': True})
        opener.assert_called_once_with(folder)

    def test_subscribed_item_can_be_opened_too(self):
        """已订阅壁纸的标题同样可点"""
        self.write_cache(['1111111111'])
        folder = self.make_folder('1111111111')
        self.scan_now()

        status, _body, opener = self.reveal('1111111111')

        self.assertEqual(status, 200)
        opener.assert_called_once_with(folder)

    def test_requires_the_panel_token(self):
        self.make_folder('3333333333')
        self.scan_now()

        status, _body, opener = self.reveal('3333333333', token='')

        self.assertEqual(status, 403)
        opener.assert_not_called()

    def test_unknown_wid_is_rejected(self):
        self.make_folder('3333333333')
        self.scan_now()

        status, body, opener = self.reveal('9999999999')

        self.assertEqual(status, 404)
        self.assertIn('最近一次扫描', body.decode('utf-8'))
        opener.assert_not_called()

    def test_request_before_any_scan_is_rejected(self):
        self.make_folder('3333333333')

        status, _body, opener = self.reveal('3333333333')

        self.assertEqual(status, 404)
        opener.assert_not_called()

    def test_folder_deleted_after_scan_reports_clearly(self):
        folder = self.make_folder('3333333333')
        self.scan_now()
        shutil.rmtree(folder)

        status, body, opener = self.reveal('3333333333')

        self.assertEqual(status, 404)
        self.assertIn('已经不在了', body.decode('utf-8'))
        opener.assert_not_called()

    def test_odd_wids_are_rejected(self):
        self.make_folder('3333333333')
        self.scan_now()

        for wid in ('../../../../workshopcache.json', '..', 'a/b', '3333333333/x', 12345):
            status, _body, opener = self.reveal(wid)
            self.assertEqual(status, 404, f'wid={wid!r} 应当被拒绝')
            opener.assert_not_called()

    def test_folder_content_is_untouched(self):
        folder = self.make_folder('3333333333')
        with open(os.path.join(folder, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump({'title': '壁纸', 'type': 'scene'}, f)
        before = sorted(os.listdir(folder))
        self.scan_now()

        status, _body, _opener = self.reveal('3333333333')

        self.assertEqual(status, 200)
        self.assertEqual(sorted(os.listdir(folder)), before)


if __name__ == '__main__':
    unittest.main()
