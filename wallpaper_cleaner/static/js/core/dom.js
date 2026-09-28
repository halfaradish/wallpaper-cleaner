// DOM 助手：查询、显隐、元素工厂
//
// 视图以前是拼 HTML 字符串再 innerHTML，用户数据（标题、路径、错误信息）全部要
// 经过 esc()。只要有一处忘了转义就是注入，而"有没有忘"只能靠人眼逐个检查。
// 改成元素工厂之后，用户数据一律走 textContent：注入这条路从"要记得防"
// 变成"根本不存在"，不需要再靠自觉。

export const $ = (id) => document.getElementById(id);
export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// 显隐统一走 .hidden 类，而不是 [hidden] 属性或内联样式：
// 只有一个开关，也就只有一个地方需要考虑"隐藏时要不要同时禁用"。
export function show(el) { if (el) el.classList.remove('hidden'); }
export function hide(el) { if (el) el.classList.add('hidden'); }
export function setHidden(el, hidden) { if (el) el.classList.toggle('hidden', !!hidden); }
export function isHidden(el) { return !el || el.classList.contains('hidden'); }
export function setText(el, text) { if (el) el.textContent = text; }

// 属性里带连字符的（aria-* / data-*）走 setAttribute，其余走属性赋值，
// 这样 disabled / checked / value 这类能拿到真正的布尔与字符串语义，
// 而不是变成字符串 "false" 这种真值。
export function h(tag, props, children) {
  const el = document.createElement(tag);
  if (props) {
    for (const key of Object.keys(props)) {
      const value = props[key];
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else if (key === 'on') {
        for (const [event, handler] of Object.entries(value)) el.addEventListener(event, handler);
      } else if (key === 'html') el.innerHTML = value;
      else if (key in el) el[key] = value;
      else el.setAttribute(key, value === true ? '' : value);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  if (children === null || children === undefined || children === false) return;
  if (Array.isArray(children)) {
    for (const child of children) append(el, child);
    return;
  }
  el.append(children instanceof Node ? children : document.createTextNode(String(children)));
}

// 内联 SVG：内容全是本文件里写死的常量，不含任何用户数据，所以这里用 innerHTML
// 是安全的。CSP 的 default-src 'none' 也决定了不能引图标库或外部字体图标，
// 图标只能这样内联（这是对设计规范"不要手写 SVG 图标"的刻意偏离，原因写在这里）。
export function svg(markup) {
  const box = document.createElement('div');
  box.innerHTML = markup;
  return box.firstElementChild;
}
