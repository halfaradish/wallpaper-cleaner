// 数字条：四个数字，回答"现在是什么情况"
//
// 数字用滚动而不是直接替换：它们是这张表存在的理由，变了就该被看见。
// 首次渲染从 0 滚上去，就是首屏那一下"数据落下来"的观感。
import { $ } from '../core/dom.js';
import { fmtSize } from '../core/format.js';
import { onRender, state } from '../core/store.js';
import { countUp } from '../ui/motion.js';
import { renderRail } from './nav.js';

function bytesOf(items, usageKey, fallbackKey) {
  return items.reduce((sum, item) => (
    sum + (Number(item[usageKey]) || Number(item[fallbackKey]) || 0)
  ), 0);
}

export function renderStats() {
  const scan = state.scan;

  // 没有扫描结果时，数字归零而不是留一个破折号：
  // 一个孤零零的占位符既读不出"没有数据"，也不比 0 更有信息量
  const subscribed = scan ? scan.subscribed.length : 0;
  const folders = scan ? scan.total_folders : 0;
  const cleanable = scan ? scan.orphans.length + scan.unknown.length : 0;
  const freed = scan
    ? bytesOf(scan.orphans, 'alloc_bytes', 'size_bytes')
      + bytesOf(scan.unknown, 'alloc_bytes', 'size_bytes')
    : 0;

  countUp($('stat-subscribed'), subscribed);
  countUp($('stat-folders'), folders);
  countUp($('stat-orphans'), cleanable);
  countUp($('stat-freed'), freed, fmtSize);

  renderRail();
}

export function initStats() {
  onRender(renderStats);
}
