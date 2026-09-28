// 设置视图：位置设置 / 运行日志 / 关于
//
// 以前这三块是一个从右侧滑出的抽屉，盖在表格上面。改成导航栏之后抽屉是多余的：
// 一个常驻的导航栏已经能表达"这是另一个地方"，再叠一层浮层只会让人分不清
// 自己现在到底在哪一层。内容一字未改，只是换了个容器。
import { $, setHidden, setText } from '../core/dom.js';
import { api } from '../core/api.js';
import { dirname } from '../core/format.js';
import { getVersion } from '../core/config.js';
import { state, onRender, onViewChange, loadState } from '../core/store.js';
import { clearTimer, setRepeating } from '../core/poll.js';
import { toast } from '../ui/toast.js';
import { openDialog, closeDialog } from '../ui/overlay.js';
import { startScan } from './scan.js';

const LOG_LINES = 400;
const LOG_REFRESH_MS = 2000;

const UPDATE_LABEL = {
  unknown: '还没有检查过',
  checking: '正在检查…',
  latest: '已是最新版本',
  outdated: '有新版本可用',
  failed: '检查失败',
};

/* ==========================================================================
   位置设置
   ========================================================================== */

function fillSettings() {
  const paths = state.paths || {};
  const jsonInput = $('input-json');
  const workshopInput = $('input-workshop');
  // 正在输入的那一格不覆盖：用户手打到一半被自动填充冲掉是最气人的事
  if (jsonInput && document.activeElement !== jsonInput) jsonInput.value = paths.json_path || '';
  if (workshopInput && document.activeElement !== workshopInput) {
    workshopInput.value = paths.workshop_dir || '';
  }
}

function showResultNotice(kind, message) {
  const box = $('autodetect-result');
  if (!box) return;
  box.className = `notice ${kind || ''}`.trim();
  box.textContent = '';
  box.append(Object.assign(document.createElement('span'), {
    className: 'notice-text',
    textContent: message,
  }));
  setHidden(box, false);
}

export function autodetect() {
  const btn = $('btn-autodetect');
  if (btn) btn.disabled = true;
  api('/api/autodetect', { body: {} })
    .then((data) => {
      if (data.found) {
        const jsonInput = $('input-json');
        const workshopInput = $('input-workshop');
        if (jsonInput) jsonInput.value = data.json_path || '';
        if (workshopInput) workshopInput.value = data.workshop_dir || '';
        showResultNotice('ok', '已找到 Wallpaper Engine 的位置，确认无误后点「保存」。');
      } else {
        showResultNotice('warn', data.error || '没有自动找到 Wallpaper Engine，请手动填写路径。');
      }
    })
    .catch((error) => showResultNotice('error', error.message))
    .finally(() => { if (btn) btn.disabled = false; });
}

export function saveConfig() {
  const jsonPath = ($('input-json') || {}).value || '';
  const workshopDir = ($('input-workshop') || {}).value || '';
  const btn = $('btn-save-config');
  if (btn) btn.disabled = true;

  api('/api/config', { body: { json_path: jsonPath.trim(), workshop_dir: workshopDir.trim() } })
    .then(async (data) => {
      const warnings = data.warnings || [];
      if (warnings.length) {
        // 保存成功但有告警（比如某个路径不存在）：说清楚是哪一条，别只报"已保存"
        showResultNotice('warn', `已保存，但需要注意：${warnings.join('；')}`);
      } else {
        showResultNotice('ok', '已保存。');
        toast('设置已保存，正在重新扫描', 'ok');
      }
      await loadState();
      startScan({ silent: true });
    })
    .catch((error) => showResultNotice('error', error.message))
    .finally(() => { if (btn) btn.disabled = false; });
}

/* ==========================================================================
   运行日志
   ========================================================================== */

export function refreshLogs() {
  api(`/api/logs?lines=${LOG_LINES}`)
    .then((data) => {
      setText($('logs-meta'), data.path ? data.path : '还没有生成日志文件。');
      const body = $('logs-body');
      if (!body) return;
      const text = (data.lines || []).join('\n');
      // 只在内容真的变了才写回：每 2 秒重写一次会让用户没法选中复制，
      // 也会把滚动位置顶掉
      if (body.textContent === text) return;
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
      body.textContent = text;
      if (atBottom) body.scrollTop = body.scrollHeight;
    })
    .catch((error) => setText($('logs-meta'), `读取日志失败：${error.message}`));
}

export function syncLogTimer() {
  const auto = $('opt-log-auto');
  const active = state.view === 'settings';
  if (active && (!auto || auto.checked)) {
    setRepeating('log', refreshLogs, LOG_REFRESH_MS);
    refreshLogs();
  } else {
    clearTimer('log');
  }
}

/* ==========================================================================
   关于
   ========================================================================== */

export function renderAbout() {
  const paths = state.paths || {};
  setText($('about-version'), getVersion());
  setText($('about-config'), paths.config_file || paths.config_name || '还没有配置文件');
  setText($('about-logs'), state.logFile ? dirname(state.logFile) : '还没有日志');

  // exe 放在 Program Files 这类不可写的目录时，配置与日志会退回 %APPDATA%。
  // 不说明的话，用户会去 exe 旁边找 config.yml，找不到就以为设置没保存。
  setHidden($('about-fallback'), !state.homeFallback);

  renderAboutUpdate();
}

export function renderAboutUpdate() {
  const update = state.update || { status: 'unknown' };
  const status = UPDATE_LABEL[update.status] || UPDATE_LABEL.unknown;
  const latest = update.status === 'outdated' && update.latest ? `（${update.latest}）` : '';
  setText($('about-update'), `${status}${latest}`);
}

export function checkUpdate() {
  const btn = $('btn-check-update');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '正在检查…';
  }

  api('/api/update/check', { body: {} })
    .then(() => pollUpdateStatus())
    .catch((error) => {
      // 409 = 已经在查了，交给轮询等结果
      if (error.status === 409) {
        pollUpdateStatus();
        return;
      }
      toast(error.message, 'error');
      restoreUpdateButton();
    });
}

function pollUpdateStatus() {
  let tries = 0;
  clearTimer('update');
  setRepeating('update', async () => {
    tries += 1;
    let data = null;
    try {
      data = await api('/api/state');
    } catch (e) {
      clearTimer('update');
      restoreUpdateButton();
      return;
    }
    if (data.update) state.update = data.update;
    renderAboutUpdate();

    const status = state.update.status;
    if (status === 'checking' && tries < 40) return;

    clearTimer('update');
    restoreUpdateButton();

    if (status === 'outdated') openUpdateDialog();
    else if (status === 'latest') toast('已经是最新版本', 'ok');
    else if (status === 'failed') toast(state.update.error || '检查更新失败', 'error');
    else toast('检查超时，请稍后再试', 'error');
  }, 500);
}

function restoreUpdateButton() {
  const btn = $('btn-check-update');
  if (!btn) return;
  btn.disabled = false;
  btn.textContent = '检查更新';
}

export function openUpdateDialog() {
  const update = state.update || {};
  setText($('update-latest'), update.latest || '未知');
  setText($('update-current'), update.current || getVersion());
  setText($('update-published'), update.published_at || '未知');
  setText($('update-notes'), update.notes || '这个版本没有提供说明。');
  openDialog('dlg-update');
}

export function closeUpdateDialog() {
  closeDialog('dlg-update');
}

export function gotoRelease() {
  api('/api/open', { body: { target: 'release' } })
    .catch((error) => toast(error.message, 'error'));
}

export function openRepo() {
  api('/api/open', { body: { target: 'repo' } })
    .catch((error) => toast(error.message, 'error'));
}

/* ==========================================================================
   接线
   ========================================================================== */

export function initSettings() {
  onRender(() => {
    fillSettings();
    renderAbout();
  });

  onViewChange((view) => {
    if (view === 'settings') {
      syncLogTimer();
      return;
    }
    clearTimer('log');
  });

  const bind = (id, fn) => {
    const el = $(id);
    if (el) el.addEventListener('click', fn);
  };

  bind('btn-autodetect', autodetect);
  bind('btn-save-config', saveConfig);
  bind('btn-check-update', checkUpdate);
  bind('btn-open-repo', openRepo);
  bind('btn-update-goto', gotoRelease);

  const auto = $('opt-log-auto');
  if (auto) auto.addEventListener('change', syncLogTimer);
}
