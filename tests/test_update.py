"""update 模块单元测试：全程离线，用假的 opener 替换 urllib 请求

不联网、不打开浏览器、不拉子进程——这里验证的是：
- 版本号比较：v 前缀、后缀、位数不齐（0.4 与 0.4.0）、按数字而不是按字符串比；
- check() 的三种结论：有新版本 / 已是最新 / 本地这份比线上还新；
- 各种失败（404、403、连不上、超时、返回内容不是 JSON）都变成 failed 状态，
  而不是抛异常——面板不能因为一次联网失败就崩掉；
- 请求带上了 GitHub 要的 User-Agent 与超时时间；
- Release 页地址只信本仓库的链接，其余一律回落到规范地址。

运行：python -m unittest discover -s tests -v
"""

import io
import json
import os
import socket
import ssl
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wallpaper_cleaner import update


class FakeResponse:
    """假响应：支持 with 语法与 read()，够 _fetch 用"""

    def __init__(self, body):
        self.body = body if isinstance(body, bytes) else str(body).encode('utf-8')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def opener_for(payload=None, error=None):
    """替掉 urllib 的 opener：返回假响应，或者抛出指定异常"""
    if error is not None:
        return mock.Mock(side_effect=error)
    if isinstance(payload, (bytes, str)):
        return mock.Mock(return_value=FakeResponse(payload))
    return mock.Mock(return_value=FakeResponse(json.dumps(payload)))


def release_payload(tag='v0.5.0', **overrides):
    payload = {
        'tag_name': tag,
        'name': f'wallpaper-cleaner {tag}',
        'html_url': f'{update.REPO_URL}/releases/tag/{tag}',
        'published_at': '2026-09-01T12:30:00Z',
        'body': '### 更新内容\n\n- 修好了取消订阅后残留的清理\n- 面板多了检查更新',
    }
    payload.update(overrides)
    return payload


class TestVersionCompare(unittest.TestCase):
    """版本号解析与比较：只认第一段数字，后缀不参与"""

    def test_reads_the_numeric_part(self):
        self.assertEqual(update._version_tuple('v1.2.3'), (1, 2, 3))
        self.assertEqual(update._version_tuple('0.4'), (0, 4))
        self.assertEqual(update._version_tuple('1.2.3-beta.1'), (1, 2, 3))

    def test_returns_none_when_there_is_no_number(self):
        for text in ('nightly', 'v', '', None, '未知', 'nightly-2026', 'release-1.2'):
            self.assertIsNone(update._version_tuple(text), f'{text!r} 不该解析出版本号')

    def test_compares_numbers_not_strings(self):
        # 按字符串比会得出 '0.10.0' < '0.9.0'，那才是错的
        self.assertTrue(update.is_newer('0.10.0', '0.9.0'))
        self.assertTrue(update.is_newer('0.4.10', '0.4.9'))

    def test_newer_versions_are_recognised(self):
        self.assertTrue(update.is_newer('0.5.0', '0.4.0'))
        self.assertTrue(update.is_newer('0.4.1', '0.4.0'))
        self.assertTrue(update.is_newer('v0.5.0', '0.4.0'))
        self.assertTrue(update.is_newer('1.0.0', '0.9.9'))

    def test_same_or_older_versions_are_not_updates(self):
        self.assertFalse(update.is_newer('0.4.0', '0.4.0'))
        self.assertFalse(update.is_newer('0.4', '0.4.0'))       # 位数不齐按补零算相等
        self.assertFalse(update.is_newer('0.4.0', '0.5.0'))     # 本地这份比线上还新

    def test_unparsable_versions_never_claim_an_update(self):
        self.assertFalse(update.is_newer('nightly', '0.4.0'))
        self.assertFalse(update.is_newer('0.5.0', ''))

    def test_display_version_drops_the_v_prefix(self):
        self.assertEqual(update._clean_version('v0.5.0'), '0.5.0')
        self.assertEqual(update._clean_version('V0.5.0'), '0.5.0')
        self.assertEqual(update._clean_version('0.5.0'), '0.5.0')
        self.assertEqual(update._clean_version(''), '')


class TestCheck(unittest.TestCase):
    """check()：结论、失败归类、请求本身该带什么"""

    def test_reports_a_newer_release(self):
        opener = opener_for(release_payload())

        result = update.check('0.4.0', opener=opener)

        self.assertEqual(result['status'], 'outdated')
        self.assertEqual(result['latest'], '0.5.0')
        self.assertEqual(result['current'], '0.4.0')
        self.assertEqual(result['url'], f'{update.REPO_URL}/releases/tag/v0.5.0')
        self.assertEqual(result['published_at'], '2026-09-01')
        self.assertIn('修好了取消订阅后残留的清理', result['notes'])
        self.assertRegex(result['checked_at'], r'^\d\d:\d\d:\d\d$')

    def test_reports_being_up_to_date(self):
        result = update.check('0.4.0', opener=opener_for(release_payload('v0.4.0')))

        self.assertEqual(result['status'], 'latest')
        self.assertEqual(result['latest'], '0.4.0')

    def test_local_build_ahead_of_the_release_counts_as_up_to_date(self):
        # 发版前把版本号先改上去的时候，不该提示"有新版本 0.4.0"
        result = update.check('0.5.0', opener=opener_for(release_payload('v0.4.0')))

        self.assertEqual(result['status'], 'latest')

    def test_request_carries_user_agent_and_timeout(self):
        """GitHub 不给 User-Agent 会直接 403；没有超时则可能一直转圈"""
        opener = opener_for(release_payload())

        update.check('0.4.0', opener=opener)

        request, timeout = opener.call_args.args[0], opener.call_args.kwargs['timeout']
        headers = {name.lower(): value for name, value in request.headers.items()}
        self.assertEqual(request.full_url, update.API_LATEST)
        self.assertEqual(headers['user-agent'], update.USER_AGENT)
        self.assertEqual(timeout, update.TIMEOUT)

    def test_default_opener_is_urllib(self):
        """线上路径不能只在注入 opener 时才成立"""
        with mock.patch.object(update.urllib.request, 'urlopen',
                               return_value=FakeResponse(json.dumps(release_payload()))) as urlopen:
            result = update.check('0.4.0')

        self.assertEqual(result['status'], 'outdated')
        self.assertTrue(urlopen.called)

    def test_missing_release_is_reported(self):
        error = urllib.error.HTTPError(update.API_LATEST, 404, 'Not Found', {}, io.BytesIO(b''))

        result = update.check('0.4.0', opener=opener_for(error=error))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('还没有发布', result['error'])

    def test_rate_limit_is_reported(self):
        for code in (403, 429):
            error = urllib.error.HTTPError(update.API_LATEST, code, 'Forbidden', {}, io.BytesIO(b''))

            result = update.check('0.4.0', opener=opener_for(error=error))

            self.assertEqual(result['status'], 'failed')
            self.assertIn('访问受限', result['error'])

    def test_other_http_errors_keep_the_status_code(self):
        error = urllib.error.HTTPError(update.API_LATEST, 500, 'Server Error', {}, io.BytesIO(b''))

        result = update.check('0.4.0', opener=opener_for(error=error))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('500', result['error'])

    def test_network_failure_is_reported(self):
        error = urllib.error.URLError(socket.gaierror('getaddrinfo failed'))

        result = update.check('0.4.0', opener=opener_for(error=error))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('连不上 GitHub', result['error'])

    def test_certificate_failure_says_what_it_really_is(self):
        """回归守卫：本机代理/安全软件换了证书时，"检查网络"会把人指错方向"""
        error = urllib.error.URLError(ssl.SSLCertVerificationError('certificate verify failed'))

        result = update.check('0.4.0', opener=opener_for(error=error))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('证书', result['error'])

    def test_timeout_is_reported(self):
        result = update.check('0.4.0', opener=opener_for(error=TimeoutError('timed out')))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('连不上 GitHub', result['error'])

    def test_broken_response_body_is_reported(self):
        result = update.check('0.4.0', opener=opener_for('<html>不是 JSON</html>'))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('检查更新失败', result['error'])

    def test_unexpected_errors_never_escape(self):
        """回归守卫：检查更新绝不能让面板崩掉，什么异常都必须在 check 里收住"""
        result = update.check('0.4.0', opener=opener_for(error=RuntimeError('接口又改版了')))

        self.assertEqual(result['status'], 'failed')
        self.assertIn('接口又改版了', result['error'])

    def test_missing_or_unparsable_tags_are_reported(self):
        for tag in ('', 'nightly-2026'):
            result = update.check('0.4.0', opener=opener_for(release_payload(tag)))

            self.assertEqual(result['status'], 'failed', f'tag={tag!r} 应当算是查不了')
            self.assertNotIn('Traceback', result['error'])

    def test_long_notes_are_truncated(self):
        result = update.check('0.4.0', opener=opener_for(release_payload(body='内' * (update.MAX_NOTES + 500))))

        self.assertEqual(result['status'], 'outdated')
        self.assertLess(len(result['notes']), update.MAX_NOTES + 200)
        self.assertIn('截断', result['notes'])

    def test_release_url_outside_the_repo_falls_back(self):
        """回归守卫：这个地址最后会交给系统去打开，不能是接口说什么就打开什么"""
        payload = release_payload(html_url='https://evil.example.com/wallpaper-cleaner')

        result = update.check('0.4.0', opener=opener_for(payload))

        self.assertEqual(result['url'], f'{update.REPO_URL}/releases/tag/v0.5.0')

    def test_lookalike_repo_urls_are_rejected(self):
        self.assertTrue(update.is_repo_url(update.REPO_URL))
        self.assertTrue(update.is_repo_url(f'{update.REPO_URL}/releases'))
        self.assertFalse(update.is_repo_url('https://github.com/halfaradish/wallpaper-cleaner-evil'))
        self.assertFalse(update.is_repo_url('https://github.com/halfaradish'))
        self.assertFalse(update.is_repo_url('steam://open/main'))
        self.assertFalse(update.is_repo_url(''))

    def test_open_state_is_unknown(self):
        state = update.empty_state()

        self.assertEqual(state['status'], 'unknown')
        self.assertEqual(state['latest'], '')
        self.assertEqual(state['error'], '')


if __name__ == '__main__':
    unittest.main()
