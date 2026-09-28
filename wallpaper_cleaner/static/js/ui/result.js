// 上一次操作的结果
//
// 以前这些信息挤在一条 4 秒就消失的提示里，而清理的"跳过"要按原因分成四种
// （仍在订阅 / 目录刚改动过 / 刚重新订阅 / Steam 里已不在列表），取消订阅还要
// 分"已提交但未确认生效"与"删除失败"——一句话读完本来就不可能。
// 现在落成一张常驻卡片：可以慢慢看，看完自己关掉。
import { $, show, hide } from '../core/dom.js';

let current = null;

/**
 * kind      'ok' | 'warn' | 'error'，决定左边那条色带
 * title     一句话结论（"已清理 3 项，释放 268.00 KB"）
 * details   分点说明，每条是一句完整的话
 * actions   [{ action, label }]，action 交给 main.js 的委托处理
 * note      收尾的一句补充（比如"文件在回收站里，可以恢复"）
 */
export function showResult(options) {
  const o = options || {};
  const host = $('result');
  if (!host) return;

  const kind = o.kind || 'ok';
  const details = (o.details || []).filter(Boolean);
  const actions = o.actions || [];

  const detailHtml = details.length
    ? `<ul class="result-details">${details.map((d) => `<li>${d}</li>`).join('')}</ul>`
    : '';
  const noteHtml = o.note ? `<p class="result-note">${o.note}</p>` : '';
  const actionHtml = actions.length
    ? `<div class="result-actions">${actions
        .map((a) => `<button type="button" class="btn ghost" data-action="${a.action}">${a.label}</button>`)
        .join('')}</div>`
    : '';

  host.className = `result ${kind}`;
  host.innerHTML = `
    <div class="result-head">
      <h2 class="result-title" id="result-title">${o.title || ''}</h2>
      <button type="button" class="icon-btn result-close" data-action="dismiss-result"
        aria-label="关闭结果" title="关闭">✕</button>
    </div>
    ${detailHtml}${noteHtml}${actionHtml}`;

  current = o;
  show(host);
  // 结果卡出现在页面顶部，但用户此刻的视线多半在下面的按钮上；把焦点送过去，
  // 读屏用户才会立刻知道"操作有结论了"。
  host.focus({ preventScroll: false });
}

export function hideResult() {
  const host = $('result');
  if (!host) return;
  hide(host);
  host.innerHTML = '';
  current = null;
}

export function hasResult() {
  return current !== null;
}
