// 弹窗控制器
//
// 用原生 <dialog> + showModal()，所以焦点陷阱、top-layer、背景 inert、Esc 关到
// 最上面那一层，全部由浏览器负责。这个模块只剩两件浏览器不管的事：
//
// 1. 关闭守卫：任务进行中不许关（关掉窗口会中断删除，留下半残目录）。
// 2. 退场动画：原生 dialog 的关闭是瞬时的，这里先加 .is-closing 等动画走完再真关。
//
// 自己实现过的那套（手写焦点陷阱 + 手动同步背景 inert）出过一个很难查的 bug：
// 关掉模态后如果还有非模态层开着，背景会永久保持 inert，整页点不动。
// 那类状态现在根本不存在了。
import { $ } from '../core/dom.js';
import { prefersReduced } from './motion.js';

const registry = new Map();
const CLOSE_FALLBACK_MS = 400;

/**
 * canClose() 返回 false 时，这个弹窗拒绝关闭（Esc、遮罩点击、关闭按钮都会走它）
 * dismissible: false 表示点遮罩不关（破坏性确认框默认就是这种）
 */
export function registerDialog(id, config) {
  registry.set(id, Object.assign({ dismissible: true }, config));
}

export function isDialogOpen(id) {
  const el = $(id);
  return !!(el && el.open);
}

export function openDialog(id) {
  const el = $(id);
  if (!el || el.open) return;
  // showModal 会把焦点交给第一个可聚焦元素（没有则给弹窗本身），
  // 也会记住打开前的焦点，关闭时还回去
  el.showModal();
}

export function closeDialog(id) {
  const el = $(id);
  if (!el || !el.open) return;
  const config = registry.get(id) || {};
  if (config.canClose && !config.canClose()) return;
  if (el.classList.contains('is-closing')) return;

  if (prefersReduced()) {
    finishClose(el);
    return;
  }

  el.classList.add('is-closing');
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    finishClose(el);
  };
  el.addEventListener('transitionend', finish, { once: true });
  setTimeout(finish, CLOSE_FALLBACK_MS);
}

function finishClose(el) {
  el.classList.remove('is-closing');
  if (el.open) el.close();
}

// 任何一个弹窗开着：日志自动刷新之类的后台动作靠它让路
export function anyDialogOpen() {
  return !!document.querySelector('dialog[open]');
}

export function initDialogs() {
  document.querySelectorAll('dialog.dialog').forEach((el) => {
    // Esc：先问守卫，再走带退场动画的关闭流程。
    // 不 preventDefault 的话浏览器会直接关掉，动画和守卫都来不及生效。
    el.addEventListener('cancel', (event) => {
      event.preventDefault();
      closeDialog(el.id);
    });

    // 点遮罩关闭。弹窗的 padding 是 0，内容由 .dialog-inner 撑满，
    // 所以"点到 dialog 本身"就等于点到遮罩。
    el.addEventListener('click', (event) => {
      if (event.target !== el) return;
      const config = registry.get(el.id) || {};
      if (config.dismissible === false) return;
      closeDialog(el.id);
    });

    // 兜底：万一有别的路径把它关了（比如表单 method="dialog"），
    // 也要保证退出动画的类不会留在元素上
    el.addEventListener('close', () => el.classList.remove('is-closing'));
  });
}
