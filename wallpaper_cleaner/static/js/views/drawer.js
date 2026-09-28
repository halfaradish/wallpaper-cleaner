// 高级抽屉：位置设置 / 运行日志 / 关于（含检查更新）
import { $, show, hide, setText } from '../core/dom.js';
import { dirname } from '../core/format.js';
import { api } from '../core/api.js';
import { state, loadState, onRender } from '../core/store.js';
import { getVersion } from '../core/config.js';
import { setTimer, setRepeating, clearTimer } from '../core/poll.js';
import { noticeText, clearNotice } from '../ui/notice.js';
import { toast } from '../ui/toast.js';
import { registerOverlay, openOverlay, closeOverlay } from '../ui/overlay.js';
import { startScan } from './scan.js';

const LOG_REFRESH_MS = 2000;

/* ---------------- 位置设置 ---------------- */

export function fillSettings() {
  const paths = state.paths;
  if (!paths) return;
  if (document.activeElement !== $('input-json')) $('input-json').value = paths.json_path || '';
  if (document.activeElement !== $('input-workshop')) $('input-workshop').value = paths.workshop_dir || '';
  setText($('settings-meta'), paths.config_file
    ? `配置文件：${paths.config_file}`
    : '配置文件尚未创建，保存后写入配置所在目录的 config.yml');
}

export async function autodetect() {
  const box = $('autodetect-result');
  noticeText(box, '', '正在检测…');
  try {
    const data = await api('/api/autodetect', { body: {} });
    if (data.found) {
      $('input-json').value = data.json_path;
      $('input-workshop').value = data.workshop_dir;
      noticeText(box, 'ok', '检测成功，已填入下方路径，确认后点击「保存」。');
    } else {
      noticeText(box, 'warn', data.error || '未能自动检测到路径，请手动填写。');
    }
  } catch (e) {
    noticeText(box, 'error', e.message);
  }
}

export async function saveConfig() {
  try {
    const data = await api('/api/config', {
      body: {
        json_path: $('input-json').value.trim(),
        workshop_dir: $('input-workshop').value.trim(),
      },
    });
    await loadState();
    if (data.warnings && data.warnings.length) {
      noticeText($('autodetect-result'), 'warn', `已保存，但请注意：${data.warnings.join('；')}`);
    } else {
      toast('配置已保存', 'ok');
      closeDrawer('advanced-drawer');
      startScan({ auto: true });
    }
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* ---------------- 运行日志 ---------------- */

export function stopLogTimer() {
  clearTimer('log');
}

export function syncLogTimer() {
  stopLogTimer();
  if (!$('opt-log-auto').checked) return;
  if ($('advanced-drawer').classList.contains('hidden')) return;
  // 周期性的：用 setRepeating 而不是 setTimer，后者只跑一次
  setRepeating('log', refreshLogs, LOG_REFRESH_MS);
}

export async function refreshLogs() {
  try {
    const data = await api('/api/logs?lines=400');
    state.logFile = data.path || state.logFile;
    setText($('logs-meta'), data.path || '当前还没有日志文件');
    const body = $('logs-body');
    body.textContent = data.lines && data.lines.length ? data.lines.join('\n') : '（暂无日志）';
    body.scrollTop = body.scrollHeight;
  } catch (e) {
    setText($('logs-body'), `读取日志失败：${e.message}`);
  }
}

/* ---------------- 关于 ---------------- */

export function renderAbout() {
  const paths = state.paths;
  setText($('about-version'), `版本：wallpaper-cleaner ${getVersion()}`);
  setText($('about-config'), (paths && paths.config_file)
    ? `配置文件：${paths.config_file}`
    : '配置文件：尚未创建，保存后写入');
  setText($('about-logs'), state.logFile
    ? `日志目录：${dirname(state.logFile)}`
    : '日志目录：—');
  renderAboutUpdate();
}

// 只在点「检查更新」时才联网：一次检查就是一次对外的请求，不该由"打开面板"这个
// 动作替用户决定。查完的结果留在服务端，重开抽屉还能看到上次的结论。
export function renderAboutUpdate() {
  const info = state.update || { status: 'unknown' };
  const at = info.checked_at ? ` · 检查于 ${info.checked_at}` : '';
  let text = '更新：尚未检查';
  if (info.status === 'checking') text = '更新：正在检查…';
  else if (info.status === 'latest') text = `更新：已是最新（${info.latest || info.current}）${at}`;
  else if (info.status === 'outdated') text = `更新：有新版本 ${info.latest}（当前 ${info.current}）${at}`;
  else if (info.status === 'failed') text = `更新：暂时查不了 —— ${info.error || '原因未知'}`;
  setText($('about-update'), text);
}

function setUpdateBusy(busy) {
  const btn = $('btn-check-update');
  btn.disabled = busy;
  btn.textContent = busy ? '正在检查…' : '检查更新';
}

export async function checkUpdate() {
  clearTimer('update');
  state.update = Object.assign({}, state.update, { status: 'checking' });
  setUpdateBusy(true);
  renderAboutUpdate();
  try {
    await api('/api/update/check', { body: {} });
  } catch (e) {
    // 409 表示已经在检查了，接着轮询就是；其它错误由轮询把真实状态刷出来
  }
  pollUpdateStatus();
}

export function pollUpdateStatus() {
  clearTimer('update');
  let tries = 0;
  const tick = async () => {
    tries += 1;
    let info = null;
    try {
      const data = await api('/api/state');
      info = data.update;
    } catch (e) {
      setUpdateBusy(false);
      return;
    }
    if (!info) {
      setUpdateBusy(false);
      return;
    }
    state.update = info;
    renderAboutUpdate();
    if (info.status === 'checking' && tries < 40) {
      setTimer('update', tick, 500);
      return;
    }
    // 无论结论是什么，按钮都要能再点一次；失败也不是"功能坏了"，重试就好
    setUpdateBusy(false);
    if (info.status === 'outdated') openUpdateDialog();
    else if (info.status === 'latest') toast(`已是最新版本（${info.latest || info.current}）`, 'ok');
    else if (info.status === 'failed') toast(info.error || '检查更新失败', 'error');
    else if (info.status === 'checking') toast('检查更新超时了，过一会儿再试', 'error');
  };
  setTimer('update', tick, 400);
}

export function openUpdateDialog() {
  const info = state.update || {};
  setText($('update-latest'), info.latest || '—');
  setText($('update-current'), info.current || getVersion());
  setText($('update-published'), info.published_at ? `发布于 ${info.published_at}` : '');
  // 说明正文来自 Release，按纯文本显示：不渲染 Markdown，也就没有注入这回事
  setText($('update-notes'), info.notes || '（这个版本没有写更新说明）');
  openOverlay('update-overlay');
}

export function closeUpdateDialog() {
  closeOverlay('update-overlay');
}

export async function gotoRelease() {
  try {
    await api('/api/open', { body: { target: 'release' } });
    closeUpdateDialog();
  } catch (e) {
    toast(e.message, 'error');
  }
}

export async function openRepo() {
  try {
    await api('/api/open', { body: { target: 'repo' } });
    toast('已在浏览器中打开 GitHub 仓库', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

/* ---------------- 抽屉本身 ---------------- */

export function openDrawer(id) {
  openOverlay(id);
  if (id !== 'advanced-drawer') return;
  // 每次打开都清掉上次的检测提示，避免残留的"检测成功"误导
  clearNotice($('autodetect-result'));
  renderAbout();
  refreshLogs();
  syncLogTimer();
}

export function closeDrawer(id) {
  closeOverlay(id);
  if (id === 'advanced-drawer') stopLogTimer();
}

export function initDrawer() {
  onRender(fillSettings);
  onRender(renderAbout);
  // 抽屉是非模态的：它没有遮罩，背后照常可以点，所以不设 inert、不锁 Tab；
  // 但也不该被"点遮罩"误关（它根本没有遮罩），只认 Esc 与关闭按钮
  registerOverlay('advanced-drawer', { close: () => closeDrawer('advanced-drawer'), modal: false, dismissible: false });
  registerOverlay('update-overlay', { close: closeUpdateDialog });
}
