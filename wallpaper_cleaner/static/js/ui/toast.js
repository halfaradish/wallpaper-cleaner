// Toast：一次性的、不需要用户回应的提示
//
// 三条规矩：
// - 最多同时 4 条，超出时挤掉最旧的一条。堆满屏幕的 Toast 等于没有提示。
// - 错误用 role="alert"（会被立刻播报），其余用 role="status"（等当前朗读结束）。
// - 鼠标悬停或键盘聚焦时暂停倒计时：正在读的那条不该在读一半时消失。
import { $, h, setText } from '../core/dom.js';

const LIMIT = 4;
const DURATION = { error: 8000, ok: 4000, info: 4000 };
const EXIT_FALLBACK_MS = 400;

export function toast(message, kind) {
  const box = $('toasts');
  if (!box) return;

  const type = kind === 'error' || kind === 'ok' ? kind : 'info';
  const text = h('span', { class: 'toast-text', text: message });
  const close = h('button', {
    class: 'toast-close',
    type: 'button',
    'aria-label': '关闭提示',
    text: '✕',
    on: { click: () => dismiss(el) },
  });
  const el = h('div', {
    class: `toast ${type}`,
    role: type === 'error' ? 'alert' : 'status',
  }, [text, close]);

  box.append(el);
  while (box.children.length > LIMIT) dismiss(box.firstElementChild, true);

  let remaining = DURATION[type];
  let startedAt = Date.now();
  let timer = setTimeout(() => dismiss(el), remaining);

  const pause = () => {
    clearTimeout(timer);
    remaining -= Date.now() - startedAt;
  };
  const resume = () => {
    startedAt = Date.now();
    clearTimeout(timer);
    timer = setTimeout(() => dismiss(el), Math.max(600, remaining));
  };

  el.addEventListener('mouseenter', pause);
  el.addEventListener('mouseleave', resume);
  el.addEventListener('focusin', pause);
  el.addEventListener('focusout', resume);
}

function dismiss(el, immediate) {
  if (!el || el.dataset.leaving) return;
  el.dataset.leaving = '1';
  if (immediate) {
    el.remove();
    return;
  }
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    el.remove();
  };
  el.addEventListener('animationend', finish, { once: true });
  el.classList.add('leaving');
  // 动画被 reduced-motion 压掉时 animationend 仍会触发，但事件偶尔会丢，留个兜底
  setTimeout(finish, EXIT_FALLBACK_MS);
}

export function clearToasts() {
  const box = $('toasts');
  if (box) box.textContent = '';
}

// 供无障碍场景使用：把提示同步写进状态栏，屏幕阅读器之外的用户也看得到结论
export function toastIntoStatusBar(message) {
  setText($('status-text'), message);
}
