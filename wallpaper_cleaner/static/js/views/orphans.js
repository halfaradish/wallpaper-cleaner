// 「待清理」列表：渲染、勾选、两个主按钮的文案，以及规则说明弹窗
import { $, show, hide, setText } from '../core/dom.js';
import { fmtSize, diskBytes } from '../core/format.js';
import { state, cleanupItems, selectableOrphans, onRender } from '../core/store.js';
import { sortItems } from '../core/sort.js';
import { buildRow, initRowCheckboxes } from './rows.js';
import { registerOverlay, openOverlay, closeOverlay } from '../ui/overlay.js';
import { emptyMarkup } from '../ui/empty.js';

export function renderOrphans() {
  const items = sortItems(cleanupItems(), state.sortOrphans);
  const body = $('orphan-body');
  const wrap = $('orphan-wrap');
  const empty = $('orphan-empty');
  const selectAll = $('check-all');

  setText($('orphan-count'), String(items.length));

  if (!state.scan) {
    hide(wrap);
    show(empty);
    empty.innerHTML = emptyMarkup({
      icon: 'scan',
      title: '尚未扫描',
      desc: '磁盘上还没有可比较的结果。打开面板会自动检查一遍，也可以现在就开始。',
      action: 'rescan',
      actionLabel: '立即扫描',
    });
    selectAll.checked = false;
    selectAll.disabled = true;
    updateDeleteButton();
    return;
  }

  if (items.length === 0) {
    // 「已订阅但还没下载」是另一回事：本地确实没有对应目录，不能说成"完全一致"
    const missing = state.scan.missing ? state.scan.missing.length : 0;
    hide(wrap);
    show(empty);
    empty.innerHTML = emptyMarkup({
      icon: missing ? 'download' : 'clean',
      title: missing ? '没有需要清理的内容' : '磁盘很干净',
      desc: missing
        ? `磁盘上的文件夹都和订阅列表对得上。另有 ${missing} 张已订阅的壁纸还没下载到本地。`
        : '磁盘上的文件夹和订阅列表完全一致，没有多余的残留。',
      action: 'rescan',
      actionLabel: '重新扫描',
    });
    selectAll.checked = false;
    selectAll.disabled = true;
    updateDeleteButton();
    return;
  }

  show(wrap);
  hide(empty);
  selectAll.disabled = state.busy;

  body.innerHTML = items.map((item) => {
    const checked = state.selected.has(item.wid);
    const revived = state.sessionResubscribed.has(item.wid);
    const kindLabel = item.kind === 'orphan' ? '已取消订阅' : '无法确定的文件夹';
    const kindCell = revived
      ? '<span class="badge resubscribed" title="已提交给 Steam，正在等它把订阅同步回来">已重新订阅</span>'
      : `<span class="badge ${item.kind}">${kindLabel}</span>`;
    // 刚重新订阅的目录不再允许勾选删除：本地订阅记录还没刷新，删了会被 Steam 重下
    return buildRow(item, {
      checked,
      pending: revived,
      disabled: revived,
      title: item.title || '',
      showType: true,
      kindCell,
    });
  }).join('');

  syncSelectAll();
  updateDeleteButton();
  updateResubscribeButton();
}

export function syncSelectAll() {
  const selectAll = $('check-all');
  const orphans = selectableOrphans();
  if (!orphans.length) { selectAll.checked = false; selectAll.indeterminate = false; return; }
  const picked = orphans.filter((o) => state.selected.has(o.wid)).length;
  selectAll.checked = picked === orphans.length;
  selectAll.indeterminate = picked > 0 && picked < orphans.length;
}

export function selectAllOrphans() {
  state.selected.clear();
  selectableOrphans().forEach((item) => state.selected.add(item.wid));
  renderOrphans();
}

export function updateDeleteButton() {
  const chosen = cleanupItems().filter((i) => state.selected.has(i.wid));
  const bytes = chosen.reduce((sum, i) => sum + diskBytes(i), 0);
  const btn = $('btn-delete');
  btn.disabled = state.busy || chosen.length === 0;
  btn.textContent = chosen.length
    ? `清理选中 ${chosen.length} 项 · ${fmtSize(bytes)}`
    : '清理选中';
}

export function updateResubscribeButton() {
  // 只有纯数字 ID 的残留能重新订阅：无法确定的文件夹没有可用的 workshop ID
  const orphans = new Set(selectableOrphans().map((i) => i.wid));
  const chosen = cleanupItems().filter((i) => state.selected.has(i.wid) && orphans.has(i.wid));
  const btn = $('btn-resubscribe');
  btn.disabled = state.busy || chosen.length === 0;
  btn.textContent = chosen.length
    ? `重新订阅选中 ${chosen.length} 项`
    : '重新订阅选中';
}

// 可以被重新订阅的当前选中项：来源不明的文件夹没有可用的 workshop ID
export function resubTargets() {
  const orphans = new Set(selectableOrphans().map((i) => i.wid));
  return cleanupItems().filter((i) => state.selected.has(i.wid) && orphans.has(i.wid));
}

export function chosenOrphans() {
  return cleanupItems().filter((i) => state.selected.has(i.wid));
}

export function openOrphanHelp() {
  openOverlay('orphan-help-overlay');
}

export function closeOrphanHelp() {
  closeOverlay('orphan-help-overlay');
}

export function initOrphans() {
  onRender(renderOrphans);
  registerOverlay('orphan-help-overlay', { close: closeOrphanHelp });
  initRowCheckboxes('orphan-body', (wid, checked) => {
    if (checked) state.selected.add(wid); else state.selected.delete(wid);
    updateDeleteButton();
    updateResubscribeButton();
    syncSelectAll();
  }, () => {
    // Shift 连选后按钮只需更新一次（循环里每次都更新是 O(n²)）
    updateDeleteButton();
    updateResubscribeButton();
    syncSelectAll();
  });
}
