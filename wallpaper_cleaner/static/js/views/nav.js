// 导航栏：视图切换与展开收起
//
// 展开状态有两条来源：窗口宽度（默认）与用户的手动选择。用户一旦手动点过折叠按钮，
// 宽度就不再参与决定——否则用户把窗口拉宽一点，刚收起的导航栏又自己弹回来，
// 那一下会让人觉得"我点了个寂寞"。
import { $, qsa, setHidden, setText } from '../core/dom.js';
import { getVersion } from '../core/config.js';
import { loadPrefs, savePrefs } from '../core/prefs.js';
import { VIEWS, state, setView } from '../core/store.js';
import { toast } from '../ui/toast.js';

const TITLES = { orphans: '待清理', subscribed: '已订阅', settings: '设置' };

// 1150px 是"展开的导航栏 + 表格全部列"还能同时容下的宽度。
// 再窄就得在导航栏和表格列之间选一个，而表格列是这个工具的主体。
const EXPAND_MIN_WIDTH = 1150;

function deriveRail() {
  return window.innerWidth >= EXPAND_MIN_WIDTH ? 'expanded' : 'collapsed';
}

export function applyRail(mode) {
  const rail = $('rail');
  const btn = $('btn-rail-toggle');
  if (!rail) return;
  state.rail = mode;
  rail.dataset.state = mode;
  const expanded = mode === 'expanded';
  if (btn) {
    btn.setAttribute('aria-expanded', String(expanded));
    const label = expanded ? '收起导航栏' : '展开导航栏';
    btn.setAttribute('aria-label', label);
    btn.title = label;
  }
}

export function toggleRail() {
  const next = state.rail === 'expanded' ? 'collapsed' : 'expanded';
  state.railPinned = true;
  applyRail(next);
  savePrefs({ rail: next }).then((error) => {
    if (error) toast(`导航栏状态已改变，但存不下来：${error}`, 'error');
  });
}

// 导航栏上所有跟着数据走的内容：两个计数徽章与版本号。
// 版本号也要在这里更新：/api/state 会在程序升级后带回新版本号，
// 只写一次的话导航栏会一直停在页面加载时那个旧值上。
export function renderRail() {
  const scan = state.scan;
  setText($('nav-count-orphans'), scan ? String(scan.orphans.length + scan.unknown.length) : '0');
  setText($('nav-count-subscribed'), scan ? String(scan.subscribed.length) : '0');
  setText($('rail-version'), `v${getVersion()}`);
}

export function switchView(view, options) {
  if (!VIEWS.includes(view)) return;
  const from = state.view;
  const forward = VIEWS.indexOf(view) > VIEWS.indexOf(from);

  setView(view, options);

  qsa('.rail-item').forEach((item) => {
    if (item.dataset.view === view) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });

  qsa('.view').forEach((section) => {
    section.classList.toggle('is-active', section.dataset.view === view);
  });

  setText($('view-title'), TITLES[view] || '');

  // 入场方向跟着导航栏里的上下位置走：往下点就从下往上进，往上点就反过来。
  // 方向对不上的话，那一下位移只会像抖动。
  const target = $(`view-${view}`);
  if (target) {
    target.style.setProperty('--view-enter-x', forward ? '8px' : '-8px');
    const scroller = target.querySelector('.settings') || target.querySelector('.table-wrap');
    if (scroller) scroller.scrollTop = 0;
  }

  // 两组动作按钮跟着视图显隐：待清理那组管删除，已订阅那组管取消订阅
  setHidden($('orphan-select-all-wrap'), view !== 'orphans');
  setHidden($('btn-resubscribe'), view !== 'orphans');
  setHidden($('btn-delete'), view !== 'orphans');
  setHidden($('sub-select-all-wrap'), view !== 'subscribed');
  setHidden($('btn-unsubscribe'), view !== 'subscribed');
}

export async function initNav() {
  const prefs = await loadPrefs();
  if (prefs && (prefs.rail === 'expanded' || prefs.rail === 'collapsed')) {
    state.railPinned = true;
    applyRail(prefs.rail);
  } else {
    applyRail(deriveRail());
  }

  // 没手动选过就跟着窗口宽度走
  window.addEventListener('resize', () => {
    if (!state.railPinned) applyRail(deriveRail());
  });

  qsa('.rail-item').forEach((item) => {
    item.addEventListener('click', () => switchView(item.dataset.view, { focus: true }));
  });

  const toggle = $('btn-rail-toggle');
  if (toggle) toggle.addEventListener('click', toggleRail);

  switchView(state.view);
}
