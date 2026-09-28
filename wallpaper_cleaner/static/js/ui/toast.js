// 提示条：可以同时存在多条
//
// 以前是单个元素，后来的消息直接覆盖前一条——一次任务里"已提交但未确认生效"
// 和"跳过 2 张"是两条独立的信息，前者会被后者吃掉。现在每条一个节点、各自计时。
//
// live 语义挂在每条自己身上：普通提示 role=status（等当前朗读完再说），
// 错误 role=alert（立刻打断）。容器不挂 aria-live，避免嵌套播报两遍。
import { $ } from '../core/dom.js';

const LIMIT = 4;               // 同时最多几条，超出就把最早的收掉
const DURATION = { error: 8000, ok: 4000, '': 4000 };
const EXIT_MS = 400;           // 退场动画的兜底时长

function dismiss(el) {
  if (!el.isConnected || el.classList.contains('leaving')) return;
  el.classList.add('leaving');
  let done = false;
  const remove = () => {
    if (done) return;
    done = true;
    el.remove();
  };
  el.addEventListener('animationend', remove, { once: true });
  // 兜底：动画被 prefers-reduced-motion 或强制颜色模式关掉时 animationend 不一定来
  setTimeout(remove, EXIT_MS);
}

export function toast(message, kind) {
  const host = $('toasts');
  if (!host) return;

  const el = document.createElement('div');
  el.className = `toast${kind ? ` ${kind}` : ''}`;
  el.setAttribute('role', kind === 'error' ? 'alert' : 'status');

  const text = document.createElement('span');
  text.className = 'toast-text';
  text.textContent = message;

  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', '关闭这条提示');
  close.textContent = '✕';

  el.append(text, close);
  host.appendChild(el);

  while (host.children.length > LIMIT) dismiss(host.firstElementChild);

  // 计时在悬停/聚焦时暂停：鼠标移上去往往正是要读它，这时消失最恼人
  let timer = null;
  let remaining = DURATION[kind] || DURATION[''];
  let startedAt = 0;

  const resume = () => {
    if (remaining <= 0) return;
    startedAt = Date.now();
    timer = setTimeout(() => dismiss(el), remaining);
  };
  const pause = () => {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    remaining -= Date.now() - startedAt;
  };

  el.addEventListener('mouseenter', pause);
  el.addEventListener('mouseleave', resume);
  el.addEventListener('focusin', pause);
  el.addEventListener('focusout', resume);
  close.addEventListener('click', () => dismiss(el));

  resume();
}
