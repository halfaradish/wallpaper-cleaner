"""面板删除流程的单元测试：只在临时沙箱里构造目录树，不启动 HTTP 服务、不碰真实目录

重点覆盖「删除前的两道安全校验」——刚下载的壁纸即使被扫描列进了待清理，也不能真的被删掉。

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


if __name__ == '__main__':
    unittest.main()
