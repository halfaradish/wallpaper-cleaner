// 待清理视图
//
// 这一列里是"磁盘上有、订阅列表里没有"的文件夹。真正需要人做判断的只有它们，
// 所以默认视图就是它。
import { $, setHidden, setText } from '../core/dom.js';
import { diskBytes, fmtSize } from '../core/format.js';
import { state, onRender, onBusyChange, cleanupItems, selectableOrphans } from '../core/store.js';
import { sortItems } from '../core/sort.js';
import { renderEmpty } from '../ui/empty.js';
import { clearSkeleton, scheduleSkeleton } from '../ui/skeleton.js';
import { renderRows, initRowSelection, syncSelection } from './table.js';

function sortedCleanupItems() {
  return sortItems(cleanupItems(), state.sortOrphans);
}

// 还没找到 Wallpaper Engine：此时所有"去扫描"的入口都是死路
function unconfigured() {
  return !state.paths || state.paths.source === 'none';
}

function chosenItems() {
  return cleanupItems().filter((item) => state.selected.has(item.wid));
}

function chosenBytes() {
  return chosenItems().reduce((sum, item) => sum + diskBytes(item), 0);
}

// 重新订阅的目标：勾选里排除"无法确定的文件夹"——它们的 workshop ID 不可信，
// 拿去重新订阅会去下载一个不该下载的东西
export function resubTargets() {
  return selectableOrphans().filter((item) => state.selected.has(item.wid));
}

export function renderOrphans(options) {
  const opts = options || {};
  const items = sortedCleanupItems();
  const wrap = $('orphan-wrap');
  const empty = $('orphan-empty');

  if (!state.scan) {
    // 首次扫描还在跑：先把表格的形状占住。这时候显示"还没有扫描结果"
    // 是在说一件三秒后就会变成错话的事
    if (state.busy) {
      setHidden(empty, true);
      setHidden(wrap, false);
      scheduleSkeleton('orphan-body');
      updateOrphanButtons();
      return;
    }
    clearSkeleton('orphan-body');
    setHidden(wrap, true);
    setHidden(empty, false);
    renderEmpty(empty, unconfigured()
      // 还没告诉程序去哪儿找 Wallpaper Engine 时，不该摆一个点了必然失败的"立即扫描"：
      // 这一步的动作是去设置里填路径，不是扫描
      ? {
        icon: 'scan',
        title: '还没有设置 Wallpaper Engine 的位置',
        desc: '程序需要知道 Wallpaper Engine 装在哪里，才能读取订阅记录并对照磁盘。去「设置」里点一下自动检测通常就够了。',
        action: 'go-settings',
        actionLabel: '打开设置',
      }
      : {
        icon: 'scan',
        title: '还没有扫描结果',
        desc: '扫描会读取 Wallpaper Engine 的缓存记录，再对照磁盘上的文件夹，找出已经取消订阅的残留。',
        action: 'rescan',
        actionLabel: '立即扫描',
      });
    updateOrphanButtons();
    return;
  }

  if (!items.length) {
    clearSkeleton('orphan-body');
    setHidden(wrap, true);
    setHidden(empty, false);
    const missing = (state.scan.missing || []).length;
    if (missing) {
      renderEmpty(empty, {
        icon: 'download',
        title: '磁盘上没有待清理的文件夹',
        desc: `有 ${missing} 个已订阅的壁纸还没有下载到磁盘上，它们不占空间。等 Steam 下载完成后再扫描一次，它们就会出现在「已订阅」里。`,
        action: 'rescan',
        actionLabel: '重新扫描',
      });
    } else {
      renderEmpty(empty, {
        icon: 'clean',
        title: '磁盘上很干净',
        desc: '没有发现已取消订阅却还留在磁盘上的壁纸文件夹。',
        action: 'rescan',
        actionLabel: '重新扫描',
      });
    }
    updateOrphanButtons();
    return;
  }

  setHidden(empty, true);
  setHidden(wrap, false);
  renderRows('orphan-body', items, {
    showKind: true,
    animate: !!opts.animate,
    selected: false,
  });
  syncSelection('orphan-body', state.selected);
  updateOrphanButtons();
}

export function syncOrphansSelectAll() {
  const box = $('check-all');
  if (!box) return;
  const selectable = selectableOrphans();
  const chosen = selectable.filter((item) => state.selected.has(item.wid));
  box.checked = selectable.length > 0 && chosen.length === selectable.length;
  box.indeterminate = chosen.length > 0 && chosen.length < selectable.length;
}

export function selectAllOrphans() {
  selectableOrphans().forEach((item) => state.selected.add(item.wid));
  syncSelection('orphan-body', state.selected);
  updateOrphanButtons();
}

export function updateOrphanButtons() {
  const chosen = chosenItems();
  const resub = resubTargets();

  const del = $('btn-delete');
  if (del) {
    del.disabled = state.busy || chosen.length === 0;
    del.textContent = chosen.length
      ? `清理选中 ${chosen.length} 项 · ${fmtSize(chosenBytes())}`
      : '清理选中';
  }

  const resubBtn = $('btn-resubscribe');
  if (resubBtn) {
    resubBtn.disabled = state.busy || resub.length === 0;
    resubBtn.textContent = resub.length ? `重新订阅选中 ${resub.length} 项` : '重新订阅选中';
  }

  syncOrphansSelectAll();
}

export { chosenItems as chosenOrphans };

export function initOrphans() {
  onRender(() => {
    // 数据换了才让行重新飞入一次；勾选、排序引起的重绘不走动画
    renderOrphans({ animate: true });
  });

  onBusyChange(() => {
    updateOrphanButtons();
    // 还没有数据时才需要重画（骨架屏 / 空态的切换）；已有数据时重画会把
    // 滚动位置顶回去，而扫描期间用户很可能正在翻表
    if (!state.scan) renderOrphans();
  });

  const checkAll = $('check-all');
  if (checkAll) {
    checkAll.addEventListener('change', () => {
      const selectable = selectableOrphans();
      if (checkAll.checked) {
        selectable.forEach((item) => state.selected.add(item.wid));
      } else {
        selectable.forEach((item) => state.selected.delete(item.wid));
      }
      syncSelection('orphan-body', state.selected);
      updateOrphanButtons();
    });
  }

  initRowSelection({
    tbodyId: 'orphan-body',
    getSet: () => state.selected,
    onChange: updateOrphanButtons,
  });

  scheduleSkeleton('orphan-body');
}
