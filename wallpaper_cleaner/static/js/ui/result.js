// 结果面板：上一次操作的结论
//
// 它留在页面上而不是弹一下就走，因为"刚才到底删掉了什么"是用户最可能想回头确认的事。
// Toast 会自己消失，不适合承载这种需要反复看的信息。
import { $, h, hide, show, setText } from '../core/dom.js';

const KIND_CLASS = { ok: 'ok', warn: 'warn', error: 'error' };

/**
 * summary: [{label, value}]  数字摘要，两列排布
 * note:    补充说明（可省）
 * actions: [{label, action, kind}]  动作按钮，action 走 data-action 委托
 */
export function showResult(config) {
  const box = $('result');
  if (!box) return;

  const head = h('div', { class: 'result-head' }, [
    h('h2', { class: 'result-title', id: 'result-title', text: config.title }),
    h('button', {
      class: 'btn icon',
      type: 'button',
      'aria-label': '关闭结果',
      dataset: { action: 'dismiss-result' },
      text: '✕',
    }),
  ]);

  const parts = [head];

  const summary = (config.summary || []).filter((row) => row && row.value !== undefined);
  if (summary.length) {
    parts.push(h('ul', { class: 'result-list' }, summary.map((row) => (
      h('li', {}, [
        h('span', { text: row.label }),
        h('span', { class: 'num', text: String(row.value) }),
      ])
    ))));
  }

  if (config.note) parts.push(h('p', { class: 'result-note', text: config.note }));

  const actions = config.actions || [];
  if (actions.length) {
    parts.push(h('div', { class: 'result-actions' }, actions.map((action) => (
      h('button', {
        class: `btn ${action.kind === 'primary' ? 'primary' : 'ghost'}`,
        type: 'button',
        dataset: { action: action.action },
        text: action.label,
      })
    ))));
  }

  box.className = `result ${KIND_CLASS[config.kind] || ''}`.trim();
  box.textContent = '';
  box.append(...parts);
  show(box);
  // 把焦点交给结论本身：破坏性操作结束后，键盘用户的下一站应该是"结果"，
  // 而不是回到表格顶部重新找
  box.focus({ preventScroll: true });
}

export function hideResult() {
  const box = $('result');
  if (!box) return;
  hide(box);
  setText(box, '');
}

export function hasResult() {
  const box = $('result');
  return !!box && !box.classList.contains('hidden');
}
