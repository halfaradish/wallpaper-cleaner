// 主题：跟随系统 / 浅色 / 深色 三态循环
//
// 初始值由服务端注入到 <html data-theme="...">，不在前端读系统偏好——
// 用户选了深色而系统是浅色时，等页面加载完再切会先闪一下浅色。第一帧就带着
// 正确主题，才没有闪烁。这个模块只负责循环、应用与持久化。
import { $, svg } from '../core/dom.js';
import { savePrefs } from '../core/prefs.js';
import { viewTransition } from './motion.js';
import { toast } from './toast.js';

export const MODES = ['auto', 'light', 'dark'];

const LABEL = { auto: '跟随系统', light: '浅色', dark: '深色' };

// 三个图标同一家族、同一 stroke-width、同一 24 格网格。
// 跟随系统用"半明半暗的圆"：它是这个模式唯一说得清自己的形状。
const ICONS = {
  auto: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17" />
    <path d="M12 3.5a8.5 8.5 0 0 1 0 17z" fill="currentColor" stroke="none"/></svg>`,
  light: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="4.2"/>
    <path d="M12 2.5v2.2M12 19.3v2.2M4.2 4.2l1.6 1.6M18.2 18.2l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.2 19.8l1.6-1.6M18.2 5.8l1.6-1.6"/></svg>`,
  dark: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <path d="M20 14.2A8.4 8.4 0 0 1 9.8 4 8.6 8.6 0 1 0 20 14.2z"/></svg>`,
};

let current = 'auto';

function apply(mode) {
  current = mode;
  document.documentElement.dataset.theme = mode;
  render();
}

function render() {
  const btn = $('btn-theme');
  if (!btn) return;
  const next = MODES[(MODES.indexOf(current) + 1) % MODES.length];
  btn.textContent = '';
  btn.append(svg(ICONS[current] || ICONS.auto));
  // 无障碍名说清"现在是什么、点了会变成什么"：只说"切换主题"的话，
  // 读屏用户要点两次才知道自己在哪一档
  const label = `主题：${LABEL[current]}，点击切换到${LABEL[next]}`;
  btn.setAttribute('aria-label', label);
  btn.title = label;
}

export function cycleTheme() {
  const next = MODES[(MODES.indexOf(current) + 1) % MODES.length];
  // 整屏换色是这一下最大的视觉变化，用一次交叉淡入把它接住
  viewTransition(() => apply(next));
  savePrefs({ theme: next }).then((error) => {
    if (error) toast(`主题已切换，但存不下来：${error}`, 'error');
  });
}

export function getTheme() {
  return current;
}

export function initTheme() {
  const injected = document.documentElement.dataset.theme;
  current = MODES.includes(injected) ? injected : 'auto';
  render();
  const btn = $('btn-theme');
  if (btn) btn.addEventListener('click', cycleTheme);
}
