// Steam 状态徽标与连接引导
//
// 取消订阅与重新订阅需要 Steam 在线。这里不做"连不上就把按钮置灰"的处理：
// 徽标始终可点，点开就是原因与下一步；真去操作时若不可用，会弹出引导弹窗而不是
// 甩一句错误——"功能看起来用不了"本身就是这个功能最需要避免的事。
import { $, show, hide, isHidden } from '../core/dom.js';
import { api } from '../core/api.js';
import { state, onRender } from '../core/store.js';
import { setTimer, clearTimer } from '../core/poll.js';
import { toast } from '../ui/toast.js';
import { registerOverlay, openOverlay, closeOverlay } from '../ui/overlay.js';

const STEAM_LABEL = {
  unknown: 'Steam 未检测 · 检测',
  probing: '正在检测 Steam…',
  ok: 'Steam 可用',
  unavailable: 'Steam 不可用 · 查看原因',
};

// 只有知道要给哪张壁纸跳转时才提供「手动操作」入口，否则那个按钮点了没反应
let manualWid = '';

export function renderSteamBadge() {
  const el = $('steam-badge');
  const steam = state.steam || { status: 'unknown' };
  const status = STEAM_LABEL[steam.status] ? steam.status : 'unknown';
  el.className = `steam-badge ${status}`;
  if (status === 'ok') {
    const count = steam.subscribed_count === null || steam.subscribed_count === undefined
      ? '—' : steam.subscribed_count;
    el.textContent = `Steam 可用 · ${count} 个订阅`;
    el.title = '取消订阅与重新订阅已就绪（数字来自你的 Steam 账号）';
  } else {
    el.textContent = STEAM_LABEL[status];
    el.title = steam.hint || '取消订阅 / 重新订阅需要 Steam 在运行，点这里检测';
  }
  el.disabled = status === 'probing';
}

export function steamReady() {
  return !!(state.steam && state.steam.status === 'ok');
}

export function openSteamGuide(message, wid) {
  const steam = state.steam || {};
  $('steam-reason').textContent = message || steam.hint || 'Steam 当前不可用';
  const detail = $('steam-detail');
  if (steam.detail && !message) {
    detail.textContent = `诊断信息：${steam.detail}`;
    show(detail);
  } else {
    detail.textContent = '';
    hide(detail);
  }
  manualWid = wid || '';
  $('btn-steam-manual').classList.toggle('hidden', !manualWid);
  openOverlay('steam-overlay');
}

export function closeSteamGuide() {
  closeOverlay('steam-overlay');
}

export function requireSteam(wid) {
  if (steamReady()) return true;
  openSteamGuide('', wid);
  return false;
}

export function handleSteamFailure(message) {
  openSteamGuide(message);
  toast(message || 'Steam 操作失败', 'error');
  probeSteam();
}

export async function probeSteam() {
  clearTimer('steam');
  state.steam = Object.assign({}, state.steam, { status: 'probing', hint: '', detail: '' });
  renderSteamBadge();
  try {
    await api('/api/steam/probe', { body: {} });
  } catch (e) {
    // 409 表示已在检测中，接着轮询就是了；其它错误由轮询把真实状态刷出来
  }
  pollSteamStatus();
}

export function pollSteamStatus() {
  clearTimer('steam');
  let tries = 0;
  const tick = async () => {
    tries += 1;
    let steam = null;
    try {
      const data = await api('/api/state');
      steam = data.steam;
    } catch (e) {
      return;
    }
    if (!steam) return;
    state.steam = steam;
    renderSteamBadge();
    if (steam.status === 'probing' && tries < 40) {
      setTimer('steam', tick, 500);
      return;
    }
    // 引导弹窗开着的时候检测成功，就把它收掉——用户已经不需要看原因了
    if (steam.status === 'ok' && !isHidden($('steam-overlay'))) {
      closeSteamGuide();
      toast('Steam 已连上，可以继续操作了', 'ok');
    }
  };
  setTimer('steam', tick, 400);
}

export async function launchSteam() {
  try {
    await api('/api/steam/launch', { body: {} });
    toast('已请求启动 Steam，登录后点「重新检测」', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

export async function openSteamManualPage() {
  if (!manualWid) return;
  try {
    await api('/api/steam/page', { body: { wid: manualWid } });
    toast('已在 Steam 中打开这张壁纸的页面，可在那里手动取消订阅', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

export function retrySteamProbe() {
  $('steam-reason').textContent = '正在检测…';
  hide($('steam-detail'));
  probeSteam();
}

export function initSteam() {
  onRender(renderSteamBadge);
  registerOverlay('steam-overlay', { close: closeSteamGuide });
}
