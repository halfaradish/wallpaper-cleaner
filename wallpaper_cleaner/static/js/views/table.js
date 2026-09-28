// 两张表共用的零件：行构造、缩略图、标题链接、勾选（含 Shift 连选）
//
// 待清理表与已订阅表的行长得几乎一样，差别只在有没有「类型」列。
// 两处各写一遍模板的代价不是多打几行字，而是"改了一处忘了另一处"——
// 这两张表里点标题打开文件夹、勾选框的无障碍名，都必须完全一致。
import { $, h, svg } from '../core/dom.js';
import { api } from '../core/api.js';
import { diskBytes, diskHint, fmtSize, hasTitle, displayTitle, NO_TITLE_HINT } from '../core/format.js';
import { toast } from '../ui/toast.js';

const ICON_FOLDER = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M3 7a2 2 0 0 1 2-2h3.4l1.8 2H19a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>`;

const ICON_IMAGE = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10.5" r="1.5"/>
  <path d="M4 16.5l4.5-4.5 3 3 3.5-3.5 5 5"/></svg>`;

function thumbUrl(wid) {
  return `/api/thumb?wid=${encodeURIComponent(wid)}`;
}

export function rowLabel(item) {
  return hasTitle(item) ? item.title : `无标题（${item.wid}）`;
}

function thumbCell(item) {
  const src = thumbUrl(item.wid);
  const img = h('img', {
    class: 'thumb',
    src,
    alt: '',
    loading: 'lazy',
    decoding: 'async',
  });
  const button = h('button', {
    class: 'thumb-btn',
    type: 'button',
    dataset: { thumbSrc: src },
    'aria-label': `放大预览：${rowLabel(item)}`,
    title: '点击放大预览',
  }, img);
  return h('td', { class: 'col-thumb' }, button);
}

function titleCell(item) {
  const named = hasTitle(item);
  const link = h('button', {
    class: named ? 'title-link' : 'title-link muted',
    type: 'button',
    dataset: { wid: item.wid },
    title: named ? `在资源管理器中打开「${item.title}」的文件夹` : NO_TITLE_HINT,
    'aria-label': named
      ? `在资源管理器中打开「${item.title}」的文件夹`
      : `在资源管理器中打开这个文件夹（${item.wid}）`,
  }, [
    svg(ICON_FOLDER),
    h('span', { class: 'title-text', text: displayTitle(item) }),
  ]);

  const cell = h('td', { class: 'title-cell' }, link);
  if (item.content_missing) {
    cell.append(h('span', { class: 'badge missing', text: '内容已缺失' }));
  }
  return cell;
}

function kindBadge(item) {
  if (item.kind === 'orphan') {
    return h('span', { class: 'badge warn', text: '已取消订阅' });
  }
  if (item.kind === 'unknown') {
    return h('span', {
      class: 'badge unknown',
      text: '无法确定的文件夹',
      title: '缓存里查不到这个文件夹的记录，不会被「全选已取消订阅」勾中',
    });
  }
  return null;
}

/**
 * options.selected  是否勾选
 * options.pending   等待 Steam 同步（勾不动）
 * options.showKind  是否渲染「类型」列（只有待清理表有）
 * options.extra     追加在标题后的额外徽章（已订阅表用它标"等待同步"）
 */
export function buildRow(item, options) {
  const opts = options || {};
  const pending = !!opts.pending;

  const checkbox = h('input', {
    type: 'checkbox',
    checked: !!opts.selected,
    disabled: pending,
    dataset: { wid: item.wid },
    'aria-label': `选择 ${rowLabel(item)}（${item.wid}）`,
  });

  const cells = [
    h('td', { class: 'col-check' }, checkbox),
    thumbCell(item),
    h('td', { class: 'col-id' }, h('span', { class: 'wid', text: item.wid })),
    titleCell(item),
  ];

  if (opts.showKind) {
    const badge = kindBadge(item);
    cells.push(h('td', { class: 'col-kind' }, badge));
  }

  cells.push(h('td', {
    class: 'col-size col-declared',
    text: item.declared_size || '未知',
    title: 'Wallpaper Engine 缓存里记录的下载大小',
  }));

  cells.push(h('td', {
    class: 'col-size',
    text: fmtSize(diskBytes(item)),
    title: diskHint(item),
  }));

  const classes = [];
  if (opts.selected) classes.push('selected');
  if (pending) classes.push('pending');
  if (opts.animate) classes.push('row-enter');

  return h('tr', { class: classes.join(' '), dataset: { wid: item.wid } }, cells);
}

/**
 * 整表重绘。
 * animate 只在"数据本身换了"时为真（首次载入、重新扫描之后）。
 * 勾选、排序这类重绘不该让整张表重新飞入一次——那不是动效，那是闪烁。
 */
export function renderRows(tbodyId, items, options) {
  const tbody = $(tbodyId);
  if (!tbody) return;
  tbody.textContent = '';
  const opts = options || {};
  const frag = document.createDocumentFragment();
  for (const item of items) frag.append(buildRow(item, opts));
  tbody.append(frag);
}

// 只更新勾选态，不重建行：勾一下重画整张表会打断 Shift 连选，
// 也会让刚才选中的那行的入场动画再放一遍
export function syncSelection(tbodyId, selected) {
  const tbody = $(tbodyId);
  if (!tbody) return;
  tbody.querySelectorAll('input[type="checkbox"][data-wid]').forEach((box) => {
    const on = selected.has(box.dataset.wid);
    box.checked = on;
    const row = box.closest('tr');
    if (row) row.classList.toggle('selected', on);
  });
}

/**
 * 勾选交互（含 Shift 连选）
 *
 * 用 click 而不是 change：change 事件里拿不到 shiftKey，而 Shift 连选正是靠它判断的。
 * 浏览器在 click 之前已经把复选框切过去了，所以 box.checked 就是"点完之后的新状态"——
 * 连选范围内的其他行要跟着它走，而不是取反。
 */
export function initRowSelection(config) {
  const tbody = $(config.tbodyId);
  if (!tbody) return;
  let lastIndex = null;

  tbody.addEventListener('click', (event) => {
    const box = event.target.closest('input[type="checkbox"][data-wid]');
    if (!box || !tbody.contains(box)) return;

    const boxes = Array.from(tbody.querySelectorAll('input[type="checkbox"][data-wid]'));
    const index = boxes.indexOf(box);
    const selected = config.getSet();
    const next = box.checked;

    if (event.shiftKey && lastIndex !== null && lastIndex !== index) {
      const from = Math.min(lastIndex, index);
      const to = Math.max(lastIndex, index);
      for (let i = from; i <= to; i += 1) {
        const target = boxes[i];
        if (target.disabled) continue;
        target.checked = next;
        const row = target.closest('tr');
        if (row) row.classList.toggle('selected', next);
        if (next) selected.add(target.dataset.wid);
        else selected.delete(target.dataset.wid);
      }
    } else {
      const row = box.closest('tr');
      if (row) row.classList.toggle('selected', next);
      if (next) selected.add(box.dataset.wid);
      else selected.delete(box.dataset.wid);
    }

    lastIndex = index;
    config.onChange();
  });
}

// 点标题打开文件夹：委托到 document，表格怎么重绘都不用重新绑定
export function initTitleLinks() {
  document.addEventListener('click', (event) => {
    const link = event.target.closest('.title-link[data-wid]');
    if (!link) return;
    const wid = link.dataset.wid;
    link.disabled = true;
    api('/api/reveal', { body: { wid } })
      .catch((error) => toast(error.message, 'error'))
      .finally(() => { link.disabled = false; });
  });
}

// 预览图加载失败：换成占位图标，而不是留一个浏览器的破图标记。
// 用捕获阶段监听：error 事件不冒泡，只能在捕获阶段拿到。
export function initThumbFallback() {
  document.addEventListener('error', (event) => {
    const img = event.target;
    if (!img || img.tagName !== 'IMG' || !img.classList.contains('thumb')) return;
    const holder = h('span', { class: 'thumb-empty', title: '这张壁纸没有可用的预览图' }, svg(ICON_IMAGE));
    img.replaceWith(holder);
  }, true);
}
