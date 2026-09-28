// 任务进度的两个落点：状态栏（非阻塞）与弹窗（阻塞）
//
// 为什么要有两个：打开面板时会自动扫一遍，扫描是常事，每次都弹一个盖住整页的模态
// 代价太大，所以扫描走状态栏。清理 / 取消订阅 / 重新订阅是破坏性操作，那段时间确实
// 不该让人继续点表格，所以走模态。同一个任务同时喂给两者，谁开着谁显示。
import { $, h, setHidden, setText } from '../core/dom.js';
import { clearTimer, setTimer } from '../core/poll.js';
import { closeDialog, isDialogOpen, openDialog } from './overlay.js';
import {
  setStatusCount, setStatusLogButton, setStatusProgress, setStatusText,
} from './statusbar.js';

const STATUS_DELAY = 250;   // 秒级任务不该让状态栏闪一下
const DIALOG_DELAY = 400;

let surface = null;         // 'status' | 'dialog' | null
let closable = false;       // 弹窗是否允许"在后台继续"
let lastJob = null;
let lastTitle = '正在执行';

export function scheduleStatus(title) {
  lastTitle = title || lastTitle;
  setTimer('progress-surface', () => {
    surface = 'status';
    setStatusText(lastTitle);
    setStatusProgress(lastJob ? ratioOf(lastJob) : -1);
    setStatusLogButton(true);
  }, STATUS_DELAY);
}

export function scheduleDialog(title) {
  lastTitle = title || lastTitle;
  setTimer('progress-surface', () => {
    surface = 'dialog';
    closable = false;
    setText($('dlg-progress-title'), lastTitle);
    setHidden($('btn-progress-close'), true);
    openDialog('dlg-progress');
    if (lastJob) renderJob(lastJob);
  }, DIALOG_DELAY);
}

// 从状态栏手动打开弹窗看日志：这时它是可关闭的，因为任务本来就在后台跑
export function openProgressDialog(title) {
  clearTimer('progress-surface');
  surface = 'dialog';
  closable = true;
  lastTitle = title || lastTitle;
  setText($('dlg-progress-title'), lastTitle);
  setHidden($('btn-progress-close'), false);
  openDialog('dlg-progress');
  if (lastJob) renderJob(lastJob);
}

export function progressDialogClosable() {
  return closable;
}

export function closeProgressDialog() {
  closeDialog('dlg-progress');
  // 关掉弹窗不等于任务结束：退回状态栏继续显示
  if (surface === 'dialog') surface = 'status';
}

export function renderJob(job) {
  lastJob = job;
  if (!job) return;

  const ratio = ratioOf(job);
  const message = job.message || '';
  const count = job.total ? `${job.done} / ${job.total}` : '';

  if (surface === 'status') {
    setStatusText(lastTitle);
    setStatusCount([message, count].filter(Boolean).join(' · '));
    setStatusProgress(ratio);
  }

  if (surface === 'dialog' && isDialogOpen('dlg-progress')) {
    setText($('progress-message'), message);
    setText($('progress-count'), count);
    const fill = $('progress-bar');
    fill.classList.toggle('indeterminate', ratio < 0);
    if (ratio >= 0) fill.style.transform = `scaleX(${ratio.toFixed(4)})`;
    renderLog($('progress-log'), job.lines || []);
  }
}

function ratioOf(job) {
  if (!job.total) return -1;
  return Math.max(0, Math.min(1, job.done / job.total));
}

// 日志行：debug 不显示（它只对排查有用，混在进度里只会把关键信息挤走）。
// 用 textContent 写，不拼 HTML——日志里可能有路径和标题。
function renderLog(container, lines) {
  if (!container) return;
  const frag = document.createDocumentFragment();
  for (const line of lines) {
    if (line.level === 'debug') continue;
    const kind = line.level === 'warn' ? 'lv-warn' : (line.level === 'error' ? 'lv-error' : '');
    frag.append(h('span', {
      class: kind,
      text: `[${line.time}] ${line.text}\n`,
    }));
  }
  container.textContent = '';
  container.append(frag);
  // 自动滚到底：用户看的是最新一行，而不是三秒前那一行
  container.scrollTop = container.scrollHeight;
}

export function clearProgress() {
  clearTimer('progress-surface');
  surface = null;
  lastJob = null;
  // 先把可关闭标记打开再关：关闭守卫是用来拦"用户在任务跑着的时候关掉它"的，
  // 而这里任务已经结束了。不这么做的话守卫会连程序自己的收尾一起拦掉，
  // 进度弹窗就永远留在屏幕上。
  closable = true;
  setStatusProgress(null);
  setStatusCount('');
  setStatusLogButton(false);
  if (isDialogOpen('dlg-progress')) closeDialog('dlg-progress');
}

export function progressSurface() {
  return surface;
}

export function initProgress() {
  const close = $('btn-progress-close');
  if (close) close.addEventListener('click', closeProgressDialog);
  setHidden($('btn-status-log'), true);
}
