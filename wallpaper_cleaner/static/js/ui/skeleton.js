// 骨架屏：首屏扫描期间先把表格的形状占住
//
// 只在"还没有任何数据"时出现。已经有数据时再换成骨架屏是退步——用户宁可看旧数据，
// 也不想看一堆灰条。占位块的宽度按各列真实内容的形状给，数据到达时不会跳一下。
import { $, h } from '../core/dom.js';
import { clearTimer, setTimer } from '../core/poll.js';

const ROW_COUNT = 4;
const DELAY = 300;   // 300ms 内就出数据的话，闪一下骨架屏反而更糟

const LAYOUTS = {
  'orphan-body': ['col-check', 'col-thumb', 'col-id', 'title-cell', 'col-kind', 'col-size col-declared', 'col-size'],
  'sub-body': ['col-check', 'col-thumb', 'col-id', 'title-cell', 'col-size col-declared', 'col-size'],
};

function buildRow(columns) {
  return h('tr', { class: 'skeleton-row' }, columns.map((cls) => (
    h('td', { class: cls }, h('div', { class: 'skeleton' }))
  )));
}

export function scheduleSkeleton(tbodyId) {
  const columns = LAYOUTS[tbodyId];
  if (!columns) return;
  setTimer(`skeleton-${tbodyId}`, () => {
    const tbody = $(tbodyId);
    // 已经有行就不画了：迟到的骨架屏比没有骨架屏更让人困惑
    if (!tbody || tbody.children.length) return;
    tbody.append(...Array.from({ length: ROW_COUNT }, () => buildRow(columns)));
  }, DELAY);
}

export function clearSkeleton(tbodyId) {
  clearTimer(`skeleton-${tbodyId}`);
  const tbody = $(tbodyId);
  if (tbody) tbody.textContent = '';
}
