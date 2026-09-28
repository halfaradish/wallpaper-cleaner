// 弹层与抽屉的统一控制器
//
// 原先每个弹窗各自一对 open/close 函数，Esc 与"点遮罩关闭"各自维护一份硬编码清单，
// 加了弹窗很容易漏掉其中一份；Esc 还是一口气把所有弹层都关掉（而不是最上面那个），
// 焦点更是从来没人管过——键盘用户打开弹窗后，Tab 会跑到背后的页面上去。
//
// 这里集中处理四件事：
//   1. 开关与登记：视图只调 openOverlay/closeOverlay，清单只有一份
//   2. Esc 只关最上面那一层（用打开顺序做栈）
//   3. 焦点：打开时送进弹层，关闭时还给触发它的那个元素
//   4. 模态时把背景设成 inert，Tab 与读屏都出不去；再加一道 Tab 循环兜底
//
// 抽屉是非模态的（没有遮罩、背后照常能点），所以它只参与 1、2、3，不做 4。
import { $, show, hide, isHidden } from '../core/dom.js';

const FOCUSABLE = [
  'a[href]', 'button:not(:disabled)', 'input:not(:disabled)',
  'select:not(:disabled)', 'textarea:not(:disabled)', '[tabindex]:not([tabindex="-1"])',
].join(',');

const registry = new Map();   // id -> { close, modal, dismissible }
const stack = [];             // 当前打开的弹层 id，按打开顺序
const restoreFocus = new Map(); // id -> 打开前焦点所在元素

export function registerOverlay(id, options) {
  const o = options || {};
  registry.set(id, {
    close: o.close || (() => closeOverlay(id)),
    // 默认是模态：有遮罩、背后不该能点。抽屉显式传 modal: false
    modal: o.modal !== false,
    // 点遮罩空白处是否关闭。进度弹窗与抽屉都不该被误关
    dismissible: o.dismissible !== false,
  });
}

function config(id) {
  return registry.get(id) || { modal: true, dismissible: true, close: () => closeOverlay(id) };
}

function focusable(container) {
  return Array.from(container.querySelectorAll(FOCUSABLE))
    .filter((el) => el.offsetParent !== null || el === document.activeElement);
}

function setBackgroundInert(inert) {
  // 模态弹层之外的两块内容。用 inert 而不是 aria-hidden：
  // inert 同时挡住焦点与点击，aria-hidden 只影响读屏
  ['main', '.topbar'].forEach((sel) => {
    const el = document.querySelector(sel);
    if (!el) return;
    if (inert) el.setAttribute('inert', '');
    else el.removeAttribute('inert');
  });
}

// 背景要不要锁，取决于"还有没有模态弹层开着"，而不是"栈空不空"。
// 抽屉也在栈里但它不是模态的：按栈空判断的话，在抽屉之上关掉一个弹窗，
// 背景会永远锁着——页面上什么都点不动了。
function syncBackgroundInert() {
  setBackgroundInert(stack.some((id) => config(id).modal));
}

// 弹窗语义在打开时补上，而不是在 HTML 里给七个弹层各写一遍：
// role=dialog + aria-modal 让读屏知道"进了一个弹窗"，aria-labelledby 指到标题，
// 否则读屏只会念一句"对话框"而说不出这是哪一个。
function applyDialogSemantics(el) {
  if (el.dataset.dialogReady) return;
  el.dataset.dialogReady = '1';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  const heading = el.querySelector('h2, h3');
  if (heading) {
    if (!heading.id) heading.id = `${el.id}-title`;
    el.setAttribute('aria-labelledby', heading.id);
  }
}

export function openOverlay(id) {
  const el = $(id);
  if (!el || !isHidden(el)) return;
  const cfg = config(id);

  // 记住是谁把弹层叫出来的，关掉时把焦点还回去
  restoreFocus.set(id, document.activeElement);
  stack.push(id);
  show(el);

  if (cfg.modal) {
    // 抽屉是非模态的，保留 <aside> 本身的 complementary 语义，不套 dialog
    applyDialogSemantics(el);
  }
  syncBackgroundInert();

  // 焦点送进弹层：优先第一个可聚焦元素，没有就落在容器本身
  const targets = focusable(el);
  if (targets.length) targets[0].focus();
  else {
    el.setAttribute('tabindex', '-1');
    el.focus();
  }
}

export function closeOverlay(id) {
  const el = $(id);
  if (!el || isHidden(el)) return;

  hide(el);
  const at = stack.lastIndexOf(id);
  if (at >= 0) stack.splice(at, 1);
  syncBackgroundInert();

  // 焦点归还：回到当初触发它的那个控件，键盘用户才不会"迷路"
  const back = restoreFocus.get(id);
  restoreFocus.delete(id);
  if (back && back.isConnected) back.focus();
}

export function isOverlayOpen(id) {
  return !isHidden($(id));
}

export function overlayIds() {
  return Array.from(registry.keys());
}

// Esc 只关最上面那一层。原先是一口气全关：从确认框里弹出 Steam 引导时，
// 一次 Esc 会把两个都收掉，用户以为自己只关了一层
export function closeTopOverlay() {
  if (!stack.length) return;
  const id = stack[stack.length - 1];
  config(id).close();
  // 关不掉就到此为止（进度弹窗在任务进行中不可关），别继续往下关
  if (isOverlayOpen(id)) return;
}

export function closeAllOverlays() {
  // 从最上层往下关，保证每一层的焦点归还顺序是对的
  while (stack.length) {
    const id = stack[stack.length - 1];
    config(id).close();
    if (isOverlayOpen(id)) {
      // 关不掉（进度弹窗在任务中）就别死循环
      stack.splice(stack.lastIndexOf(id), 1);
    }
  }
  syncBackgroundInert();
}

// Tab 在模态弹层里循环。inert 已经挡住了背景，但焦点走到弹层最后一个元素后
// 仍会跳到浏览器地址栏，所以再兜一道。
function trapTab(e) {
  if (e.key !== 'Tab' || !stack.length) return;
  const id = stack[stack.length - 1];
  if (!config(id).modal) return;
  const el = $(id);
  if (!el) return;
  const items = focusable(el);
  if (!items.length) {
    e.preventDefault();
    return;
  }
  const first = items[0];
  const last = items[items.length - 1];
  const active = document.activeElement;
  if (e.shiftKey && (active === first || !el.contains(active))) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && (active === last || !el.contains(active))) {
    e.preventDefault();
    first.focus();
  }
}

export function initOverlayController() {
  // 点遮罩空白处关闭：只有登记过、且允许遮罩关闭的弹层才响应
  document.addEventListener('click', (e) => {
    const el = e.target;
    if (!el || !el.classList || !el.classList.contains('overlay')) return;
    const cfg = registry.get(el.id);
    if (cfg && cfg.dismissible) cfg.close();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeTopOverlay();
      return;
    }
    trapTab(e);
  });
}
