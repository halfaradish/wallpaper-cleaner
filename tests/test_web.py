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

from wallpaper_cleaner import core, update, web


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
        """造一个壁纸目录；minutes_old 用来模拟"早就下载好、已经过了保护期"的目录

        目录自身和里面的文件一起回拨：新鲜度看的是内容的时间（Steam 删除内容时也会
        刷新目录自身的 mtime，只看目录会把刚被清空的残留误判成正在下载）。
        """
        path = os.path.join(self.workshop_dir, wid)
        os.makedirs(path)
        data = os.path.join(path, 'data.bin')
        with open(data, 'wb') as f:
            f.write(b'x' * 128)
        if minutes_old:
            stamp = time.time() - minutes_old * 60
            os.utime(data, (stamp, stamp))
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

    def run_delete(self, scan, items, recycle=False, just_resubscribed=()):
        """直接调面板的删除 worker（recycle=False 时是永久删除，所以只在沙箱里用）"""
        state = web.PanelState()
        job = {'lines': []}
        outcome = web._delete_worker(state, job, scan, items, recycle, just_resubscribed)
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

    def test_shell_left_by_steam_is_deletable(self):
        """Steam 刚清空过的残留：目录 mtime 很新，里面的内容却还是下载那一刻的

        真实场景：取消订阅后启动 Steam，它把内容删掉、只留下 WE 写的着色器缓存，
        同时刷新了目录自身的 mtime。这样的空壳不该被当作"正在下载"再等 30 分钟。
        """
        self.write_cache([])
        path = os.path.join(self.workshop_dir, '3115163440')
        cache = os.path.join(path, 'shaders')
        os.makedirs(cache)
        blob = os.path.join(cache, 'blob.dxs')
        with open(blob, 'wb') as f:
            f.write(b'cache')
        old = time.time() - core.FRESH_DOWNLOAD_GRACE_SECONDS - 60
        for target in (blob, cache):
            os.utime(target, (old, old))
        os.utime(path, None)  # Steam 的删除动作只刷新目录自身

        scan, items = self.scan_items(['3115163440'])
        self.assertEqual(len(items), 1)

        outcome, _job = self.run_delete(scan, items)

        self.assertEqual(len(outcome['deleted']), 1)
        self.assertEqual(outcome['skipped'], [])
        self.assertFalse(os.path.exists(path))

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
        # 释放量按磁盘占用报，不写死 128（小文件按 4 KB 簇对齐会变成 4096）
        self.assertEqual(outcome['freed_bytes'], core.usage_bytes(items[0]))
        self.assertGreaterEqual(outcome['freed_bytes'], 128)
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

    def make_we_cache(self, wid=None):
        """按真实布局造 WE 的目录：<tmp>\\wallpaper_engine\\{bin\\workshopcache.json, ui\\thumbnails}

        返回 (缓存文件路径, 缩略图路径)；wid 为 None 时不放缩略图。
        调用方把它当成本次的 json_path，扫描结果里记的就会是这个位置。
        """
        we_root = os.path.join(self.tmp, 'wallpaper_engine')
        thumbs = os.path.join(we_root, 'ui', 'thumbnails')
        os.makedirs(thumbs)
        json_path = os.path.join(we_root, 'bin', 'workshopcache.json')
        os.makedirs(os.path.dirname(json_path))
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump({'wallpapers': []}, f)
        thumb = ''
        if wid:
            thumb = os.path.join(thumbs, f'ws_{wid}_thumb.jpg')
            with open(thumb, 'wb') as f:
                f.write(b'\xff\xd8\xff' + b'x' * 64)  # 只要求 JPEG 的魔数与后缀
        return json_path, thumb

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

    def test_falls_back_to_the_we_thumbnail_cache(self):
        """目录里的预览图被 Steam 连着内容删掉后，发 WE 缓存里那张浏览缩略图"""
        self.make_folder('3333333333')  # 只剩缓存文件的空壳，没有预览图
        self.json_path, _thumb = self.make_we_cache('3333333333')
        self.scan_now()

        status, headers, body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertTrue(body.startswith(b'\xff\xd8\xff'))

    def test_folders_own_preview_wins_over_the_cache(self):
        """目录里有作者发的原图就用原图，不去动缓存里那张小图"""
        self.make_preview_folder('3333333333')
        self.json_path, _thumb = self.make_we_cache('3333333333')
        self.scan_now()

        status, headers, body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(body, self.PNG_BYTES)

    def test_other_ids_thumbnail_in_the_cache_is_not_borrowed(self):
        """缓存目录里有的只是别人的图：这个 ID 没有就还是 404"""
        self.make_folder('3333333333')
        self.json_path, _thumb = self.make_we_cache('9999999999')
        self.scan_now()

        status, _headers, _body = self.get('/api/thumb?wid=3333333333')

        self.assertEqual(status, 404)

    def test_unstandard_json_path_is_not_an_error(self):
        """缓存文件不在 WE 的标准目录里时推不出缩略图目录：404，而不是 500"""
        self.make_folder('3333333333')
        self.scan_now()
        with self.state.lock:
            self.state.last_scan['json_path'] = os.path.join(
                self.tmp, 'xvault', 'workshopcache.json')

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

    def test_content_missing_shell_can_be_opened(self):
        """取不到标题的空壳（内容已被 Steam 清理）同样要能打开目录

        面板给这类行显示的是占位文字「打开目录」：能不能打开目录不该由标题决定。
        """
        path = os.path.join(self.workshop_dir, '3333333333')
        os.makedirs(path)
        self.scan_now()

        status, _body, opener = self.reveal('3333333333')

        self.assertEqual(status, 200)
        opener.assert_called_once_with(path)

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


class SteamApiStub:
    """把 steamapi 整体打桩：不加载任何真实 dll，也不连真实 Steam"""

    def __init__(self, case, subscribed=(), unsubscribed_ok=True, subscribed_ok=True,
                 connect_ok=True, connect_reason='ok', connect_detail='', confirm=True):
        self.subscribed = set(subscribed)
        self.confirm = confirm
        patches = [
            mock.patch.object(web.steamapi, 'connect',
                              return_value=(connect_ok, connect_reason, connect_detail)),
            mock.patch.object(web.steamapi, 'get_subscribed',
                              side_effect=lambda: set(self.subscribed)),
            mock.patch.object(web.steamapi, 'unsubscribe', return_value=unsubscribed_ok),
            mock.patch.object(web.steamapi, 'subscribe', return_value=subscribed_ok),
            mock.patch.object(web.steamapi, 'confirm_unsubscribed',
                              side_effect=lambda wids, **kw: set(wids) if confirm else set()),
            mock.patch.object(web.steamapi, 'confirm_subscribed',
                              side_effect=lambda wids, **kw: set(wids) if confirm else set()),
            mock.patch.object(web.steamapi, 'shutdown'),
        ]
        self.mocks = {}
        for patcher in patches:
            self.mocks[patcher.attribute] = patcher.start()
            case.addCleanup(patcher.stop)

    def unsubscribed_calls(self):
        return [call.args[0] for call in self.mocks['unsubscribe'].call_args_list]

    def subscribed_calls(self):
        return [call.args[0] for call in self.mocks['subscribe'].call_args_list]


class SteamWorkerTestCase(DeleteGuardTestCase):
    """取消订阅/重新订阅 worker 的公共沙箱：路径与 Steam 全部打桩"""

    def make_state(self):
        state = web.PanelState()
        paths = {
            'json_path': self.json_path,
            'workshop_dir': self.workshop_dir,
            'steam_dll_path': '',
        }
        patcher = mock.patch.object(web, 'resolve_paths', return_value=paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        return state

    def scan_all(self):
        context = core.load_subscription_context(self.json_path, self.workshop_dir)
        return core.scan(
            self.workshop_dir, context['subscriptions'], json_path=self.json_path,
            extra_subscribed=context['protected'], extra_sizes=context['installed_sizes'],
        )

    def run_worker(self, worker, *args):
        job = {'lines': []}
        return worker(self.state, job, *args), job


class TestUnsubscribeWorker(SteamWorkerTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.make_state()

    def test_only_mode_leaves_the_files_alone(self):
        """「仅取消订阅」不主动删任何东西：文件留给 Steam 自己处理"""
        self.write_cache(['1111111111'])
        folder = self.make_folder('1111111111')
        stub = SteamApiStub(self, subscribed=['1111111111'])
        scan = self.scan_all()

        outcome, _job = self.run_worker(
            web._unsubscribe_worker, scan, scan['subscribed'], 'only', False)

        self.assertEqual(outcome['unsubscribed'], ['1111111111'])
        self.assertEqual(stub.unsubscribed_calls(), ['1111111111'])
        self.assertEqual(outcome['deleted'], [])
        self.assertTrue(os.path.isdir(folder))
        self.assertIn('1111111111', self.state.session_unsubscribed)

    def test_with_delete_mode_removes_the_folder_after_confirmation(self):
        self.write_cache(['1111111111'])
        folder = self.make_folder('1111111111')
        SteamApiStub(self, subscribed=['1111111111'])
        scan = self.scan_all()

        outcome, _job = self.run_worker(
            web._unsubscribe_worker, scan, scan['subscribed'], 'with_delete', False)

        self.assertEqual([i['wid'] for i in outcome['deleted']], ['1111111111'])
        self.assertFalse(os.path.exists(folder))

    def test_unconfirmed_item_is_never_deleted(self):
        """Steam 还没确认生效就不删本地：此时它可能仍是订阅状态，删了会被重新下载"""
        self.write_cache(['1111111111'])
        folder = self.make_folder('1111111111')
        SteamApiStub(self, subscribed=['1111111111'], confirm=False)
        scan = self.scan_all()

        outcome, _job = self.run_worker(
            web._unsubscribe_worker, scan, scan['subscribed'], 'with_delete', False)

        self.assertEqual(outcome['unconfirmed'], ['1111111111'])
        self.assertEqual(outcome['deleted'], [])
        self.assertTrue(os.path.isdir(folder))
        self.assertNotIn('1111111111', self.state.session_unsubscribed)

    def test_folder_already_gone_counts_as_done(self):
        """Steam 自己把目录删掉了：这是目的达成，不是失败"""
        self.write_cache(['1111111111'])
        folder = self.make_folder('1111111111')
        SteamApiStub(self, subscribed=['1111111111'])
        scan = self.scan_all()
        shutil.rmtree(folder)  # 扫描之后、我们动手之前，Steam 把它删了

        outcome, _job = self.run_worker(
            web._unsubscribe_worker, scan, scan['subscribed'], 'with_delete', False)

        self.assertEqual(outcome['already_gone'], 1)
        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(outcome['delete_failed'], [])

    def test_item_no_longer_subscribed_is_skipped(self):
        """扫描之后用户在 Steam 里自己取消订阅了：跳过，不当失败"""
        self.write_cache(['1111111111'])
        self.make_folder('1111111111')
        stub = SteamApiStub(self, subscribed=[])
        scan = self.scan_all()

        outcome, job = self.run_worker(
            web._unsubscribe_worker, scan, scan['subscribed'], 'only', False)

        self.assertEqual(stub.unsubscribed_calls(), [])
        self.assertEqual(outcome['skipped'], ['1111111111'])
        self.assertEqual(outcome['failed'], [])
        self.assertTrue(any('跳过' in line['text'] for line in job['lines']))

    def test_one_failure_does_not_stop_the_rest(self):
        self.write_cache(['1111111111', '2222222222'])
        self.make_folder('1111111111')
        self.make_folder('2222222222')
        stub = SteamApiStub(self, subscribed=['1111111111', '2222222222'])
        stub.mocks['unsubscribe'].side_effect = [False, True]
        scan = self.scan_all()

        outcome, _job = self.run_worker(
            web._unsubscribe_worker, scan, scan['subscribed'], 'only', False)

        self.assertEqual(len(outcome['failed']), 1)
        self.assertEqual(outcome['unsubscribed'], ['2222222222'])

    def test_steam_failure_raises_a_readable_error(self):
        self.write_cache(['1111111111'])
        self.make_folder('1111111111')
        stub = SteamApiStub(self, connect_ok=False,
                            connect_reason='steam_not_running',
                            connect_detail='Steam 客户端没有在运行')
        scan = self.scan_all()

        with self.assertRaises(core.ScanError) as ctx:
            self.run_worker(web._unsubscribe_worker, scan, scan['subscribed'], 'only', False)

        self.assertIn('Steam 客户端没有在运行', str(ctx.exception))
        stub.mocks['shutdown'].assert_called()  # 失败也要断开连接


class TestResubscribeWorker(SteamWorkerTestCase):
    def setUp(self):
        super().setUp()
        self.state = self.make_state()

    def test_subscribes_the_leftover_folder(self):
        self.write_cache([])
        folder = self.make_folder('3115163440')
        stub = SteamApiStub(self, subscribed=[])
        scan = self.scan_all()

        outcome, _job = self.run_worker(web._resubscribe_worker, scan, scan['orphans'])

        self.assertEqual(outcome['resubscribed'], ['3115163440'])
        self.assertEqual(stub.subscribed_calls(), ['3115163440'])
        self.assertIn('3115163440', self.state.session_resubscribed)
        self.assertTrue(os.path.isdir(folder))  # 只是重新订阅，不动文件

    def test_already_subscribed_item_is_skipped(self):
        self.write_cache([])
        self.make_folder('3115163440')
        stub = SteamApiStub(self, subscribed=['3115163440'])
        scan = self.scan_all()

        outcome, _job = self.run_worker(web._resubscribe_worker, scan, scan['orphans'])

        self.assertEqual(stub.subscribed_calls(), [])
        self.assertEqual(outcome['skipped'], ['3115163440'])
        self.assertEqual(outcome['resubscribed'], [])


class TestResubscribeBlocksDelete(DeleteGuardTestCase):
    def test_just_resubscribed_folder_is_skipped(self):
        """刚重新订阅的目录不能删：本地订阅记录还没刷新，只有面板自己知道它已经回来了"""
        self.write_cache([])
        folder = self.make_folder('3115163440',
                                  minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        scan, items = self.scan_items(['3115163440'])
        self.assertEqual(len(items), 1)

        outcome, job = self.run_delete(scan, items, just_resubscribed=['3115163440'])

        self.assertEqual(outcome['deleted'], [])
        self.assertEqual(outcome['skipped_resubscribed'], ['3115163440'])
        self.assertTrue(os.path.isdir(folder))
        self.assertTrue(any('刚重新订阅' in line['text'] for line in job['lines']))


class TestUnsubscribeEndpoint(PanelEndpointTestCase):
    """取消订阅接口：令牌、扫描新鲜度、白名单、模式枚举都要挡住"""

    def setUp(self):
        super().setUp()
        self.write_cache(['1111111111'])
        self.make_folder('1111111111')
        self.scan = self.scan_now()

    def unsubscribe(self, payload, token=None):
        return self.post('/api/unsubscribe', payload,
                         token=self.token() if token is None else token)

    def payload(self, **overrides):
        data = {
            'wids': ['1111111111'],
            'scan_id': self.scan['scanned_at'],
            'mode': 'only',
            'recycle': True,
        }
        data.update(overrides)
        return data

    def test_requires_the_panel_token(self):
        status, _headers, body = self.unsubscribe(self.payload(), token='')
        self.assertEqual(status, 403)
        self.assertIn('令牌', body.decode('utf-8'))

    def test_requires_a_scan(self):
        with self.state.lock:
            self.state.last_scan = None
        status, _headers, body = self.unsubscribe(self.payload())
        self.assertEqual(status, 409)
        self.assertIn('先扫描', body.decode('utf-8'))

    def test_rejects_a_stale_scan_id(self):
        status, _headers, body = self.unsubscribe(self.payload(scan_id='2000-01-01 00:00:00'))
        self.assertEqual(status, 409)
        self.assertIn('已过期', body.decode('utf-8'))

    def test_rejects_an_unknown_mode(self):
        status, _headers, body = self.unsubscribe(self.payload(mode='burn-it'))
        self.assertEqual(status, 400)
        self.assertIn('方式', body.decode('utf-8'))

    def test_rejects_wids_outside_the_subscribed_list(self):
        status, _headers, body = self.unsubscribe(self.payload(wids=['9999999999']))
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body.decode('utf-8'))['rejected'], ['9999999999'])

    def test_starts_a_job_for_a_valid_request(self):
        with mock.patch.object(web, '_unsubscribe_worker', return_value={'mode': 'only'}) as worker:
            status, _headers, body = self.unsubscribe(self.payload())

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode('utf-8'))['job_id'])
        self.assertEqual(worker.call_args.args[4], 'only')


class TestResubscribeEndpoint(PanelEndpointTestCase):
    def setUp(self):
        super().setUp()
        self.write_cache([])
        self.make_folder('3115163440')
        self.scan = self.scan_now()

    def resubscribe(self, payload, token=None):
        return self.post('/api/resubscribe', payload,
                         token=self.token() if token is None else token)

    def test_rejects_a_wid_that_is_not_a_leftover(self):
        status, _headers, body = self.resubscribe(
            {'wids': ['9999999999'], 'scan_id': self.scan['scanned_at']})
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body.decode('utf-8'))['rejected'], ['9999999999'])

    def test_starts_a_job_for_a_valid_request(self):
        with mock.patch.object(web, '_resubscribe_worker', return_value={}) as worker:
            status, _headers, body = self.resubscribe(
                {'wids': ['3115163440'], 'scan_id': self.scan['scanned_at']})

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode('utf-8'))['job_id'])
        self.assertEqual(worker.call_args.args[3][0]['wid'], '3115163440')


class TestSteamEndpoints(PanelEndpointTestCase):
    """Steam 状态与跳转接口：探测不占任务槽，跳转要挡非法 ID"""

    def test_probe_starts_a_background_check(self):
        with mock.patch.object(web.steamapi, 'probe',
                               return_value={'status': 'ok', 'reason': 'ok', 'detail': '',
                                             'hint': '', 'subscribed_count': 3,
                                             'dll_path': 'x.dll', 'checked_at': '10:00:00'}):
            status, _headers, body = self.post('/api/steam/probe', {}, token=self.token())

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body.decode('utf-8'))['ok'])

    def test_probe_failure_does_not_touch_the_job_slot(self):
        """探测跑在后台线程里：不能占住唯一那个任务槽，否则扫描会被挡住"""
        with mock.patch.object(web.steamapi, 'probe', return_value={'status': 'unavailable'}):
            self.post('/api/steam/probe', {}, token=self.token())
        self.assertIsNone(self.state.active_job)

    def test_launch_opens_the_steam_protocol(self):
        with mock.patch.object(core, 'open_url') as opener:
            status, _headers, _body = self.post('/api/steam/launch', {}, token=self.token())

        self.assertEqual(status, 200)
        opener.assert_called_once_with('steam://open/main')

    def test_manual_page_opens_the_workshop_item(self):
        self.make_folder('3333333333')
        self.scan_now()

        with mock.patch.object(core, 'open_url') as opener:
            status, _headers, _body = self.post('/api/steam/page', {'wid': '3333333333'},
                                                token=self.token())

        self.assertEqual(status, 200)
        opener.assert_called_once_with('steam://url/CommunityFilePage/3333333333')

    def test_manual_page_rejects_unknown_or_illegal_ids(self):
        self.make_folder('3333333333')
        self.scan_now()

        for wid in ('9999999999', '../../etc/passwd', '..', 12345):
            with mock.patch.object(core, 'open_url') as opener:
                status, _headers, _body = self.post('/api/steam/page', {'wid': wid},
                                                    token=self.token())
            self.assertEqual(status, 404, f'wid={wid!r} 应当被拒绝')
            opener.assert_not_called()

    def test_state_exposes_the_steam_status(self):
        with mock.patch.object(web, 'start_steam_probe', return_value=False):
            with self.state.lock:
                self.state.steam = {'status': 'unavailable', 'reason': 'no_dll', 'hint': '缺 dll'}
                self.state.session_unsubscribed = {'1'}
                self.state.session_resubscribed = {'2'}

        status, _headers, body = self.get('/api/state')
        data = json.loads(body.decode('utf-8'))

        self.assertEqual(status, 200)
        self.assertEqual(data['steam']['status'], 'unavailable')
        self.assertEqual(data['session_unsubscribed'], ['1'])
        self.assertEqual(data['session_resubscribed'], ['2'])

    def test_state_scan_carries_both_size_measures(self):
        """面板要显示「标注大小 + 磁盘占用」，所以扫描结果里两个口径都要带上"""
        self.write_cache([])
        self.make_folder('3333333333',
                         minutes_old=core.FRESH_DOWNLOAD_GRACE_SECONDS // 60 + 5)
        self.write_acf(installed_ids=['3333333333'])  # 内容记录还在 → 标注大小有值
        self.scan_now()

        status, _headers, body = self.get('/api/state')
        data = json.loads(body.decode('utf-8'))
        entry = data['scan']['orphans'][0]

        self.assertEqual(status, 200)
        self.assertEqual(entry['size_bytes'], 128)
        self.assertGreaterEqual(entry['alloc_bytes'], entry['size_bytes'])
        self.assertEqual(entry['declared_bytes'], 1536)  # write_acf 里写的 size
        self.assertEqual(entry['declared_size'], core.format_size(1536))
        self.assertGreaterEqual(data['scan']['orphan_usage_bytes'], 128)


class TestUpdateEndpoints(PanelEndpointTestCase):
    """检查更新与打开链接：检查走后台线程不占任务槽，跳转地址只由服务端决定"""

    def wait_until(self, predicate, timeout=5.0):
        """后台线程写状态是异步的，等它落地；超时也不抛异常，交给后面的断言报错"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_check_runs_in_the_background_and_lands_in_state(self):
        result = dict(update.empty_state(), status='outdated', latest='0.9.0', current='0.4.0')

        with mock.patch.object(web.update, 'check', return_value=result) as checker:
            status, _headers, _body = self.post('/api/update/check', {}, token=self.token())

            self.assertEqual(status, 200)
            # 查更新跑在后台线程里：不能占住唯一那个任务槽，否则扫描会被挡住
            self.assertIsNone(self.state.active_job)
            self.assertTrue(self.wait_until(lambda: self.state.update_status()['status'] != 'checking'))

        checker.assert_called_once()
        self.assertEqual(self.state.update_status()['status'], 'outdated')
        self.assertEqual(self.state.update_status()['latest'], '0.9.0')

    def test_second_check_while_one_is_running_is_refused(self):
        entered, release = threading.Event(), threading.Event()

        def slow_check(_current):
            entered.set()
            release.wait(timeout=5)
            return dict(update.empty_state(), status='latest', latest='0.4.0')

        with mock.patch.object(web.update, 'check', side_effect=slow_check):
            first, _headers, _body = self.post('/api/update/check', {}, token=self.token())
            self.assertEqual(first, 200)
            self.assertTrue(entered.wait(timeout=5), '后台线程应当已经进了检查')

            second, _headers, body = self.post('/api/update/check', {}, token=self.token())
            release.set()

        self.assertEqual(second, 409)
        self.assertIn('正在检查', json.loads(body.decode('utf-8'))['error'])

    def test_state_exposes_the_update_status(self):
        with self.state.lock:
            self.state.update = dict(update.empty_state(), status='outdated', latest='0.9.0',
                                     current='0.4.0', url=f'{update.REPO_URL}/releases/tag/v0.9.0')

        status, _headers, body = self.get('/api/state')
        data = json.loads(body.decode('utf-8'))

        self.assertEqual(status, 200)
        self.assertEqual(data['update']['status'], 'outdated')
        self.assertEqual(data['update']['latest'], '0.9.0')

    def test_open_repo_opens_the_repository_page(self):
        with mock.patch.object(core, 'open_url') as opener:
            status, _headers, body = self.post('/api/open', {'target': 'repo'}, token=self.token())

        self.assertEqual(status, 200)
        opener.assert_called_once_with(update.REPO_URL)
        self.assertEqual(json.loads(body.decode('utf-8'))['url'], update.REPO_URL)

    def test_open_release_prefers_the_page_from_the_last_check(self):
        url = f'{update.REPO_URL}/releases/tag/v0.9.0'
        with self.state.lock:
            self.state.update = dict(update.empty_state(), status='outdated', url=url)

        with mock.patch.object(core, 'open_url') as opener:
            status, _headers, _body = self.post('/api/open', {'target': 'release'}, token=self.token())

        self.assertEqual(status, 200)
        opener.assert_called_once_with(url)

    def test_open_release_falls_back_when_there_is_no_trusted_page(self):
        """没查过更新，或者缓存里那个地址不是本仓库的，都要回落到最新 Release 页"""
        for cached in ('', 'https://evil.example.com/wallpaper-cleaner/releases'):
            with self.state.lock:
                self.state.update = dict(update.empty_state(), status='latest', url=cached)

            with mock.patch.object(core, 'open_url') as opener:
                status, _headers, _body = self.post('/api/open', {'target': 'release'},
                                                    token=self.token())

            self.assertEqual(status, 200, f'cached={cached!r}')
            opener.assert_called_once_with(update.LATEST_RELEASE_URL)

    def test_open_rejects_unknown_targets(self):
        """回归守卫：这个接口最后会让系统去打开地址，不能变成"面板能打开任何东西"的通道"""
        for target in ('https://evil.example.com', 'file:///C:/Windows/System32/calc.exe',
                       '', None, 123, ['repo']):
            with mock.patch.object(core, 'open_url') as opener:
                status, _headers, _body = self.post('/api/open', {'target': target},
                                                    token=self.token())

            self.assertEqual(status, 400, f'target={target!r} 应当被拒绝')
            opener.assert_not_called()

    def test_open_reports_a_system_failure(self):
        with mock.patch.object(core, 'open_url', side_effect=OSError('没有可用的默认浏览器')):
            status, _headers, body = self.post('/api/open', {'target': 'repo'}, token=self.token())

        self.assertEqual(status, 500)
        self.assertIn('没有可用的默认浏览器', json.loads(body.decode('utf-8'))['error'])

    def test_write_endpoints_require_the_panel_token(self):
        with mock.patch.object(core, 'open_url') as opener:
            check, _headers, _body = self.post('/api/update/check', {}, token=None)
            opened, _headers, _body = self.post('/api/open', {'target': 'repo'}, token=None)

        self.assertEqual(check, 403)
        self.assertEqual(opened, 403)
        opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
