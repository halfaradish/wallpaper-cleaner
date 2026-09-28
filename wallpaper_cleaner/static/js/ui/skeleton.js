// 骨架屏：首屏扫描期间先把表格的形状占住
//
// 原先是"尚未扫描。"一句灰字，扫完之后整张表突然长出来，页面高度会跳一下。
// 骨架屏保持表格的形状（同样的列数、同样的行高），内容到位时只是把占位换掉。
//
// 只在"本来就没有数据"时铺骨架：已经有上次的扫描结果时保持旧数据，
// 重扫时把表闪成骨架比看着旧数据更糟。
//
// 延迟 300ms 才铺：这个工具的扫描通常不到一秒，立即铺骨架等于闪一下，
// 和它要解决的问题一样烦人。
import { $, show, hide } from '../core/dom.js';
import { setTimer, clearTimer } from '../core/poll.js';

const ROW_COUNT = 4;
const DELAY = 300;

// 列宽必须和真表格对得上，否则换内容时照样跳
const COLUMNS = {
  'orphan-body': [
    'col-check', 'col-thumb', '', '', 'col-kind', 'col-size col-declared', 'col-size col-usage',
  ],
  'sub-body': [
    'col-check', 'col-thumb', '', '', 'col-size col-declared', 'col-size col-usage',
  ],
};

function hasRealData(tbodyId) {
  const body = $(tbodyId);
  if (!body) return false;
  return body.children.length > 0 && !body.querySelector('.skeleton-row');
}

function fill(tbodyId) {
  const body = $(tbodyId);
  if (!body || hasRealData(tbodyId)) return;
  const columns = COLUMNS[tbodyId] || [];
  body.innerHTML = Array.from({ length: ROW_COUNT }, () => (
    `<tr class="skeleton-row" aria-hidden="true">${columns.map((cls) =>
      `<td class="${cls}"><span class="skeleton"></span></td>`).join('')}</tr>`
  )).join('');
  if (tbodyId === 'orphan-body') {
    show($('orphan-wrap'));
    hide($('orphan-empty'));
  }
}

function empty(tbodyId) {
  const body = $(tbodyId);
  if (body && body.querySelector('.skeleton-row')) body.innerHTML = '';
}

// 扫描开始：等一小会儿再铺，扫得快就当没这回事
export function scheduleSkeleton() {
  clearTimer('skeleton');
  setTimer('skeleton', () => {
    fill('orphan-body');
    fill('sub-body');
  }, DELAY);
}

// 数据到位（或扫描失败）：撤掉待铺的定时器并清掉已有的骨架行
export function clearSkeleton() {
  clearTimer('skeleton');
  empty('orphan-body');
  empty('sub-body');
}
