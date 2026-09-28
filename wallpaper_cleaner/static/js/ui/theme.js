// 主题：跟随系统 / 浅色 / 深色，三态循环
//
// 为什么是三态而不是一个太阳/月亮的开关：面板默认跟随系统，直接把开关做成二态会
// 把"跟随系统"这个能力弄丢——用户一旦点过，就再也回不到自动了。
//
// 为什么存在服务端而不是 localStorage：桌面模式的端口是系统分配的（每次启动都不同），
// 而 localStorage 与 cookie 都按来源隔离，换个端口就是换一个来源。打包后的 exe 默认
// 就是桌面模式，只放本地存储等于每次启动都重置——一个会忘掉自己的主题开关。
// 首次渲染也不靠接口往返：服务端把存下来的值直接注入 <html data-theme>，
// 所以不存在"先闪一下浅色再变深色"。
import { $ } from '../core/dom.js';
import { api } from '../core/api.js';
import { toast } from './toast.js';

const MODES = ['auto', 'light', 'dark'];
const LABEL = { auto: '跟随系统', light: '浅色', dark: '深色' };

// 图标三态：半明半暗的圆=跟随系统、太阳=浅色、月亮=深色。
// 内联 SVG，跟着 currentColor 走，不引图标库。
const ICONS = {
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/>',
  light: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2'
       + 'M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6M6.8 17.2l-1.6 1.6"/>',
  dark: '<path d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.4 8.4 0 1 0 10.2 10.2z"/>',
};

let current = 'auto';

function render() {
  const btn = $('btn-theme');
  if (!btn) return;
  btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
    stroke-linecap="round" aria-hidden="true">${ICONS[current]}</svg>`;
  const next = LABEL[MODES[(MODES.indexOf(current) + 1) % MODES.length]];
  // 说清"现在是什么、点一下会变成什么"：只写"切换主题"的话，用户得点一遍才知道
  const text = `主题：${LABEL[current]}，点击切换到${next}`;
  btn.title = text;
  btn.setAttribute('aria-label', text);
}

function apply(mode) {
  current = MODES.includes(mode) ? mode : 'auto';
  // auto 也要写出来：CSS 用 :not([data-theme="dark"]):not([data-theme="light"]) 匹配它
  document.documentElement.dataset.theme = current;
  render();
}

async function persist(mode) {
  try {
    await api('/api/prefs', { body: { theme: mode } });
  } catch (e) {
    // 界面已经换过去了，这里只是存不下来。如实说，不假装成功
    toast(e.message, 'error');
  }
}

export function cycleTheme() {
  const next = MODES[(MODES.indexOf(current) + 1) % MODES.length];
  apply(next);
  persist(next);
}

export function initTheme() {
  // 服务端注入的初始值就是当前模式；没有（JS 被绕过、模板被改坏）就按 auto
  apply(document.documentElement.dataset.theme || 'auto');
  const btn = $('btn-theme');
  if (btn) btn.addEventListener('click', cycleTheme);
}
