"""浏览器管理面板：Python 标准库 http.server 实现，无第三方依赖

安全设计（三层）：
1. CSRF：所有写操作必须带 X-Panel-Token 头（自定义头会触发跨域预检，而本服务不返回任何
   CORS 头，恶意网页因此无法构造请求）；同时校验 Origin 与本次请求的 Host 一致。
2. 路径穿越：删除接口只接受 workshop ID，不接受路径；每个 ID 都会重新做单层目录名校验。
3. 只动已确认的对象：删除的每一项必须来自最近一次扫描的待清理列表，取消订阅的每一项必须
   来自最近一次扫描的已订阅列表，重新订阅的每一项必须来自待清理列表，且都要带同一份
   scan_id；执行前还会再核对一次真实订阅状态，扫描之后被订阅/取消订阅过的壁纸会被跳过。

取消订阅与重新订阅走 Steam 官方 Steamworks 接口（steamapi 模块），作用于账号的订阅
状态——等价于在 Steam 客户端里点一下。本服务不接触账号凭据，也不写 Steam 或
Wallpaper Engine 的任何文件。
"""

import json
import os
import re
import secrets
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from . import core
from . import steamapi
from . import __version__

def _find_static_dir():
    """定位面板静态资源目录

    打包后这些文件被 PyInstaller 解压到 sys._MEIPASS 下，不再位于 __file__ 旁边，
    所以除了常规位置还要在打包目录里兜底找一遍。
    """
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static'),
        os.path.join(core.bundle_dir(), 'wallpaper_cleaner', 'static'),
        os.path.join(core.bundle_dir(), 'static'),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


STATIC_DIR = _find_static_dir()
STATIC_FILES = ('index.html', 'app.js', 'style.css')
CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
}
# 缩略图按扩展名精确给类型：响应带 nosniff，类型给错浏览器就直接不渲染
PREVIEW_CONTENT_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
}

MAX_JOB_LINES = 400
MAX_KEPT_JOBS = 20
MAX_BODY_BYTES = 256 * 1024

# 前端轮询与静态资源的请求量很大，不写进日志，否则会把日志刷满
_quiet_request = re.compile(r'^/(favicon\.ico|static/)|^/api/(job/|state|thumb)')


def _line(level, text):
    return {'level': level, 'text': text, 'time': datetime.now().strftime('%H:%M:%S')}


def _empty_steam_state():
    """Steam 状态初值：面板据此显示灰色的「未检测」"""
    return {
        'status': 'unknown',      # unknown / probing / ok / unavailable
        'reason': '',
        'detail': '',
        'hint': '',
        'subscribed_count': None,
        'dll_path': '',
        'checked_at': '',
    }


class PanelState:
    """面板运行期共享状态：任务表、最近一次扫描结果、访问令牌、Steam 连接状态"""

    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.jobs = {}
        self.job_seq = 0
        self.active_job = None
        self.last_scan = None
        self.autodetect = (None, None)
        # Steam 可用性：unknown → probing → ok / unavailable，由后台探测线程更新
        self.steam = _empty_steam_state()
        # 本会话内已成功取消订阅/重新订阅的 ID。仅存在内存里：Steam 改订阅状态与
        # 本地缓存刷新之间有时间差，这些 ID 用来在面板上标出"等待 Steam 同步"，
        # 同时把刚重新订阅的目录挡在删除白名单之外。重扫后自然失去意义。
        self.session_unsubscribed = set()
        self.session_resubscribed = set()

    def steam_status(self):
        """给面板看的 Steam 状态快照"""
        with self.lock:
            return dict(self.steam)

    def start_job(self, job_type, worker):
        """同一时间只允许一个任务；返回 (job_id, error)"""
        with self.lock:
            if self.active_job:
                return None, '已有任务正在执行，请等待完成'
            self.job_seq += 1
            job_id = f'{job_type}-{self.job_seq}'
            job = {
                'id': job_id,
                'seq': self.job_seq,
                'type': job_type,
                'status': 'running',
                'done': 0,
                'total': 0,
                'message': '正在启动…',
                'lines': [],
                'result': None,
                'error': None,
                'started_at': datetime.now().strftime('%H:%M:%S'),
            }
            self.jobs[job_id] = job
            self.active_job = job_id
            for stale in [jid for jid, j in self.jobs.items() if j['seq'] <= self.job_seq - MAX_KEPT_JOBS]:
                self.jobs.pop(stale, None)

        threading.Thread(target=self._run, args=(job_id, worker), daemon=True).start()
        return job_id, None

    def _run(self, job_id, worker):
        job = self.jobs[job_id]
        try:
            result = worker(job)
            with self.lock:
                job['status'] = 'done'
                job['result'] = result
                job['message'] = '已完成'
        except Exception as e:
            core.logger.exception('面板任务失败 (%s)', job_id)
            with self.lock:
                job['status'] = 'error'
                job['error'] = str(e)
                job['message'] = '执行失败'
                job['lines'].append(_line('error', str(e)))
        finally:
            with self.lock:
                self.active_job = None

    def progress(self, job):
        """给 core 用的进度回调：on_progress(done, total, message, level)"""
        def emit(done, total, message, level='info'):
            with self.lock:
                if total:
                    job['done'] = done
                    job['total'] = total
                job['message'] = message
                job['lines'].append(_line(level, message))
                if len(job['lines']) > MAX_JOB_LINES:
                    del job['lines'][:len(job['lines']) - MAX_JOB_LINES]
        return emit

    def snapshot(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            data = dict(job)
            data['lines'] = list(job['lines'])
            data.pop('seq', None)
            return data


def resolve_paths(state):
    """决定本次使用的路径：优先已保存的配置，其次自动检测；并给出未配置时的原因说明"""
    info = core.describe_config(auto_detect=state.autodetect)

    if info['json_path'] and info['workshop_dir']:
        source = 'config'
        json_path, workshop_dir = info['json_path'], info['workshop_dir']
    elif info['auto_json_path'] and info['auto_workshop_dir']:
        source = 'autodetect'
        json_path, workshop_dir = info['auto_json_path'], info['auto_workshop_dir']
    else:
        source, json_path, workshop_dir = 'none', '', ''

    if source != 'none':
        hint = ''
    elif info['error']:
        hint = '配置文件无法解析，请在「高级」中修正后保存'
    elif not info['exists']:
        hint = '还没设置 Wallpaper Engine 的位置，请点「高级」→「自动检测」'
    else:
        hint = '配置里的位置是空的，请到「高级」里填写'

    return {
        'json_path': json_path,
        'workshop_dir': workshop_dir,
        'steam_dll_path': info['steam_dll_path'],
        'source': source,
        'hint': hint,
        'config_file': info['config_file'],
        'config_name': info['config_name'],
        'config_exists': info['exists'],
        'config_error': info['error'],
        'is_legacy_json': info['is_legacy_json'],
        'auto_json_path': info['auto_json_path'],
        'auto_workshop_dir': info['auto_workshop_dir'],
    }


def start_steam_probe(state):
    """在后台探测一次 Steam 可用性，立即返回

    刻意不走任务槽：探测可能耗时一两秒，而扫描/删除才是主线，不能让它挡住扫描，
    也不该让用户看到"探测中就是一个任务"。探测完就断开连接（steamapi.probe 里做）。
    """
    with state.lock:
        if state.steam.get('status') == 'probing':
            return False
        state.steam = dict(_empty_steam_state(), status='probing', checked_at=datetime.now().strftime('%H:%M:%S'))

    paths = resolve_paths(state)

    def worker():
        result = steamapi.probe(
            paths['json_path'], paths['workshop_dir'], paths['steam_dll_path'] or ''
        )
        core.logger.debug('Steam 状态: %s %s', result.get('status'), result.get('detail') or '')
        with state.lock:
            state.steam = result

    threading.Thread(target=worker, daemon=True).start()
    return True


def _scan_worker(state, job):
    emit = state.progress(job)
    paths = resolve_paths(state)

    if not paths['json_path'] or not paths['workshop_dir']:
        raise core.ConfigError(paths['hint'] or '尚未配置路径')

    source_label = '配置文件' if paths['source'] == 'config' else '自动检测'
    emit(0, 0, f'使用{source_label}中的路径', 'info')
    emit(0, 0, f'订阅缓存: {paths["json_path"]}', 'debug')
    emit(0, 0, f'壁纸目录: {paths["workshop_dir"]}', 'debug')

    context = core.load_subscription_context(paths['json_path'], paths['workshop_dir'])
    subscriptions = context['subscriptions']
    emit(0, 0, f'已订阅壁纸数量: {len(subscriptions)}', 'info')
    for level, message in core.describe_steam_record(context):
        emit(0, 0, message, level)

    result = core.scan(
        paths['workshop_dir'],
        subscriptions,
        json_path=paths['json_path'],
        config_file=paths['config_file'] or '',
        on_progress=emit,
        extra_subscribed=context['protected'],
        extra_sizes=context['installed_sizes'],
    )

    with state.lock:
        state.last_scan = result

    unknown = len(result['unknown'])
    emit(
        result['total_folders'], result['total_folders'],
        f"扫描完成：待清理 {len(result['orphans'])} 个"
        + (f"，其中 {unknown} 个无法确定" if unknown else ''),
        'info',
    )
    core.logger.info(
        '面板扫描完成: 文件夹 %d，待清理 %d，未知 %d',
        result['total_folders'], len(result['orphans']), len(result['unknown']),
    )
    return result


def _delete_worker(state, job, scan, items, recycle, just_resubscribed=()):
    emit = state.progress(job)

    # 本会话里刚重新订阅的项：Steam 那边订阅状态已经改回来了，但本地 ACF/缓存还没刷新，
    # 下面那套"重读订阅记录"的复核看不出它们已经回到订阅列表，只能由我们自己记住。
    recent = {item['wid'] for item in items} & set(just_resubscribed)
    if recent:
        for item in [i for i in items if i['wid'] in recent]:
            emit(0, len(items), f"跳过 {core.item_label(item)}：刚重新订阅，等待 Steam 同步", 'warn')
        items = [item for item in items if item['wid'] not in recent]

    total = len(items)
    emit(0, total, '正在复核订阅列表…', 'info')

    # 扫描之后可能有人重新订阅了某些壁纸，删除前必须重新核对，避免删掉刚订阅的内容。
    # 这里用与扫描时同一套判定（含「谁更新就信谁」的 Steam 记录仲裁），不另写一份。
    try:
        context = core.load_subscription_context(scan['json_path'], scan['workshop_dir'])
    except core.SubscriptionError as e:
        raise core.ScanError(f'删除前复核订阅列表失败，已中止：{e}')
    protected = context['protected']

    revived = [i for i in items if i['wid'] in protected]
    rest = [i for i in items if i['wid'] not in protected]
    for item in revived:
        emit(0, total, f"跳过 {core.item_label(item)}：该壁纸仍处于订阅状态", 'warn')

    # 目录刚被改动过说明可能还在下载，先放过这一轮；
    # 但内容记录里已写明装完时（Steam 记下了 manifest），就不是"正在下载"，不必再等
    complete = context['complete']
    grace_minutes = core.FRESH_DOWNLOAD_GRACE_SECONDS // 60
    fresh = [i for i in rest
             if i['wid'] not in complete and core.is_freshly_downloaded(i.get('path') or '')]
    fresh_wids = {i['wid'] for i in fresh}
    targets = [i for i in rest if i['wid'] not in fresh_wids]
    for item in fresh:
        emit(0, total, f"跳过 {core.item_label(item)}：目录内容在 {grace_minutes} 分钟内被改动过，"
                       '可能是正在下载的壁纸', 'warn')

    if not targets:
        emit(0, total, '选中项都仍处于订阅或刚下载状态，无需删除', 'info')
        outcome = {'deleted': [], 'failed': [], 'freed_bytes': 0}
    else:
        mode = '回收站' if recycle else '永久删除'
        emit(0, len(targets), f'开始删除（{mode}）…', 'info')
        outcome = core.delete_folders(
            targets, scan['workshop_dir'], to_recycle_bin=recycle, on_progress=emit
        )

    # skipped 仍是全部跳过项；另外按原因分开报，面板才能把提示说清楚
    outcome['skipped'] = [i['wid'] for i in revived + fresh] + sorted(recent)
    outcome['skipped_subscribed'] = [i['wid'] for i in revived]
    outcome['skipped_fresh'] = [i['wid'] for i in fresh]
    outcome['skipped_resubscribed'] = sorted(recent)
    with state.lock:
        # 磁盘内容已变化，旧扫描结果作废，避免下一次删除基于过期列表
        state.last_scan = None

    core.logger.info(
        '面板删除完成: 成功 %d，失败 %d，跳过 %d，释放 %s',
        len(outcome['deleted']), len(outcome['failed']), len(outcome['skipped']),
        core.format_size(outcome['freed_bytes']),
    )
    return outcome


def _steam_paths(state):
    """当前生效的路径配置，供 steamapi 定位 dll"""
    paths = resolve_paths(state)
    return paths['json_path'], paths['workshop_dir'], paths['steam_dll_path'] or ''


def _steam_failure(reason, detail):
    """把原因码与原始说明合成一句给用户看的话"""
    hint = steamapi.hint_for(reason)
    detail = (detail or '').strip()
    if detail and detail != hint and hint not in detail:
        return f'{hint}（{detail}）'
    return hint


def _connect_steam(state):
    """按当前路径配置连接 Steam，失败抛 ScanError（消息直接给用户看）"""
    json_path, workshop_dir, dll_path = _steam_paths(state)
    ok, reason, detail = steamapi.connect(json_path, workshop_dir, dll_path)
    if not ok:
        raise core.ScanError(_steam_failure(reason, detail))


def _unsubscribe_worker(state, job, scan, items, mode, recycle):
    """批量取消订阅；mode 为 with_delete 时顺带清理本地目录

    删除只针对"已确认生效"的项：UnsubscribeItem 返回 true 只说明 Steam 受理了
    请求，订阅状态还没落下来就去删目录的话，Steam 可能因为仍在订阅而重新下载。
    """
    emit = state.progress(job)
    total = len(items)
    emit(0, total, '正在连接 Steam…', 'info')

    try:
        _connect_steam(state)
        emit(0, total, '正在读取订阅列表…', 'info')
        subscribed = steamapi.get_subscribed()
        targets = [item for item in items if item['wid'] in subscribed]
        skipped = [item for item in items if item['wid'] not in subscribed]
        for item in skipped:
            emit(0, total, f'跳过 {core.item_label(item)}：Steam 里已经不在订阅列表', 'warn')

        accepted, failed = [], []
        for index, item in enumerate(targets, 1):
            wid = item['wid']
            try:
                if not steamapi.unsubscribe(wid):
                    failed.append({'wid': wid, 'error': 'Steam 没有受理这次请求'})
                    emit(index, total, f'取消订阅失败 {core.item_label(item)}', 'error')
                    continue
            except Exception as e:
                failed.append({'wid': wid, 'error': str(e)})
                emit(index, total, f'取消订阅失败 {core.item_label(item)}：{e}', 'error')
                continue
            accepted.append(wid)
            emit(index, total, f'已提交取消订阅：{core.item_label(item)}')

        emit(0, total, '正在确认订阅状态…', 'info')
        confirmed = steamapi.confirm_unsubscribed(accepted) if accepted else set()
        unconfirmed = [wid for wid in accepted if wid not in confirmed]
        for wid in unconfirmed:
            emit(0, total, f'已提交但未确认生效：{wid}（Steam 处理较慢，稍后重新扫描可核实）', 'warn')

        outcome = {
            'mode': mode,
            'unsubscribed': sorted(confirmed),
            'unconfirmed': unconfirmed,
            'failed': failed,
            'skipped': [item['wid'] for item in skipped],
            'deleted': [],
            'delete_failed': [],
            'freed_bytes': 0,
            'already_gone': 0,
        }

        if mode == 'with_delete' and confirmed:
            emit(0, len(confirmed), '正在清理本地文件…', 'info')
            chosen = [item for item in items if item['wid'] in confirmed]
            # Steam 也在删同一个目录：目录已经不在了就是目的达成，不算失败
            remaining = [item for item in chosen if os.path.isdir(item.get('path') or '')]
            outcome['already_gone'] = len(chosen) - len(remaining)
            if remaining:
                deleted = core.delete_folders(
                    remaining, scan['workshop_dir'], to_recycle_bin=recycle, on_progress=emit
                )
                outcome['deleted'] = deleted['deleted']
                outcome['delete_failed'] = deleted['failed']
                outcome['freed_bytes'] = deleted['freed_bytes']

        with state.lock:
            state.session_unsubscribed |= set(confirmed)
            state.session_resubscribed -= set(confirmed)

        core.logger.info(
            '面板取消订阅完成: 成功 %d，未确认 %d，失败 %d，跳过 %d，清理目录 %d',
            len(outcome['unsubscribed']), len(unconfirmed), len(failed), len(skipped),
            len(outcome['deleted']),
        )
        return outcome
    finally:
        steamapi.shutdown()


def _resubscribe_worker(state, job, scan, items):
    """批量重新订阅（对象来自待清理列表里的残留目录）"""
    emit = state.progress(job)
    total = len(items)
    emit(0, total, '正在连接 Steam…', 'info')

    try:
        _connect_steam(state)
        emit(0, total, '正在读取订阅列表…', 'info')
        subscribed = steamapi.get_subscribed()
        targets = [item for item in items if item['wid'] not in subscribed]
        skipped = [item for item in items if item['wid'] in subscribed]
        for item in skipped:
            emit(0, total, f'跳过 {core.item_label(item)}：这张壁纸已经在订阅列表中', 'warn')

        accepted, failed = [], []
        for index, item in enumerate(targets, 1):
            wid = item['wid']
            try:
                if not steamapi.subscribe(wid):
                    failed.append({'wid': wid, 'error': 'Steam 没有受理这次请求'})
                    emit(index, total, f'重新订阅失败 {core.item_label(item)}', 'error')
                    continue
            except Exception as e:
                failed.append({'wid': wid, 'error': str(e)})
                emit(index, total, f'重新订阅失败 {core.item_label(item)}：{e}', 'error')
                continue
            accepted.append(wid)
            emit(index, total, f'已提交重新订阅：{core.item_label(item)}')

        emit(0, total, '正在确认订阅状态…', 'info')
        confirmed = steamapi.confirm_subscribed(accepted) if accepted else set()
        unconfirmed = [wid for wid in accepted if wid not in confirmed]
        for wid in unconfirmed:
            emit(0, total, f'已提交但未确认生效：{wid}（Steam 处理较慢，稍后重新扫描可核实）', 'warn')

        with state.lock:
            state.session_resubscribed |= set(confirmed)
            state.session_unsubscribed -= set(confirmed)

        core.logger.info(
            '面板重新订阅完成: 成功 %d，未确认 %d，失败 %d，跳过 %d',
            len(confirmed), len(unconfirmed), len(failed), len(skipped),
        )
        return {
            'resubscribed': sorted(confirmed),
            'unconfirmed': unconfirmed,
            'failed': failed,
            'skipped': [item['wid'] for item in skipped],
        }
    finally:
        steamapi.shutdown()


class PanelHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'wallpaper-cleaner'

    # ---------- 基础响应 ----------

    def log_message(self, fmt, *args):
        if _quiet_request.match(self.path or ''):
            return
        core.logger.debug('[面板] %s %s', self.address_string(), fmt % args)

    def _send_bytes(self, data, content_type, status=200, cache='no-store', etag=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        if etag:
            self.send_header('ETag', etag)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _send_not_modified(self, cache, etag):
        """304：浏览器手里那份还能用，不回正文"""
        self.send_response(304)
        self.send_header('Cache-Control', cache)
        self.send_header('ETag', etag)
        self.end_headers()

    def _send_json(self, payload, status=200):
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            'application/json; charset=utf-8',
            status,
        )

    def _read_json_body(self):
        """返回 (body, error)；body 一定是个 dict"""
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            return None, 'Content-Length 无效'
        if length < 0 or length > MAX_BODY_BYTES:
            return None, '请求体过大'
        if length == 0:
            return {}, None
        try:
            body = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return None, '请求体不是合法 JSON'
        if not isinstance(body, dict):
            return None, '请求体必须是 JSON 对象'
        return body, None

    # ---------- 安全校验 ----------

    def _check_token(self):
        supplied = self.headers.get('X-Panel-Token') or ''
        expected = self.server.state.token
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    def _check_origin(self):
        """Origin 存在时必须与本次请求的 Host 完全一致（同源），否则视为跨站请求"""
        origin = self.headers.get('Origin')
        if not origin:
            return True
        host = self.headers.get('Host') or ''
        parsed = urlparse(origin)
        return bool(parsed.netloc) and parsed.netloc.lower() == host.lower()

    def _reject_write(self):
        """写操作的前置校验，通过返回 None"""
        if not self._check_origin():
            self._send_json({'error': '请求来源校验失败，请通过面板页面操作'}, 403)
            return 'origin'
        if not self._check_token():
            self._send_json({'error': '访问令牌无效或已过期，请刷新页面后重试'}, 403)
            return 'token'
        return None

    # ---------- 路由 ----------

    def do_GET(self):
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:
            core.logger.exception('面板 GET 处理失败: %s', self.path)
            self._safe_error(e)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        try:
            self._route_post()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:
            core.logger.exception('面板 POST 处理失败: %s', self.path)
            self._safe_error(e)

    def do_OPTIONS(self):
        # 故意不返回任何 CORS 头：跨域预检必然失败，恶意网页无法发起写操作
        self._send_json({'error': '不支持跨域请求'}, 403)

    def _safe_error(self, exc):
        try:
            self._send_json({'error': f'服务器内部错误: {exc}'}, 500)
        except Exception:
            self.close_connection = True

    def _route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/':
            return self._serve_index()
        if path == '/favicon.ico':
            return self._send_bytes(b'', 'image/x-icon', 204)
        if path.startswith('/static/'):
            return self._serve_static(path[len('/static/'):])
        if path == '/api/state':
            return self._send_json(self._state_payload())
        if path == '/api/thumb':
            return self._serve_thumb(parse_qs(parsed.query))
        if path == '/api/logs':
            query = parse_qs(parsed.query)
            try:
                lines = int((query.get('lines') or ['200'])[0])
            except ValueError:
                lines = 200
            return self._send_json(core.read_log_tail(max(10, min(lines, 2000))))
        if path.startswith('/api/job/'):
            snapshot = self.server.state.snapshot(path[len('/api/job/'):])
            if snapshot is None:
                return self._send_json({'error': '任务不存在'}, 404)
            return self._send_json(snapshot)
        return self._send_json({'error': '未知接口'}, 404)

    def _route_post(self):
        if self._reject_write():
            return

        parsed = urlparse(self.path)
        body, error = self._read_json_body()
        if error:
            return self._send_json({'error': error}, 400)

        if parsed.path == '/api/scan':
            return self._post_scan()
        if parsed.path == '/api/delete':
            return self._post_delete(body)
        if parsed.path == '/api/reveal':
            return self._post_reveal(body)
        if parsed.path == '/api/config':
            return self._post_config(body)
        if parsed.path == '/api/autodetect':
            return self._post_autodetect()
        if parsed.path == '/api/steam/probe':
            return self._post_steam_probe()
        if parsed.path == '/api/steam/launch':
            return self._post_steam_launch()
        if parsed.path == '/api/steam/page':
            return self._post_steam_page(body)
        if parsed.path == '/api/unsubscribe':
            return self._post_unsubscribe(body)
        if parsed.path == '/api/resubscribe':
            return self._post_resubscribe(body)
        return self._send_json({'error': '未知接口'}, 404)

    # ---------- 具体接口 ----------

    def _state_payload(self):
        state = self.server.state
        with state.lock:
            active_job = state.active_job
            session_unsubscribed = sorted(state.session_unsubscribed)
            session_resubscribed = sorted(state.session_resubscribed)
        return {
            'paths': resolve_paths(state),
            'scan': state.last_scan,
            'active_job': active_job,
            'log_file': core.latest_log_file(),
            'recycle_supported': sys.platform == 'win32',
            'steam': state.steam_status(),
            'session_unsubscribed': session_unsubscribed,
            'session_resubscribed': session_resubscribed,
            'version': __version__,
        }

    def _post_scan(self):
        state = self.server.state
        job_id, error = state.start_job('scan', lambda job: _scan_worker(state, job))
        if not job_id:
            return self._send_json({'error': error}, 409)
        return self._send_json({'job_id': job_id})

    def _post_delete(self, body):
        state = self.server.state
        with state.lock:
            scan = state.last_scan
        if scan is None:
            return self._send_json({'error': '请先扫描，再执行删除'}, 409)

        if body.get('scan_id') != scan['scanned_at']:
            return self._send_json({'error': '扫描结果已过期，请重新扫描后再删除'}, 409)

        wids = body.get('wids')
        if not isinstance(wids, list) or not wids:
            return self._send_json({'error': '未选择任何目录'}, 400)
        if len(wids) > 5000:
            return self._send_json({'error': '单次选择的目录过多'}, 400)

        # 只允许删除最近一次扫描已确认的待清理项，前端被篡改也无法删除订阅中的内容
        allowed = {item['wid']: item for item in scan['orphans'] + scan['unknown']}
        items, rejected = [], []
        for wid in wids:
            key = wid if isinstance(wid, str) else str(wid)
            if key in allowed:
                items.append(allowed[key])
            else:
                rejected.append(key)
        if rejected:
            return self._send_json({
                'error': '以下目录不在最近一次扫描的待清理列表中，已拒绝整批删除',
                'rejected': rejected[:50],
            }, 400)

        recycle = bool(body.get('recycle', True))
        # 本会话刚重新订阅的 ID 一并交给 worker：本地订阅记录还没刷新，
        # 只有我们自己知道它们已经回到订阅列表，不能当残留删掉
        with state.lock:
            just_resubscribed = set(state.session_resubscribed)
        job_id, error = state.start_job(
            'delete',
            lambda job: _delete_worker(state, job, scan, items, recycle, just_resubscribed),
        )
        if not job_id:
            return self._send_json({'error': error}, 409)
        core.logger.info(
            '面板请求删除 %d 个目录（%s）', len(items),
            '回收站' if recycle else '永久删除',
        )
        return self._send_json({'job_id': job_id})

    def _post_reveal(self, body):
        """在系统文件管理器里打开某个壁纸的目录

        和缩略图一样只认最近一次扫描结果里的目录：ID 来自请求，路径来自扫描。
        只把目录交给系统去打开，不读、不改目录里的任何东西。
        """
        wid = body.get('wid')
        if not core.is_safe_wid(wid):
            return self._send_json({'error': '非法的 workshop ID'}, 404)

        item = self._scan_item(wid)
        if item is None:
            return self._send_json({'error': '该壁纸不在最近一次扫描结果里'}, 404)

        path = item.get('path') or ''
        if not path or not os.path.isdir(path):
            # 扫描之后被删掉/被移走
            return self._send_json({'error': '这个文件夹已经不在了，请重新扫描'}, 404)

        try:
            core.open_folder(path)
        except OSError as e:
            return self._send_json({'error': f'打开文件夹失败：{e}'}, 500)

        core.logger.info('面板打开目录: %s', path)
        return self._send_json({'ok': True})

    def _post_config(self, body):
        state = self.server.state
        json_path = body.get('json_path')
        workshop_dir = body.get('workshop_dir')
        if not isinstance(json_path, str) or not isinstance(workshop_dir, str):
            return self._send_json({'error': 'json_path 与 workshop_dir 必须是字符串'}, 400)

        json_path, workshop_dir = json_path.strip(), workshop_dir.strip()
        if not json_path or not workshop_dir:
            return self._send_json({'error': 'json_path 与 workshop_dir 都不能为空'}, 400)

        try:
            target = core.write_config_values({
                'json_path': json_path,
                'workshop_dir': workshop_dir,
            })
        except core.ConfigError as e:
            return self._send_json({'error': str(e)}, 400)

        warnings = []
        resolved_json = core.resolve_path(json_path)
        resolved_workshop = core.resolve_path(workshop_dir)
        if not os.path.isfile(resolved_json):
            warnings.append(f'该文件当前不存在：{resolved_json}')
        if not os.path.isdir(resolved_workshop):
            warnings.append(f'该目录当前不存在：{resolved_workshop}')

        with state.lock:
            state.last_scan = None

        core.logger.info('面板已更新配置: %s', target)
        return self._send_json({'ok': True, 'config_file': target, 'warnings': warnings})

    def _post_autodetect(self):
        try:
            auto_json, auto_workshop = core.auto_detect_paths()
        except Exception as e:
            return self._send_json({'found': False, 'error': f'自动检测失败: {e}'})

        self.server.state.autodetect = (auto_json, auto_workshop)
        if not auto_json or not auto_workshop:
            return self._send_json({
                'found': False,
                'error': '未能自动检测到 Wallpaper Engine 路径，请手动填写',
            })
        return self._send_json({
            'found': True,
            'json_path': auto_json,
            'workshop_dir': auto_workshop,
        })

    # ---------- Steam：状态与跳转 ----------

    def _post_steam_probe(self):
        """探测 Steam 是否可用。后台执行，接口立刻返回，结果进 /api/state"""
        state = self.server.state
        if not start_steam_probe(state):
            return self._send_json(
                {'ok': False, 'error': '正在检测中，请稍候', 'steam': state.steam_status()}, 409
            )
        return self._send_json({'ok': True, 'steam': state.steam_status()})

    def _post_steam_launch(self):
        """唤起 Steam 客户端（没在运行时由系统把它拉起来）"""
        try:
            core.open_url('steam://open/main')
        except OSError as e:
            return self._send_json({'error': f'启动 Steam 失败：{e}'}, 500)
        core.logger.info('面板请求启动 Steam')
        return self._send_json({'ok': True})

    def _post_steam_page(self, body):
        """在 Steam 客户端里打开某张壁纸的创意工坊页面

        连不上 Steam 接口时的保底路径：把用户送到 Steam 里手动点一下。
        """
        wid = body.get('wid')
        if not core.is_safe_wid(wid) or not str(wid).isdigit():
            return self._send_json({'error': '非法的 workshop ID'}, 404)
        if self._scan_item(wid) is None:
            return self._send_json({'error': '该壁纸不在最近一次扫描结果里'}, 404)

        try:
            core.open_url(f'steam://url/CommunityFilePage/{wid}')
        except OSError as e:
            return self._send_json({'error': f'打开 Steam 页面失败：{e}'}, 500)

        core.logger.info('面板打开 Steam 页面: %s', wid)
        return self._send_json({'ok': True})

    # ---------- 取消订阅 / 重新订阅 ----------

    def _post_unsubscribe(self, body):
        state = self.server.state
        with state.lock:
            scan = state.last_scan
        if scan is None:
            return self._send_json({'error': '请先扫描，再取消订阅'}, 409)
        if body.get('scan_id') != scan['scanned_at']:
            return self._send_json({'error': '扫描结果已过期，请重新扫描后再取消订阅'}, 409)

        mode = body.get('mode') or 'only'
        if mode not in ('only', 'with_delete'):
            return self._send_json({'error': '未知的取消订阅方式'}, 400)

        wids = body.get('wids')
        if not isinstance(wids, list) or not wids:
            return self._send_json({'error': '未选择任何壁纸'}, 400)
        if len(wids) > 5000:
            return self._send_json({'error': '单次选择的壁纸过多'}, 400)

        # 只允许操作最近一次扫描确认仍在订阅的壁纸（执行前还会再跟 Steam 核对一次）
        allowed = {item['wid']: item for item in scan['subscribed']}
        items, rejected = [], []
        for wid in wids:
            key = wid if isinstance(wid, str) else str(wid)
            if key in allowed:
                items.append(allowed[key])
            else:
                rejected.append(key)
        if rejected:
            return self._send_json({
                'error': '以下壁纸不在最近一次扫描的已订阅列表中，已拒绝整批操作',
                'rejected': rejected[:50],
            }, 400)

        recycle = bool(body.get('recycle', True))
        job_id, error = state.start_job(
            'unsubscribe',
            lambda job: _unsubscribe_worker(state, job, scan, items, mode, recycle),
        )
        if not job_id:
            return self._send_json({'error': error}, 409)
        core.logger.info(
            '面板请求取消订阅 %d 张壁纸（%s）', len(items),
            '同时删除本地文件' if mode == 'with_delete' else '仅取消订阅',
        )
        return self._send_json({'job_id': job_id})

    def _post_resubscribe(self, body):
        state = self.server.state
        with state.lock:
            scan = state.last_scan
        if scan is None:
            return self._send_json({'error': '请先扫描，再重新订阅'}, 409)
        if body.get('scan_id') != scan['scanned_at']:
            return self._send_json({'error': '扫描结果已过期，请重新扫描后再重新订阅'}, 409)

        wids = body.get('wids')
        if not isinstance(wids, list) or not wids:
            return self._send_json({'error': '未选择任何壁纸'}, 400)
        if len(wids) > 5000:
            return self._send_json({'error': '单次选择的壁纸过多'}, 400)

        # 重新订阅的对象只能是磁盘上的残留目录（纯数字 ID），不接受任意 ID
        allowed = {item['wid']: item for item in scan['orphans']}
        items, rejected = [], []
        for wid in wids:
            key = wid if isinstance(wid, str) else str(wid)
            if key in allowed:
                items.append(allowed[key])
            else:
                rejected.append(key)
        if rejected:
            return self._send_json({
                'error': '以下目录不在最近一次扫描的待清理列表中，已拒绝整批操作',
                'rejected': rejected[:50],
            }, 400)

        job_id, error = state.start_job(
            'resubscribe', lambda job: _resubscribe_worker(state, job, scan, items)
        )
        if not job_id:
            return self._send_json({'error': error}, 409)
        core.logger.info('面板请求重新订阅 %d 张壁纸', len(items))
        return self._send_json({'job_id': job_id})

    # ---------- 按 ID 查最近一次扫描结果（缩略图与打开目录共用） ----------

    def _scan_item(self, wid):
        """在最近一次扫描结果里按 ID 找条目，找不到返回 None

        路径与预览图文件名都取自我们自己的扫描结果，绝不接受请求里带来的路径：
        接口只有「ID 形状校验 + 查扫描结果」两道输入检查，比"先拼路径再校验"更难写错。
        """
        state = self.server.state
        with state.lock:
            scan = state.last_scan
        if not scan:
            return None
        for key in ('orphans', 'unknown', 'subscribed'):
            for item in scan.get(key) or ():
                if item.get('wid') == wid:
                    return item
        return None

    # ---------- 缩略图 ----------

    def _serve_thumb(self, query):
        """发壁纸目录里的预览图字节，解码与缩放交给浏览器"""
        wid = (query.get('wid') or [''])[0]
        if not core.is_safe_wid(wid):
            return self._send_json({'error': '非法的 workshop ID'}, 404)

        item = self._scan_item(wid)
        if item is None:
            # 未扫描、已删除，或改过配置导致旧结果作废
            return self._send_json({'error': '该壁纸不在最近一次扫描结果里'}, 404)

        path = core.resolve_preview_path(item.get('path') or '', item.get('preview') or '')
        if not path:
            return self._send_json({'error': '这个文件夹里没有可用的预览图'}, 404)
        try:
            with open(path, 'rb') as f:
                data = f.read()
            info = os.stat(path)
        except OSError:
            return self._send_json({'error': '预览图读不出来'}, 404)

        # 列表每次重绘都会重建 <img>，没有缓存的话每次都要把这些图重拉一遍
        cache = 'private, max-age=600'
        etag = f'"{info.st_size:x}-{info.st_mtime_ns:x}"'
        if self.headers.get('If-None-Match') == etag:
            return self._send_not_modified(cache, etag)
        content_type = PREVIEW_CONTENT_TYPES.get(
            os.path.splitext(path)[1].lower(), 'application/octet-stream'
        )
        self._send_bytes(data, content_type, cache=cache, etag=etag)

    # ---------- 静态文件 ----------

    def _serve_index(self):
        try:
            with open(os.path.join(STATIC_DIR, 'index.html'), 'r', encoding='utf-8') as f:
                html = f.read()
        except OSError as e:
            return self._send_json({'error': f'面板页面缺失: {e}'}, 500)
        # 占位符与 JS 变量名必须不同，否则会把变量名一起替换掉
        html = html.replace('__PANEL_TOKEN_VALUE__', self.server.state.token)
        html = html.replace('__PANEL_VERSION_VALUE__', __version__)
        self._send_bytes(html.encode('utf-8'), CONTENT_TYPES['.html'])

    def _serve_static(self, name):
        # 白名单匹配，天然挡住目录穿越
        if name not in STATIC_FILES or name == 'index.html':
            return self._send_json({'error': '文件不存在'}, 404)
        try:
            with open(os.path.join(STATIC_DIR, name), 'rb') as f:
                data = f.read()
        except OSError:
            return self._send_json({'error': '文件不存在'}, 404)
        self._send_bytes(data, CONTENT_TYPES.get(os.path.splitext(name)[1], 'application/octet-stream'))


class PanelServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # Windows 下 SO_REUSEADDR 会让两个进程绑到同一端口上，必须关掉以免静默启动第二个面板
    allow_reuse_address = False

    def __init__(self, address, handler, state):
        self.state = state
        HTTPServer.__init__(self, address, handler)


def _bind(host, port, state, attempts=20):
    """绑定端口，被占用时向后递增；全部失败抛 OSError

    port 为 0 表示交给系统分配一个空闲端口（桌面窗口模式用，彻底避免端口冲突）。
    """
    if port == 0:
        return PanelServer((host, 0), PanelHandler, state)

    last_error = None
    for offset in range(attempts):
        candidate = port + offset
        try:
            server = PanelServer((host, candidate), PanelHandler, state)
        except OSError as e:
            last_error = e
            continue
        if offset:
            core.logger.warning('端口 %d 已被占用，改用 %d', port, candidate)
        return server
    raise OSError(f'端口 {port}-{port + attempts - 1} 都不可用: {last_error}')


def server_url(server):
    """服务器的访问地址；绑定在通配地址时给出本机可用的 127.0.0.1"""
    host, port = server.server_address[0], server.server_address[1]
    display_host = '127.0.0.1' if host in ('0.0.0.0', '::', '') else host
    return f'http://{display_host}:{port}/'


def create_server(host='127.0.0.1', port=8787, state=None):
    """创建并绑定面板服务器（尚未开始监听），返回 (server, state)

    浏览器模式与桌面窗口模式共用这一份，避免两处实现行为漂移。
    """
    core.setup_logger()
    if state is None:
        state = PanelState()
        try:
            state.autodetect = core.auto_detect_paths()
        except Exception:
            state.autodetect = (None, None)
    return _bind(host, port, state), state


def run(host='127.0.0.1', port=8787, open_browser=True):
    logger = core.setup_logger()

    try:
        server, _state = create_server(host, port)
    except OSError as e:
        logger.error(str(e))
        return 1

    actual_host = server.server_address[0]
    url = server_url(server)

    config_file, is_legacy = core.find_config_file()
    logger.info('=' * 60)
    logger.info('  Wallpaper Cleaner 管理面板已启动')
    logger.info(f'  访问地址: {url}')
    logger.info(f"  配置文件: {config_file or '（尚未创建，可在面板「设置」中生成）'}")
    if is_legacy:
        logger.warning('当前使用旧版 config.json，面板保存时会保持 JSON 格式')
    logger.info('  按 Ctrl+C 停止')
    logger.info('=' * 60)

    if actual_host in ('0.0.0.0', '::'):
        logger.warning('面板监听在所有网卡上，局域网内其他设备也能访问，请谨慎使用')

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('正在停止面板…')
    finally:
        server.server_close()
    return 0
