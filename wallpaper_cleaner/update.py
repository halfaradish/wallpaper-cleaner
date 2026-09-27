"""检查 GitHub 上的最新 Release —— 全项目唯一一处主动发起的出站网络请求

面板「关于」里的「检查更新」用它：点一次查一次，不驻留、不定时、启动时不查。

两条硬约束：

1. 这是给打包后的 exe 用的，而 exe 自己没有控制台（打包配置里 console=False）。
   所以请求必须在本进程里用 urllib 发出去，**不拉任何子进程**——拉起 curl / PowerShell
   这类控制台程序时，Windows 会给它新建一个终端窗口，每次检查都在屏幕上闪一下
   （steamapi 里查 tasklist 踩过同一个坑，那边靠 CREATE_NO_WINDOW 解决，这边从根上不需要）。
2. 只读 GitHub 的公开接口，不发送本机任何信息，不写任何文件。任何失败都变成结果里的
   failed 状态，绝不抛给调用方——面板不能因为一次联网失败就崩掉。
"""

import json
import re
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import quote

from . import core

REPO = 'halfaradish/wallpaper-cleaner'
REPO_URL = f'https://github.com/{REPO}'
# 还没查出具体是哪个 Release 时，「前往 GitHub」的落点：这个地址会跳到最新那个
LATEST_RELEASE_URL = f'{REPO_URL}/releases/latest'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'

# 不带 User-Agent 会被 GitHub 直接 403，浏览器和 curl 能用是因为它们都自带一个
USER_AGENT = 'wallpaper-cleaner'
# 界面上是要等的（按钮显示"正在检查…"），宁可十秒后说"连不上"，也不要一直转圈
TIMEOUT = 10
# 说明正文是 Markdown，弹窗里按纯文本显示，太长会把按钮挤出屏幕，截断即可
MAX_NOTES = 4000

_VERSION_RE = re.compile(r'^[vV]?(\d+(?:\.\d+)*)')


def empty_state():
    """未检查时的初值，面板据此显示「未检查」"""
    return {
        'status': 'unknown',   # unknown / checking / latest / outdated / failed
        'current': '',
        'latest': '',
        'name': '',
        'url': '',
        'published_at': '',
        'notes': '',
        'error': '',
        'checked_at': '',
    }


def _version_tuple(text):
    """把版本号里的数字段取出来：'v1.2.3' / '1.2' / '1.2.3-beta.1' 都认，认不出返回 None

    只认开头那一段（可带一个 v 前缀），这样 'nightly-2026' 这种标签不会被当成版本号——
    它中间确实有数字，但那不是版本。后缀（-beta、+build）一律忽略：预发布版本的先后
    由发布者用标签表达，这里不替它判断。
    """
    match = _VERSION_RE.match(str(text or '').strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split('.'))


def _clean_version(text):
    """显示用的版本号：去掉前面的 v（'v0.5.0' 与面板上的 '0.4.0' 保持同一写法）"""
    value = str(text or '').strip()
    if len(value) > 1 and value[0] in 'vV' and value[1].isdigit():
        return value[1:]
    return value


def is_newer(latest, current):
    """latest 是否比 current 新；任一边认不出就返回 False（宁可不提示，也不误报）"""
    a, b = _version_tuple(latest), _version_tuple(current)
    if a is None or b is None:
        return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def is_repo_url(url):
    """这个地址是不是本仓库自己的页面：打开外部链接前的最后一道校验"""
    text = str(url or '').strip()
    return text == REPO_URL or text.startswith(REPO_URL + '/')


def _release_url(payload, tag):
    """Release 页地址：只信本仓库的链接，其余一律回落到规范地址

    html_url 来自网络，而它最终会交给系统去打开；万一接口返回的内容不对，也不能让
    面板去打开一个陌生地址。
    """
    url = str(payload.get('html_url') or '').strip()
    if is_repo_url(url):
        return url
    return f'{REPO_URL}/releases/tag/{quote(str(tag or "").strip(), safe="")}'


def _notes_from(payload):
    body = str(payload.get('body') or '').strip()
    if len(body) <= MAX_NOTES:
        return body
    return body[:MAX_NOTES].rstrip() + '\n\n…（更新说明太长，已截断，完整内容请在 GitHub 上查看）'


def _fetch(opener):
    """请求 GitHub 的最新 Release 接口并解析；异常由 check 归类成用户看得懂的话"""
    request = urllib.request.Request(API_LATEST, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/vnd.github+json',
    })
    with opener(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if not isinstance(payload, dict):
        raise ValueError('接口返回的内容不是预期格式')
    return payload


def check(current_version, opener=None):
    """查一次最新 Release，返回状态字典；无论出什么事都不抛异常

    opener 是留给测试的注入口，默认走 urllib 请求真实接口。
    """
    opener = opener or urllib.request.urlopen
    result = dict(empty_state(), current=_clean_version(current_version),
                  checked_at=datetime.now().strftime('%H:%M:%S'))

    def fail(message):
        result.update({'status': 'failed', 'error': message})
        core.logger.debug('检查更新失败: %s', message)
        return result

    try:
        payload = _fetch(opener)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return fail('仓库里还没有发布过 Release')
        if e.code in (403, 429):
            return fail('GitHub 接口访问受限（短时间内查得太频繁），请过一会儿再试')
        return fail(f'GitHub 返回 HTTP {e.code}，暂时查不了')
    except urllib.error.URLError as e:
        # 证书校验失败要单独说：用户这边的网络其实是通的（浏览器能开 GitHub），
        # 卡住的是本机代理/安全软件换上的证书，用一句"检查网络"会把人指错方向
        if isinstance(getattr(e, 'reason', None), ssl.SSLError):
            return fail('连不上 GitHub：HTTPS 证书校验没通过，多半是本机代理或安全软件在替换证书，'
                        '可关掉它们，或让 api.github.com 直连后重试')
        return fail('连不上 GitHub，请检查网络后重试')
    except (socket.timeout, TimeoutError):
        return fail('连不上 GitHub，请检查网络后重试')
    except Exception as e:  # 接口改版、返回内容不是 JSON 等等，都不该让面板崩掉
        return fail(f'检查更新失败：{e}')

    tag = str(payload.get('tag_name') or '').strip()
    if not tag:
        return fail('GitHub 没有给出最新版本号')
    if _version_tuple(tag) is None:
        return fail(f'认不出最新版本号「{tag}」')

    latest = _clean_version(tag)
    result.update({
        'status': 'outdated' if is_newer(latest, result['current']) else 'latest',
        'latest': latest,
        'name': str(payload.get('name') or '').strip(),
        'url': _release_url(payload, tag),
        'published_at': str(payload.get('published_at') or '')[:10],
        'notes': _notes_from(payload),
    })
    core.logger.debug('检查更新：当前 %s，最新 %s（%s）',
                      result['current'], latest, result['status'])
    return result
