// 已订阅视图
//
// 这一列是"现在有什么"。它同时是取消订阅的入口，所以也要能勾选。
// 本会话里已经取消订阅、但 Steam 还没同步完的行会标出来并压暗：它们此刻勾不动，
// 因为再去操作一次只会失败。
import { $, setHidden } from '../core/dom.js';
import { state, onRender, onBusyChange, subscribedItems } from '../core/store.js';
import { sortItems } from '../core/sort.js';
import { renderEmpty } from '../ui/empty.js';
import { clearSkeleton, scheduleSkeleton } from '../ui/skeleton.js';
import { renderRows, initRowSelection, syncSelection } from './table.js';

// 还没找到 Wallpaper Engine：此时所有"去扫描"的入口都是死路
function unconfigured() {
  return !state.paths || state.paths.source === 'none';
}

function filteredItems() {
  const keyword = state.subFilter.trim().toLowerCase();
  const items = subscribedItems();
  if (!keyword) return items;
  return items.filter((item) => (
    item.wid.toLowerCase().includes(keyword)
    || String(item.title || '').toLowerCase().includes(keyword)
  ));
}

export function chosenSubscribed() {
  return subscribedItems().filter((item) => state.subSelected.has(item.wid));
}

export function renderSubscribed(options) {
  const opts = options || {};
  const items = sortItems(filteredItems(), state.sortSub);
  const wrap = $('sub-wrap');
  const empty = $('sub-empty');
  const tools = document.querySelector('.view-tools');

  if (!state.scan) {
    // 首次扫描还在跑：先占住形状，而不是先说"还没有扫描结果"
    if (state.busy) {
      setHidden(tools, false);
      setHidden(empty, true);
      setHidden(wrap, false);
      scheduleSkeleton('sub-body');
      updateUnsubscribeButton();
      return;
    }
    clearSkeleton('sub-body');
    setHidden(tools, true);
    setHidden(wrap, true);
    setHidden(empty, false);
    renderEmpty(empty, unconfigured()
      ? {
        icon: 'scan',
        title: '还没有设置 Wallpaper Engine 的位置',
        desc: '程序需要知道 Wallpaper Engine 装在哪里，才能列出你的订阅。去「设置」里点一下自动检测通常就够了。',
        action: 'go-settings',
        actionLabel: '打开设置',
      }
      : {
        icon: 'scan',
        title: '还没有扫描结果',
        desc: '扫描之后这里会列出你当前订阅的全部壁纸，以及它们在磁盘上的实际占用。',
        action: 'rescan',
        actionLabel: '立即扫描',
      });
    updateUnsubscribeButton();
    return;
  }

  setHidden(tools, false);

  if (!items.length) {
    clearSkeleton('sub-body');
    setHidden(wrap, true);
    setHidden(empty, false);
    if (state.subFilter.trim()) {
      renderEmpty(empty, {
        icon: 'nomatch',
        title: '没有匹配的壁纸',
        desc: `没有标题或 workshop ID 含有「${state.subFilter.trim()}」的订阅。`,
        action: 'clear-filter',
        actionLabel: '清除筛选',
      });
    } else {
      renderEmpty(empty, {
        icon: 'clean',
        title: '当前没有订阅',
        desc: 'Steam 里没有已订阅的壁纸，所以这里没有东西可以管理。',
      });
    }
    updateUnsubscribeButton();
    return;
  }

  setHidden(empty, true);
  setHidden(wrap, false);
  renderRows('sub-body', items, {
    showKind: false,
    animate: !!opts.animate,
    selected: false,
  });
  syncSelection('sub-body', state.subSelected);
  markPendingRows();
  updateUnsubscribeButton();
}

// 等待 Steam 同步的行：压暗 + 标一句，并且把勾选框禁掉
function markPendingRows() {
  const tbody = $('sub-body');
  if (!tbody || !state.sessionUnsubscribed.size) return;
  tbody.querySelectorAll('tr[data-wid]').forEach((row) => {
    if (!state.sessionUnsubscribed.has(row.dataset.wid)) return;
    row.classList.add('pending');
    const box = row.querySelector('input[type="checkbox"]');
    if (box) {
      box.checked = false;
      box.disabled = true;
    }
    row.classList.remove('selected');
    const titleCell = row.querySelector('.title-cell');
    if (titleCell) titleCell.append(badgePending());
  });
}

function badgePending() {
  const span = document.createElement('span');
  span.className = 'badge';
  span.textContent = '等待 Steam 同步';
  span.title = '已经提交取消订阅，等 Steam 同步完成后这一行会自动消失';
  return span;
}

export function syncSubSelectAll() {
  const box = $('check-all-sub');
  if (!box) return;
  const selectable = subscribedItems().filter((item) => !state.sessionUnsubscribed.has(item.wid));
  const chosen = selectable.filter((item) => state.subSelected.has(item.wid));
  box.checked = selectable.length > 0 && chosen.length === selectable.length;
  box.indeterminate = chosen.length > 0 && chosen.length < selectable.length;
}

export function updateUnsubscribeButton() {
  const chosen = chosenSubscribed();
  const btn = $('btn-unsubscribe');
  if (btn) {
    btn.disabled = state.busy || chosen.length === 0;
    btn.textContent = chosen.length ? `取消订阅选中 ${chosen.length} 项` : '取消订阅选中';
  }
  syncSubSelectAll();
}

export function clearFilter() {
  state.subFilter = '';
  const input = $('sub-filter');
  if (input) input.value = '';
  renderSubscribed();
}

export function initSubscribed() {
  onRender(() => renderSubscribed({ animate: true }));

  onBusyChange(() => {
    updateUnsubscribeButton();
    // 同待清理：只在还没有数据时重画，避免把滚动位置顶掉
    if (!state.scan) renderSubscribed();
  });

  const filter = $('sub-filter');
  if (filter) {
    filter.addEventListener('input', () => {
      state.subFilter = filter.value;
      // 筛选是"看着打字"的动作，每敲一个字重画整表并让它重新飞入会闪得没法用，
      // 所以这里明确不走入场动画
      renderSubscribed();
    });
  }

  const checkAll = $('check-all-sub');
  if (checkAll) {
    checkAll.addEventListener('change', () => {
      const selectable = subscribedItems().filter((item) => !state.sessionUnsubscribed.has(item.wid));
      if (checkAll.checked) {
        selectable.forEach((item) => state.subSelected.add(item.wid));
      } else {
        selectable.forEach((item) => state.subSelected.delete(item.wid));
      }
      syncSelection('sub-body', state.subSelected);
      updateUnsubscribeButton();
    });
  }

  initRowSelection({
    tbodyId: 'sub-body',
    getSet: () => state.subSelected,
    onChange: updateUnsubscribeButton,
  });
}
