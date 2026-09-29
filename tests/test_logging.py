"""日志装载策略的测试：文件日志默认 INFO、--verbose 全量、过期日志启动清理

文件日志之前永远开 DEBUG，HTTP 访问行与 Steam 记录一天能刷出几百行；现在默认
INFO，排障用 --verbose 打开全量。按天命名的日志文件轮转只管单日大小，历史日期
的文件靠启动时的 cleanup_old_logs 兜底回收。

运行：python -m unittest discover -s tests -v
"""

import logging
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from logging.handlers import RotatingFileHandler

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


def file_handlers():
    return [h for h in core.logger.handlers if isinstance(h, RotatingFileHandler)]


class LogSandboxTestCase(unittest.TestCase):
    """把 log_dir 关进临时目录，并恢复日志单例的可变状态"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='wc-log-test-')
        self._saved_dir = core.log_dir
        self._saved_ready = core._logger_ready
        self._saved_verbose = core.verbose
        self._saved_level = core.logger.level
        core.log_dir = self.tmp
        core.verbose = False
        # setup_logger 是一次性单例：先拆掉已有的 handler 才能重新初始化
        for handler in core.logger.handlers[:]:
            handler.close()
            core.logger.removeHandler(handler)
        core._logger_ready = False

    def tearDown(self):
        for handler in core.logger.handlers[:]:
            handler.close()
            core.logger.removeHandler(handler)
        core.log_dir = self._saved_dir
        core.logger.setLevel(self._saved_level)
        core._logger_ready = self._saved_ready
        core.verbose = self._saved_verbose
        rmtree(self.tmp)

    def make_log(self, name, days_old=0):
        """造一个日志文件；days_old 把修改时间回拨，模拟历史文件"""
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('2026-01-01 00:00:00 [INFO] 测试日志\n')
        if days_old:
            stamp = time.time() - days_old * 86400
            os.utime(path, (stamp, stamp))
        return path


class TestFileLogLevel(LogSandboxTestCase):
    def test_defaults_to_info(self):
        """文件日志默认 INFO：访问行这类过程 DEBUG 不再进文件"""
        core.setup_logger()
        handlers = file_handlers()
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].level, logging.INFO)

    def test_verbose_raises_to_debug(self):
        """--verbose 时文件日志回到 DEBUG 全量，排障才有完整过程"""
        core.verbose = True
        core.setup_logger()
        handlers = file_handlers()
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].level, logging.DEBUG)

    def test_console_stays_info_even_when_verbose(self):
        """verbose 只放宽文件端；控制台如果存在，保持 INFO 不跟着刷屏"""
        core.verbose = True
        core.setup_logger()
        consoles = [h for h in core.logger.handlers
                    if isinstance(h, logging.StreamHandler)
                    and not isinstance(h, RotatingFileHandler)]
        for handler in consoles:
            self.assertEqual(handler.level, logging.INFO)


class TestLogRetention(LogSandboxTestCase):
    def test_setup_removes_expired_logs(self):
        """启动初始化时清掉超期的按天日志（含轮转备份），别的文件不动"""
        expired = self.make_log('wallpaper-cleaner_20260101.log', days_old=30)
        expired_backup = self.make_log('wallpaper-cleaner_20260101.log.1', days_old=30)
        recent = self.make_log('wallpaper-cleaner_20991231.log')
        unrelated = self.make_log('unrelated.txt', days_old=30)

        core.setup_logger()

        self.assertFalse(os.path.exists(expired))
        self.assertFalse(os.path.exists(expired_backup))
        self.assertTrue(os.path.exists(recent))
        self.assertTrue(os.path.exists(unrelated))

    def test_cleanup_counts_and_is_idempotent(self):
        self.make_log('wallpaper-cleaner_20260101.log', days_old=30)
        self.make_log('wallpaper-cleaner_20260101.log.2', days_old=30)
        self.make_log('wallpaper-cleaner_20260101.log.7', days_old=30)

        self.assertEqual(core.cleanup_old_logs(keep_days=14), 3)
        # 再跑一遍没有可删的，也不会误删别的
        self.assertEqual(core.cleanup_old_logs(keep_days=14), 0)

    def test_missing_log_dir_is_not_an_error(self):
        """logs/ 还没生成时（首次运行）静默返回 0"""
        core.log_dir = os.path.join(self.tmp, 'no-such-dir')
        self.assertEqual(core.cleanup_old_logs(), 0)


if __name__ == '__main__':
    unittest.main()
