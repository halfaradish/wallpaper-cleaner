// Steam 连接状态
//
// 取消订阅与重新订阅都要经过 Steam 客户端，所以"Steam 在不在"是这块界面上
// 唯一一个会影响操作能否成功的环境状态。它常驻在顶栏，因为用户需要它的时候
// 往往正是操作失败的时候——那时再去某个角落找它已经晚了。
import { $, setHidden, setText } from '../core/dom.js';
import { api } from '../core/api.js';
import { state, onRender } from '../core/store.js';
import { clearTimer, setRepeating, setTimer } from '../core/poll.js';
import { toast } from '../ui/toast.js';
import { closeDialog, isDialogOpen, openDialog } from '../ui/overlay.js';

const LABEL = {
  unknown: 'Steam 未检测',
  probing: '检测中…',
  ok: 'Steam 已连接',
  unavailable: 'Steam 未运行',
};

const PROBE_INTERVAL = 500;
const PROBE_MAX_TRIES = 40;   // 500ms × 40 = 20 秒

let manualWid = null;         // 有明确目标时才能给"在 Steam 中手动操作"这个出口

export function steamReady() {
  return !!state.steam && state.steam.status === 'ok';
}

export function renderSteamBadge() {
  const badge = $('steam-badge');
  if (!badge) return;
  const steam = state.steam || { status: 'unknown' };
  const status = LABEL[steam.status] ? steam.status : 'unknown';

  badge.className = `steam ${status}`;
  badge.disabled = status === 'probing';

  const count = steam.status === 'ok' && steam.subscribed_count !== null && steam.subscribed_count !== undefined
    ? ` · ${steam.subscribed_count} 项订阅`
    : '';
  setText($('steam-label'), `${LABEL[status]}${count}`);

  // 悬停提示用后端给的 hint（一句人话），拿不到才退回固定文案
  badge.title = steam.status === 'ok'
    ? 'Steam 已连接，可以执行取消订阅与重新订阅'
    : (steam.hint || '取消订阅与重新订阅需要 Steam 在运行，点这里检测');
}

export function openSteamGuide(wid) {
  manualWid = wid || null;
  const steam = state.steam || {};

  // reason 是给程序看的机器码（steam_not_running 这种），给人看的那句话在 hint 里。
  // 直接把 reason 显示出来等于让用户读一个内部标识符。
  setText($('steam-reason'), steam.hint || '取消订阅与重新订阅都要通过 Steam 客户端完成。');

  // detail 只有比 hint 多带了信息时才单独显示。后端在没有额外信息时会把 detail
  // 也填成同一句话，那样就会同一句出现两遍。
  const detail = $('steam-detail');
  const box = $('steam-detail-box');
  if (steam.detail && steam.detail !== steam.hint) {
    setText(detail, steam.detail);
    setHidden(box, false);
  } else {
    setHidden(box, true);
  }

  // 只有在知道具体是哪一个 workshop ID 时，才给"去 Steam 里手动操作"这条路：
  // 不知道目标的话，那个按钮打开的是 Steam 首页，帮不上忙
  setHidden($('btn-steam-manual'), !manualWid);
  openDialog('dlg-steam');
}

export function closeSteamGuide() {
  closeDialog('dlg-steam');
}

/**
 * 需要 Steam 时的统一入口。
 * 返回 true 表示可以直接往下走，false 表示已经把引导弹窗打开了。
 */
export function requireSteam(wid) {
  if (steamReady()) return true;
  openSteamGuide(wid);
  return false;
}

// 任务因为 Steam 失败时的呈现：把技术错误翻译成"该去做什么"
export function handleSteamFailure(message) {
  openSteamGuide(null);
  const box = $('steam-detail-box');
  if (box && message) {
    setText($('steam-detail'), message);
    setHidden(box, false);
  }
}

export function probeSteam() {
  if (state.steam && state.steam.status === 'probing') return;
  state.steam = Object.assign({}, state.steam, { status: 'probing' });
  renderSteamBadge();

  api('/api/steam/probe', { body: {} })
    .then(() => pollSteamStatus())
    .catch((error) => {
      // 409 是"正在检测中"，不是失败，交给轮询继续等
      if (error.status === 409) {
        pollSteamStatus();
        return;
      }
      state.steam = Object.assign({}, state.steam, { status: 'unavailable', reason: error.message });
      renderSteamBadge();
    });
}

function pollSteamStatus() {
  let tries = 0;
  clearTimer('steam');
  setRepeating('steam', async () => {
    tries += 1;
    let data = null;
    try {
      data = await api('/api/state');
    } catch (e) {
      clearTimer('steam');
      return;
    }
    if (data.steam) state.steam = data.steam;

    const status = state.steam.status;
    if (status !== 'probing' || tries >= PROBE_MAX_TRIES) {
      clearTimer('steam');
      renderSteamBadge();
      if (status === 'ok' && isDialogOpen('dlg-steam')) {
        // 用户正开着引导弹窗时检测成功：直接关掉它并说一声，
        // 否则弹窗会留在那儿让人以为还得再点一次
        closeSteamGuide();
        toast('Steam 已连接', 'ok');
      } else if (status === 'unavailable' && tries >= PROBE_MAX_TRIES) {
        toast('没有检测到 Steam，请确认它正在运行', 'error');
      }
      return;
    }
    renderSteamBadge();
  }, PROBE_INTERVAL);
}

export function launchSteam() {
  api('/api/steam/launch', { body: {} })
    .then(() => {
      toast('正在启动 Steam，稍等片刻后点「重新检测」', 'ok');
      // 给 Steam 一点启动时间再自动检测一次，省掉用户点那一下
      setTimer('steam-launch-retry', () => probeSteam(), 6000);
    })
    .catch((error) => toast(error.message, 'error'));
}

export function openSteamManualPage() {
  if (!manualWid) return;
  api('/api/steam/page', { body: { wid: manualWid } })
    .catch((error) => toast(error.message, 'error'));
}

export function retrySteamProbe() {
  probeSteam();
}

export function initSteam() {
  onRender(renderSteamBadge);

  const badge = $('steam-badge');
  if (badge) {
    badge.addEventListener('click', () => {
      // 已经确认连不上时，点它是想解决问题，直接开引导；
      // 状态未知或已连接时，点它是想刷新状态
      if (state.steam && state.steam.status === 'unavailable') openSteamGuide(null);
      else probeSteam();
    });
  }

  const retry = $('btn-steam-retry');
  if (retry) retry.addEventListener('click', retrySteamProbe);

  const launch = $('btn-steam-launch');
  if (launch) launch.addEventListener('click', launchSteam);

  const manual = $('btn-steam-manual');
  if (manual) manual.addEventListener('click', openSteamManualPage);
}
