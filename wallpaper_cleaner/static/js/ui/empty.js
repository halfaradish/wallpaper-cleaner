// 组合式空状态
//
// 原先空状态就是一句灰字（"没有待清理的文件夹。"），看起来像出了故障。
// 现在给三样东西：一个图标表明状态、一句说明讲清为什么、一个出口告诉用户能做什么。
// 图标一律内联 SVG，跟着 currentColor 走，不引图标库也不引图片文件。

const ICONS = {
  // 放大镜：还没有结果
  scan: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21"/>',
  // 对勾：没有可做的
  clean: '<path d="M4 12.5 9.5 18 20 6.5"/>',
  // 带斜杠的放大镜：筛选没命中
  nomatch: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 21 21"/><path d="M8 13 13 8"/>',
  // 纸飞机/箭头：等待下载
  download: '<path d="M12 3v12"/><path d="M7.5 10.5 12 15l4.5-4.5"/><path d="M4 19h16"/>',
};

function icon(name) {
  const body = ICONS[name] || ICONS.scan;
  return `<svg class="empty-icon" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
}

/**
 * 生成空状态的 HTML。
 * action 传了才出按钮，按钮带 data-action，由 main.js 统一委托处理
 * （视图模块之间不互相引用，避免循环依赖）。
 */
export function emptyMarkup(options) {
  const o = options || {};
  const desc = o.desc ? `<p class="empty-desc">${o.desc}</p>` : '';
  const action = o.action
    ? `<button type="button" class="btn ghost" data-action="${o.action}">${o.actionLabel}</button>`
    : '';
  return `${icon(o.icon)}<p class="empty-title">${o.title}</p>${desc}${action}`;
}
