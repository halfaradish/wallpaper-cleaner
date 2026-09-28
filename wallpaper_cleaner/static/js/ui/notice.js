// 提示条：顶栏下面那条常驻说明，以及抽屉里自动检测结果那种小块提示
//
// 两种用法：kind 为空表示中性，其余是 warn / ok / error（对应 .notice.warn 等）

export function clearNotice(el) {
  if (!el) return;
  el.className = 'notice hidden';
  el.textContent = '';
}

export function noticeText(el, kind, text) {
  if (!el) return;
  el.className = kind ? `notice ${kind}` : 'notice';
  el.textContent = text;
}

export function noticeHtml(el, kind, html) {
  if (!el) return;
  el.className = kind ? `notice ${kind}` : 'notice';
  el.innerHTML = html;
}
