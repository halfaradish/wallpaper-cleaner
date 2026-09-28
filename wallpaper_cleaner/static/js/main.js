// 入口：注册各视图的渲染函数、绑定事件、启动
//
// 事件绑定集中在这一处（与重构前的 bind() 一样），好处是"哪个控件连到哪个处理函数"
// 只需要看一个文件；渲染则相反，各自登记在视图里，新增视图不必回头改这里。
import { $ } from './core/dom.js';
import { state, loadState, selectableOrphans, subscribedItems } from './core/store.js';
import { toggleSort } from './core/sort.js';
import { initOverlayController, registerOverlay } from './ui/overlay.js';
import { closeLightbox, initLightbox } from './ui/lightbox.js';
import { toast } from './ui/toast.js';
import { hideResult } from './ui/result.js';
import { openProgressOverlay, closeProgressOverlay, progressOverlayClosable } from './ui/progress.js';
import { initConnection, reconnectNow, reloadPage } from './ui/connection.js';
import { initTheme } from './ui/theme.js';

import { initTitleLinks, initThumbFallback } from './views/rows.js';
import { initTopbar } from './views/topbar.js';
import { initStats } from './views/stats.js';
import { initSteam, probeSteam, launchSteam, openSteamManualPage, retrySteamProbe, openSteamGuide } from './views/steam.js';
import { initOrphans, renderOrphans, selectAllOrphans, openOrphanHelp, closeOrphanHelp } from './views/orphans.js';
import { initSubscribed, renderSubscribed, setSubscribedOpen } from './views/subscribed.js';
import { initBusy } from './views/busy.js';
import {
  initDrawer, openDrawer, closeDrawer, autodetect, saveConfig, syncLogTimer,
  checkUpdate, openRepo, gotoRelease, closeUpdateDialog,
} from './views/drawer.js';
import {
  initActions, openConfirm, closeConfirm, doDelete, openUnsubConfirm, closeUnsubConfirm,
  doUnsubscribe, openResubConfirm, closeResubConfirm, doResubscribe, syncUnsubMode,
} from './views/actions.js';
import { startScan, scanAgeMs, SCAN_REUSE_MS } from './views/scan.js';

function bind() {
  $('btn-scan').addEventListener('click', () => startScan());
  $('btn-advanced').addEventListener('click', () => openDrawer('advanced-drawer'));

  document.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeDrawer(btn.dataset.close));
  });

  // 标题旁的「?」：打开规则说明（原先那段常驻提示）
  $('btn-orphan-help').addEventListener('click', openOrphanHelp);
  $('btn-close-orphan-help').addEventListener('click', closeOrphanHelp);

  $('btn-delete').addEventListener('click', openConfirm);
  $('btn-cancel-delete').addEventListener('click', closeConfirm);
  $('btn-confirm-delete').addEventListener('click', doDelete);

  $('opt-recycle').addEventListener('change', (e) => {
    $('permanent-warning').classList.toggle('hidden', e.target.checked);
  });

  // 「全选已取消订阅」只选纯数字 ID 的残留：来源不明的文件夹永远不自动勾选，
  // 必须逐个手动勾（见 docs/管理面板.md 的目录分类）
  $('check-all').addEventListener('change', (e) => {
    selectableOrphans().forEach((item) => {
      if (e.target.checked) state.selected.add(item.wid);
      else state.selected.delete(item.wid);
    });
    renderOrphans();
  });

  // 已订阅列表的勾选独立于待清理列表：一个是要退的，一个是要删的
  $('check-all-sub').addEventListener('change', (e) => {
    subscribedItems().forEach((item) => {
      if (e.target.checked) state.subSelected.add(item.wid);
      else state.subSelected.delete(item.wid);
    });
    renderSubscribed();
  });

  $('btn-unsubscribe').addEventListener('click', openUnsubConfirm);
  $('btn-cancel-unsub').addEventListener('click', closeUnsubConfirm);
  $('btn-confirm-unsub').addEventListener('click', doUnsubscribe);
  $('mode-only').addEventListener('change', syncUnsubMode);
  $('mode-delete').addEventListener('change', syncUnsubMode);
  $('opt-unsub-recycle').addEventListener('change', syncUnsubMode);

  $('btn-resubscribe').addEventListener('click', openResubConfirm);
  $('btn-cancel-resub').addEventListener('click', closeResubConfirm);
  $('btn-confirm-resub').addEventListener('click', doResubscribe);

  // Steam 徽标与引导弹窗
  $('steam-badge').addEventListener('click', () => {
    if (state.steam && state.steam.status === 'unavailable') openSteamGuide();
    else probeSteam();
  });
  $('btn-steam-retry').addEventListener('click', retrySteamProbe);
  $('btn-steam-launch').addEventListener('click', launchSteam);
  $('btn-steam-manual').addEventListener('click', openSteamManualPage);

  $('toggle-subscribed').addEventListener('click', () => {
    setSubscribedOpen(!state.subscribedOpen);
  });

  $('sub-filter').addEventListener('input', (e) => {
    state.subFilter = e.target.value;
    renderSubscribed();
  });

  // 两张表的「占用大小」列头各自三态循环，互不影响；只重画自己那张表
  $('sort-orphan').addEventListener('click', () => {
    state.sortOrphans = toggleSort('sort-orphan', state.sortOrphans);
    renderOrphans();
  });

  $('sort-sub').addEventListener('click', () => {
    state.sortSub = toggleSort('sort-sub', state.sortSub);
    renderSubscribed();
  });

  $('btn-autodetect').addEventListener('click', autodetect);
  $('btn-save-config').addEventListener('click', saveConfig);
  $('opt-log-auto').addEventListener('change', syncLogTimer);

  // 关于：检查更新与 GitHub 仓库
  $('btn-check-update').addEventListener('click', checkUpdate);
  $('btn-open-repo').addEventListener('click', openRepo);
  $('btn-cancel-update').addEventListener('click', closeUpdateDialog);
  $('btn-goto-release').addEventListener('click', gotoRelease);

  initOverlayController();
  initLightbox();

  // 空状态里的出口按钮、结果面板上的按钮，统一在这里委托。
  // 这样视图之间不用互相引用，也就没有循环依赖；重绘换掉整块 DOM 也不受影响。
  document.addEventListener('click', (e) => {
    const btn = e.target && e.target.closest ? e.target.closest('[data-action]') : null;
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === 'rescan') startScan();
    else if (action === 'clear-filter') {
      state.subFilter = '';
      $('sub-filter').value = '';
      renderSubscribed();
    } else if (action === 'dismiss-result') {
      hideResult();
    }
  });

  // 顶栏进度条上的「查看日志」：把完整日志用模态调出来。
  // 扫描默认不挡界面，但排查问题时还是得能看到日志。
  $('btn-taskbar-detail').addEventListener('click', () => {
    openProgressOverlay('正在检查壁纸文件夹', { closable: true });
  });
  $('btn-close-progress').addEventListener('click', closeProgressOverlay);
  $('btn-reconnect').addEventListener('click', reconnectNow);
  $('btn-reload').addEventListener('click', reloadPage);

  // 进度弹窗默认不可关（任务在跑）；只有从顶栏手动调出来的那份才允许收起来。
  // 也因此它不参与"点遮罩关闭"，只认那个收起按钮与 Esc。
  registerOverlay('progress-overlay', {
    close: () => { if (progressOverlayClosable()) closeProgressOverlay(); },
    dismissible: false,
  });

  // Esc 只关最上面那一层，由弹层控制器统一处理；这里只管灯箱
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeLightbox();
  });
}

async function bootstrap() {
  initConnection();
  // 主题按钮的图标与无障碍名要按注入的初始值画出来；绑定也在这里做
  initTheme();
  initBusy();
  initTopbar();
  initStats();
  initSteam();
  initOrphans();
  initSubscribed();
  initDrawer();
  initActions();
  initTitleLinks();
  initThumbFallback();
  bind();

  try {
    await loadState();
  } catch (e) {
    toast(`载入失败：${e.message}`, 'error');
    return;
  }

  // 打开面板就自动检查一遍，普通用户不必自己去找「重新扫描」
  if (!state.paths || state.paths.source === 'none') return;

  // 顺便探一次 Steam（后台执行，不挡扫描）：徽标先告诉用户这个功能现在能不能用
  probeSteam();

  if (state.scan && scanAgeMs(state.scan) < SCAN_REUSE_MS) {
    // 刚扫过（刷新页面、开第二个窗口）就直接复用，选中状态只存在页面内存里，这里补上
    selectAllOrphans();
  } else {
    startScan({ auto: true });
  }
}

bootstrap();
