// 入口：把各模块接起来，然后跑第一次加载
//
// 这里刻意只有"接线"和"启动顺序"，没有业务逻辑。启动顺序是这个文件唯一需要
// 仔细看的东西：初始化函数负责登记渲染回调，loadState 负责触发它们——
// 顺序反了的话第一次渲染什么都不会发生。
import { $ } from './core/dom.js';
import { state, loadState } from './core/store.js';
import { toggleSort } from './core/sort.js';
import { toast } from './ui/toast.js';
import { hideResult } from './ui/result.js';
import { openProgressDialog } from './ui/progress.js';
import { initConnection, reconnectNow, reloadPage } from './ui/connection.js';
import { closeLightbox, initLightbox, isLightboxOpen } from './ui/lightbox.js';
import { initTheme } from './ui/theme.js';
import { initZoom } from './ui/zoom.js';
import { initTitleLinks, initThumbFallback } from './views/table.js';
import { initNav, switchView } from './views/nav.js';
import { initShell } from './views/shell.js';
import { initStats } from './views/stats.js';
import { initSteam, probeSteam } from './views/steam.js';
import { initOrphans, renderOrphans, selectAllOrphans } from './views/orphans.js';
import { initSubscribed, renderSubscribed, clearFilter } from './views/subscribed.js';
import { initSettings } from './views/settings.js';
import { initBusy } from './views/busy.js';
import { initActions } from './views/actions.js';
import { initDialogRegistry } from './views/dialogs.js';
import { startScan, scanAgeMs, SCAN_REUSE_MS } from './views/scan.js';

function bind() {
  const bind = (id, fn) => {
    const el = $(id);
    if (el) el.addEventListener('click', fn);
  };

  bind('btn-scan', () => startScan());
  bind('btn-reconnect', reconnectNow);
  bind('btn-reload', reloadPage);
  bind('btn-status-log', () => openProgressDialog('正在检查壁纸文件夹'));

  bind('sort-orphan', () => {
    state.sortOrphans = toggleSort('sort-orphan', state.sortOrphans);
    renderOrphans();
  });
  bind('sort-sub', () => {
    state.sortSub = toggleSort('sort-sub', state.sortSub);
    renderSubscribed();
  });

  // 提示条与结果面板里的按钮都走这一条委托：它们的内容是动态生成的，
  // 逐个绑定要么漏要么重复绑
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-action]');
    if (!trigger) return;
    const action = trigger.dataset.action;
    if (action === 'rescan') startScan();
    else if (action === 'clear-filter') clearFilter();
    else if (action === 'dismiss-result') hideResult();
    else if (action === 'go-settings') switchView('settings', { focus: true });
  });

  // 灯箱的 Esc 单独处理：弹窗有自己的 Esc（原生 dialog 的 cancel 事件），
  // 两者不会同时开着，所以不需要仲裁
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isLightboxOpen()) closeLightbox();
  });
}

// 兜底：模块里的异常如果没人接，界面会停在一个"看起来还在忙"的状态上，
// 而用户完全不知道发生了什么，也没有任何线索可报告。至少要说出来。
// 这里只处理脚本错误（冒泡阶段），图片加载失败走 table.js 的捕获监听单独处理。
function installErrorReporting() {
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    const message = (reason && reason.message) || String(reason);
    toast(`出了点问题：${message}`, 'error');
  });
  window.addEventListener('error', (event) => {
    if (event.message) toast(`出了点问题：${event.message}`, 'error');
  });
}

async function bootstrap() {
  installErrorReporting();

  // 先登记回调，再 loadState —— 反过来的话第一次渲染不会发生
  initConnection();
  initTheme();
  initZoom();
  initDialogRegistry();
  initBusy();
  initShell();
  initStats();
  initSteam();
  initOrphans();
  initSubscribed();
  initSettings();
  initActions();
  initTitleLinks();
  initThumbFallback();
  initLightbox();
  bind();

  // 导航栏要先就位：它决定初始视图，而初始视图决定第一次渲染画哪一块
  await initNav();

  try {
    await loadState();
  } catch (e) {
    toast(`载入失败：${e.message}`, 'error');
    return;
  }

  // 没找到 Wallpaper Engine 时不做任何事：没有可扫的东西，
  // 界面上那条提示条已经在告诉用户该去设置里填路径了
  const paths = state.paths;
  if (!paths || paths.source === 'none') return;

  probeSteam();

  if (state.scan && scanAgeMs(state.scan) < SCAN_REUSE_MS) {
    // 刚扫过就直接用，并把可清理的项预先勾上：这是用户打开面板最可能想做的事
    selectAllOrphans();
    return;
  }
  startScan({ auto: true });
}

bootstrap();
