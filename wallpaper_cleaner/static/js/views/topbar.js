// 顶栏里那行自动生成的副标题，以及顶栏下面那条常驻提示条
import { $ } from '../core/dom.js';
import { esc } from '../core/format.js';
import { state, onRender, onBusyChange } from '../core/store.js';
import { noticeHtml } from '../ui/notice.js';
import { openDrawer } from './drawer.js';

export function renderConfigLine() {
  const paths = state.paths;
  const el = $('config-line');
  if (!paths) return;

  if (paths.source === 'none') {
    el.textContent = '还没找到 Wallpaper Engine，请在「高级」里设置位置';
    el.classList.add('warn');
    return;
  }

  el.classList.remove('warn');
  const count = state.scan ? state.scan.subscribed.length : null;
  el.textContent = count === null
    ? '已找到 Wallpaper Engine'
    : `已找到 Wallpaper Engine · ${count} 张壁纸已订阅`;
}

export function renderNotice() {
  const el = $('notice');
  const paths = state.paths;
  const scan = state.scan;

  if (paths && paths.source === 'none') {
    noticeHtml(el, 'warn', `<span>${esc(paths.hint || '还没设置 Wallpaper Engine 的位置')}</span>
      <span class="notice-actions"><button class="btn ghost" id="notice-advanced">打开高级</button></span>`);
    $('notice-advanced').addEventListener('click', () => openDrawer('advanced-drawer'));
    return;
  }

  if (scan && scan.orphans.length === 0 && scan.unknown.length === 0) {
    // 只有"已订阅但还没下载"时不能说成完全一致，那句话会让人以为本地就是全部
    const missing = scan.missing ? scan.missing.length : 0;
    noticeHtml(el, missing ? '' : 'ok', missing
      ? `<span>没有需要清理的内容。另有 ${missing} 张已订阅的壁纸还没下载到本地。</span>`
      : '<span>很干净，没有需要清理的内容。磁盘上的文件夹和订阅列表完全一致。</span>');
    return;
  }

  if (!scan) {
    // 不能一律说"正在检查"：后端刚重启（或扫描失败）时根本没有扫描在跑，
    // 那句话就是假的，而"界面说在做、其实没做"正是最难排查的那种状态
    noticeHtml(el, '', state.busy
      ? '<span>正在检查壁纸文件夹…</span>'
      : '<span>还没有扫描结果。点「重新扫描」开始检查，或先到「高级」里确认位置设置。</span>');
    return;
  }

  el.className = 'notice hidden';
  el.innerHTML = '';
}

export function initTopbar() {
  onRender(renderConfigLine);
  onRender(renderNotice);
  // 提示条的措辞取决于"是不是正在扫描"（没结果时不能一律说"正在检查"），
  // 所以忙碌状态一变就得重画一次，否则会停在上一句话上
  onBusyChange(renderNotice);
}
