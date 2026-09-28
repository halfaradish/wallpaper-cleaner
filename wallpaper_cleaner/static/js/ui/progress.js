// 任务进度的两个呈现面
//
// 1. 模态弹窗（破坏性操作用）：清理 / 取消订阅 / 重新订阅时挡住整页是合理的——
//    文件正在被删，这时本来也不该乱点。弹窗里带完整日志。
// 2. 顶栏内联条（扫描用）：打开面板会自动扫一遍，每次都拿模态挡住整页代价太大。
//    内联条只占顶栏一行，想看日志可以点「查看日志」把模态调出来。
//
// 两个面共用同一份 job 数据，renderJob 同时写两处，只有可见的那个会被看到。
import { $, show, hide, setText, setHidden, isHidden } from '../core/dom.js';
import { esc } from '../core/format.js';
import { clearTimer, setTimer } from '../core/poll.js';
import { openOverlay, closeOverlay } from './overlay.js';

export const OVERLAY_DELAY = 400;   // 任务很快时不要闪一下进度弹窗
const TASKBAR_DELAY = 250;          // 内联条同样给个延迟，免得一闪而过

/* ---------------- 模态弹窗 ---------------- */

export function openProgressOverlay(title, options) {
  // 手动打开（顶栏「查看日志」）时给一个收起按钮；任务自己弹出来的不可关——
  // 文件正在被删的时候不该让人随便点
  const closable = Boolean(options && options.closable);
  setText($('progress-title'), title);
  setOverlayBar(0);
  $('progress-bar').classList.add('indeterminate');
  setText($('progress-message'), '准备中…');
  setText($('progress-count'), '');
  $('progress-log').innerHTML = '';
  setHidden($('progress-actions'), !closable);
  if (isHidden($('progress-overlay'))) openOverlay('progress-overlay');
}

// 任务还在跑的时候，进度弹窗按设计不可关；手动调出来的那份才允许收起来
export function progressOverlayClosable() {
  return !isHidden($('progress-actions'));
}

export function closeProgressOverlay() {
  closeOverlay('progress-overlay');
}

/* ---------------- 顶栏内联条 ---------------- */

export function openTaskbar(title) {
  setText($('taskbar-message'), title);
  setText($('taskbar-count'), '');
  setTaskbarBar(0);
  $('taskbar-bar').classList.add('indeterminate');
  show($('taskbar'));
}

export function closeTaskbar() {
  hide($('taskbar'));
}

/* ---------------- 调度 ---------------- */

// 延迟打开：任务在这之前就结束时 clearProgress 会把它撤掉，界面上什么都不出现。
// 四个长操作都要这一套，收在这里免得每处各写一遍 setTimeout。
export function scheduleProgressOverlay(title) {
  setTimer('overlay', () => openProgressOverlay(title), OVERLAY_DELAY);
}

export function scheduleTaskbar(title) {
  setTimer('overlay', () => openTaskbar(title), TASKBAR_DELAY);
}

function clearOverlayTimer() {
  clearTimer('overlay');
}

// 收掉两个面并撤掉待打开的定时器；任务结束时统一走这里
export function clearProgress() {
  clearOverlayTimer();
  closeProgressOverlay();
  closeTaskbar();
}

/* ---------------- 渲染 ---------------- */

// 进度条走 transform: scaleX 而不是 width：width 每次变化都要重新排版整条，
// scaleX 只动合成层。所以这里的"百分比"是缩放比例，元素宽度始终是 100%。
function scaleBar(el, pct) {
  el.style.transform = `scaleX(${Math.max(0, Math.min(1, pct / 100))})`;
}

function setOverlayBar(pct) { scaleBar($('progress-bar'), pct); }
function setTaskbarBar(pct) { scaleBar($('taskbar-bar'), pct); }

export function renderJob(job) {
  const pct = job.total ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
  const indeterminate = !job.total;
  const message = job.message || '';
  const count = job.total ? `${job.done} / ${job.total}` : '';

  setOverlayBar(pct);
  $('progress-bar').classList.toggle('indeterminate', indeterminate);
  setText($('progress-message'), message);
  setText($('progress-count'), count);

  setTaskbarBar(pct);
  $('taskbar-bar').classList.toggle('indeterminate', indeterminate);
  setText($('taskbar-message'), message || '正在执行…');
  setText($('taskbar-count'), count);

  // debug 是给排查用的（订阅缓存路径、逐个目录算大小），不该出现在普通用户眼前
  const log = $('progress-log');
  log.innerHTML = job.lines
    .filter((line) => line.level !== 'debug')
    .map((line) => `<span class="lv-${esc(line.level)}">[${esc(line.time)}] ${esc(line.text)}</span>`)
    .join('\n');
  log.scrollTop = log.scrollHeight;
}
