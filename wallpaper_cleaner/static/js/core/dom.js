// DOM 小工具：取元素与显示/隐藏
//
// 显示与隐藏全站都靠 .hidden 类，收在这里是为了以后要加 aria-hidden / inert 时
// 只需要改一处，不用去追散落的 classList 调用。
export const $ = (id) => document.getElementById(id);

export function show(el) {
  if (el) el.classList.remove('hidden');
}

export function hide(el) {
  if (el) el.classList.add('hidden');
}

export function setHidden(el, hidden) {
  if (el) el.classList.toggle('hidden', Boolean(hidden));
}

export function isHidden(el) {
  return !el || el.classList.contains('hidden');
}

export function setText(el, text) {
  if (el) el.textContent = text;
}
