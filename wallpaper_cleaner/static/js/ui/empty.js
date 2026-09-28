// 空状态：表格里没有东西可显示时，说清楚"为什么没有"和"接下来能做什么"
//
// 空着不说话的表格是最让人困惑的状态——它和"还没扫"、"扫了但没结果"、
// "筛掉了"看起来一模一样，而这三种情况该做的事完全不同。
import { h, svg } from '../core/dom.js';

// 图标同一家族、同一 stroke-width。CSP 决定了引不进图标库，所以只能内联
// （这是对设计规范里"用图标库"那条的刻意偏离，原因写在这里）。
const ICONS = {
  scan: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/><path d="M11 8v6M8 11h6"/></svg>`,
  clean: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M4 20l7.5-7.5"/><path d="M11.5 12.5L18 6a2.1 2.1 0 0 1 3 3l-6.5 6.5z"/>
    <path d="M5 15l4 4"/><path d="M15 3.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>`,
  nomatch: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="11" cy="11" r="7"/><path d="M20 20l-3.6-3.6"/><path d="M8.5 11h5"/></svg>`,
  download: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M12 3v11"/><path d="M7.5 10L12 14.5 16.5 10"/><path d="M4 18.5h16"/></svg>`,
};

/**
 * action 是 data-action 的值，由 main.js 的委托处理器接住；
 * 传了就渲染一个按钮，没传就只是说明。
 */
export function renderEmpty(container, config) {
  if (!container) return;
  container.textContent = '';
  const parts = [h('div', { class: 'empty-icon' }, svg(ICONS[config.icon] || ICONS.scan))];
  parts.push(h('p', { class: 'empty-title', text: config.title }));
  if (config.desc) parts.push(h('p', { class: 'empty-desc', text: config.desc }));
  if (config.action && config.actionLabel) {
    parts.push(h('button', {
      class: 'btn ghost',
      type: 'button',
      dataset: { action: config.action },
      text: config.actionLabel,
    }));
  }
  container.append(...parts);
}
