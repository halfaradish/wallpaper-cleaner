// 三态循环排序（默认 → 大到小 → 小到大），两张表各排各的
//
// 默认态不干预后端顺序（待清理本来就是大到小、已订阅是 workshop ID 升序），
// 也不显示方向指示，只留一个「这里可以点」的提示。
// 状态放在 store 里而不是 DOM 上：重扫、清理完成、勾选引起的重绘都只读它，天然保持。
import { $ } from './dom.js';
import { diskBytes } from './format.js';

export const SORT_CYCLE = { default: 'desc', desc: 'asc', asc: 'default' };
export const SORT_ARIA = { default: 'none', desc: 'descending', asc: 'ascending' };

export function sortItems(items, mode) {
  if (mode !== 'desc' && mode !== 'asc') return items;
  const wantDesc = mode === 'desc';
  return items.slice().sort((a, b) => {
    // diff 是「a 比 b 大多少」：大到小就得让更大的排前面，返回负数把它顶到前面去。
    // 排的是表里那列「磁盘占用」，与服务端默认顺序同一个口径
    const diff = diskBytes(a) - diskBytes(b);
    if (diff) return wantDesc ? -diff : diff;
    // 体积相同用 ID 兜底，比较器才是全序：连点排序不会让两行互换位置
    return a.wid < b.wid ? -1 : (a.wid > b.wid ? 1 : 0);
  });
}

export function toggleSort(id, mode) {
  const next = SORT_CYCLE[mode] || 'desc';
  const btn = $(id);
  btn.dataset.mode = next;
  btn.closest('th').setAttribute('aria-sort', SORT_ARIA[next]);
  return next;
}
