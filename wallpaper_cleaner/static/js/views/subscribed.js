// 「已订阅壁纸」列表：折叠、筛选、勾选与取消订阅按钮的文案
import { $, show, hide, setText } from '../core/dom.js';
import { esc } from '../core/format.js';
import { state, subscribedItems, onRender } from '../core/store.js';
import { sortItems } from '../core/sort.js';
import { buildRow, initRowCheckboxes } from './rows.js';
import { emptyMarkup } from '../ui/empty.js';

export function renderSubscribed() {
  const scan = state.scan;
  const body = $('sub-body');
  const wrap = $('sub-wrap');
  const empty = $('sub-empty');
  const selectAll = $('check-all-sub');

  if (!scan) {
    setText($('sub-count'), '0');
    hide(wrap);
    show(empty);
    empty.innerHTML = emptyMarkup({
      icon: 'scan',
      title: '尚未扫描',
      desc: '扫描一次才知道你订阅了哪些壁纸。',
    });
    selectAll.checked = false;
    selectAll.disabled = true;
    updateUnsubscribeButton();
    return;
  }

  setText($('sub-count'), String(scan.subscribed.length));
  const filter = state.subFilter.trim().toLowerCase();
  const matched = filter
    ? scan.subscribed.filter((i) =>
        i.wid.toLowerCase().includes(filter) ||
        String(i.title || '').toLowerCase().includes(filter))
    : scan.subscribed;
  // 先筛选后排序：排序作用于当前看得见的这些行
  const items = sortItems(matched, state.sortSub);

  if (items.length === 0) {
    hide(wrap);
    show(empty);
    empty.innerHTML = filter
      ? emptyMarkup({
          icon: 'nomatch',
          title: '没有匹配的壁纸',
          desc: `没有 workshop ID 或标题包含「${esc(state.subFilter)}」的壁纸。`,
          action: 'clear-filter',
          actionLabel: '清除筛选',
        })
      : emptyMarkup({
          icon: 'clean',
          title: '没有已订阅的壁纸',
          desc: 'Wallpaper Engine 里当前没有任何订阅。',
        });
    selectAll.checked = false;
    selectAll.disabled = true;
    updateUnsubscribeButton();
    return;
  }

  show(wrap);
  hide(empty);
  selectAll.disabled = state.busy;

  body.innerHTML = items.map((item) => {
    const checked = state.subSelected.has(item.wid);
    // 本会话刚取消订阅的：Steam 那边已经改了，本地记录还没跟上，先标出来
    const pending = state.sessionUnsubscribed.has(item.wid);
    const mark = pending
      ? '<br><span class="badge orphan" title="Steam 正在后台处理，重新扫描后会从这张表里消失">已取消订阅</span>'
      : '';
    return buildRow(item, { checked, pending, widMark: mark });
  }).join('');

  syncSelectAllSub();
  updateUnsubscribeButton();
}

export function syncSelectAllSub() {
  const selectAll = $('check-all-sub');
  const items = subscribedItems();
  if (!items.length) { selectAll.checked = false; selectAll.indeterminate = false; return; }
  const picked = items.filter((i) => state.subSelected.has(i.wid)).length;
  selectAll.checked = picked === items.length;
  selectAll.indeterminate = picked > 0 && picked < items.length;
}

export function updateUnsubscribeButton() {
  const chosen = subscribedItems().filter((i) => state.subSelected.has(i.wid));
  const btn = $('btn-unsubscribe');
  btn.disabled = state.busy || chosen.length === 0;
  btn.textContent = chosen.length
    ? `取消订阅选中 ${chosen.length} 项`
    : '取消订阅选中';
}

export function chosenSubscribed() {
  return subscribedItems().filter((i) => state.subSelected.has(i.wid));
}

export function setSubscribedOpen(open) {
  state.subscribedOpen = open;
  $('subscribed-body').classList.toggle('hidden', !open);
  $('toggle-subscribed').setAttribute('aria-expanded', String(open));
}

export function initSubscribed() {
  onRender(renderSubscribed);
  initRowCheckboxes('sub-body', (wid, checked) => {
    if (checked) state.subSelected.add(wid); else state.subSelected.delete(wid);
    updateUnsubscribeButton();
    syncSelectAllSub();
  }, () => {
    // Shift 连选后按钮只需更新一次
    updateUnsubscribeButton();
    syncSelectAllSub();
  });
}
