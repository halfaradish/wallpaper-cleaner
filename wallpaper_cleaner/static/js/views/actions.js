// 三条破坏性流程：清理 / 取消订阅 / 重新订阅
//
// 三者共用同一套骨架：确认弹窗列出"到底动了哪些东西"，任务在模态里跑，
// 结束后把结论留在结果面板上。差异只在参数与结果措辞。
//
// 两条刻意的设计：
// - 确认弹窗里逐条列出目标（最多 40 条，其余折成一句），而不是只说"N 项"。
//   只给数字的话，用户没法在按下按钮之前发现自己选错了。
// - 删除与取消订阅走的是"先提交、再让行淡出、然后才重画"。行直接消失的话，
//   用户没法确认走的到底是哪几行。
import { $, h, setHidden, setText } from '../core/dom.js';
import { api } from '../core/api.js';
import { diskBytes, fmtSize, confirmLabel } from '../core/format.js';
import { state, loadState, setBusy, scanId } from '../core/store.js';
import { pollJob } from '../core/job.js';
import { scheduleDialog, clearProgress } from '../ui/progress.js';
import { showResult, hideResult } from '../ui/result.js';
import { toast } from '../ui/toast.js';
import { closeDialog, openDialog } from '../ui/overlay.js';
import { leaveThenRemove } from '../ui/motion.js';
import { chosenOrphans, resubTargets } from './orphans.js';
import { chosenSubscribed } from './subscribed.js';
import { handleSteamFailure, requireSteam } from './steam.js';
import { startScan } from './scan.js';

const MAX_LISTED = 40;

function fillPickList(container, items, describe) {
  if (!container) return;
  container.textContent = '';
  const frag = document.createDocumentFragment();
  items.slice(0, MAX_LISTED).forEach((item) => {
    frag.append(h('li', {}, [
      h('span', { text: confirmLabel(item) }),
      h('span', { class: 'size', text: describe(item) }),
    ]));
  });
  if (items.length > MAX_LISTED) {
    frag.append(h('li', { class: 'more', text: `另有 ${items.length - MAX_LISTED} 项未列出` }));
  }
  container.append(frag);
}

// 让即将消失的行先播完退场动画，再重画表格
function waitForLeave(wids) {
  return new Promise((resolve) => {
    const rows = Array.from(document.querySelectorAll('tbody tr[data-wid]'))
      .filter((row) => wids.has(row.dataset.wid));
    leaveThenRemove(rows, resolve);
  });
}

function chosenBytes(items) {
  return items.reduce((sum, item) => sum + diskBytes(item), 0);
}

function summaryRow(label, value) {
  return value ? { label, value: String(value) } : null;
}

/* ==========================================================================
   清理
   ========================================================================== */

export function openConfirm() {
  const items = chosenOrphans();
  if (!items.length) return;

  setText($('confirm-count'), String(items.length));
  setText($('confirm-size'), fmtSize(chosenBytes(items)));
  fillPickList($('confirm-list'), items, (item) => fmtSize(diskBytes(item)));

  // 回收站不可用（非 Windows）时不给这个选项，直接说明删除不可恢复
  const recycle = $('opt-recycle');
  const row = recycle ? recycle.closest('.check') : null;
  const supported = state.recycleSupported;
  if (recycle) recycle.checked = supported;
  setHidden(row, !supported);
  setHidden($('permanent-warning'), supported);

  openDialog('dlg-confirm');
}

export function closeConfirm() {
  closeDialog('dlg-confirm');
}

export function doDelete() {
  const items = chosenOrphans();
  if (!items.length) return;
  const wids = items.map((item) => item.wid);
  const recycle = !!(state.recycleSupported && $('opt-recycle').checked);

  closeConfirm();
  setBusy(true);
  hideResult();
  scheduleDialog('正在清理');

  api('/api/delete', { body: { wids, scan_id: scanId(), recycle } })
    .then(({ job_id }) => {
      pollJob(job_id, async (job) => {
        const result = job.result || {};
        const deletedList = result.deleted || [];
        const deleted = new Set(deletedList.map((row) => row.wid));
        const failed = result.failed || [];
        const skipped = (result.skipped || []).length;

        // 让即将消失的行先播完退场动画，再重画表格
        await waitForLeave(deleted);
        await loadState();

        const freed = result.freed_bytes || 0;
        showResult({
          // 一项都没删成时不该报"成功"：用户点的是清理，不是"看一眼跳过了什么"
          kind: failed.length || !deletedList.length ? 'warn' : 'ok',
          title: deletedList.length
            ? `清理完成：${deletedList.length} 项，释放 ${fmtSize(freed)}`
            : '没有清理任何项目',
          summary: [
            summaryRow('已删除', deletedList.length),
            summaryRow('跳过', skipped),
            summaryRow('失败', failed.length),
          ].filter(Boolean),
          note: noteForDelete(result),
          actions: [{ label: '重新扫描', action: 'rescan' }],
        });
      },
      // 重扫必须放在 onSettled 而不是 onDone 里：onDone 执行时忙碌标记还没清掉，
      // 而 startScan 开头就是 if (state.busy) return —— 放在里面会被自己吞掉，
      // 结果是删完之后界面停在"还没有扫描结果"上。onSettled 就是为这个顺序存在的。
      () => startScan({ silent: true }),
      (message) => {
        showResult({ kind: 'error', title: '清理失败', note: message });
      });
    })
    .catch((error) => {
      clearProgress();
      setBusy(false);
      showResult({ kind: 'error', title: '清理失败', note: error.message });
    });
}

function noteForDelete(result) {
  const parts = [];
  if ((result.skipped_subscribed || []).length) {
    parts.push(`${result.skipped_subscribed.length} 项在扫描之后又被订阅了，已跳过`);
  }
  if ((result.skipped_fresh || []).length) {
    parts.push(`${result.skipped_fresh.length} 项是刚下载的，已跳过`);
  }
  if ((result.skipped_resubscribed || []).length) {
    parts.push(`${result.skipped_resubscribed.length} 项刚重新订阅过，已跳过`);
  }
  const failed = result.failed || [];
  if (failed.length) {
    parts.push(`失败原因：${failed.slice(0, 3).map((row) => `${row.wid}（${row.error}）`).join('；')}`);
  }
  return parts.join('。');
}

/* ==========================================================================
   取消订阅
   ========================================================================== */

export function openUnsubConfirm() {
  const items = chosenSubscribed();
  if (!items.length) return;
  if (!requireSteam(items.length === 1 ? items[0].wid : null)) return;

  setText($('unsub-count'), String(items.length));
  fillPickList($('unsub-list'), items, (item) => fmtSize(diskBytes(item)));

  // 每次打开都回到最保守的默认值：上一次选了"并删除文件"不该被记住
  $('mode-only').checked = true;
  const recycle = $('opt-unsub-recycle');
  if (recycle) recycle.checked = state.recycleSupported;
  syncUnsubMode();

  openDialog('dlg-unsub');
}

export function closeUnsubConfirm() {
  closeDialog('dlg-unsub');
}

export function syncUnsubMode() {
  const withDelete = $('mode-delete').checked;
  setHidden($('unsub-delete-options'), !withDelete);
  setHidden($('unsub-permanent-warning'), !state.recycleSupported || !withDelete);
}

export function doUnsubscribe() {
  const items = chosenSubscribed();
  if (!items.length) return;
  const wids = items.map((item) => item.wid);
  const mode = $('mode-delete').checked ? 'with_delete' : 'only';
  const recycle = !!(state.recycleSupported && $('opt-unsub-recycle').checked);

  closeUnsubConfirm();
  setBusy(true);
  hideResult();
  scheduleDialog('正在取消订阅');

  api('/api/unsubscribe', { body: { wids, mode, recycle, scan_id: scanId() } })
    .then(({ job_id }) => {
      pollJob(job_id, async (job) => {
        const result = job.result || {};
        // 只有"并删除文件"模式才有文件消失，也才需要让行淡出
        const deleted = new Set((result.deleted || []).map((row) => row.wid));
        if (deleted.size) await waitForLeave(deleted);
        await loadState();

        const unsubscribed = (result.unsubscribed || []).length;
        const unconfirmed = (result.unconfirmed || []).length;
        const failed = (result.failed || []).length;
        showResult({
          kind: failed ? 'warn' : 'ok',
          title: `已取消订阅 ${unsubscribed} 项`,
          summary: [
            summaryRow('已删除文件', (result.deleted || []).length),
            summaryRow('释放空间', result.freed_bytes ? fmtSize(result.freed_bytes) : ''),
            summaryRow('待 Steam 确认', unconfirmed),
            summaryRow('失败', failed),
          ].filter(Boolean),
          note: unconfirmed
            ? '待确认的项目会在 Steam 同步完成后从「已订阅」里消失。'
            : '',
          actions: [{ label: '重新扫描', action: 'rescan' }],
        });
        // 这里刻意不自动重扫：Steam 的取消订阅要几秒到几十秒才落库，
        // 立刻扫会扫到"还没取消"的旧状态，反而让人以为操作失败了
      }, null, (message) => {
        if (/steam/i.test(message)) handleSteamFailure(message);
        else showResult({ kind: 'error', title: '取消订阅失败', note: message });
      });
    })
    .catch((error) => {
      clearProgress();
      setBusy(false);
      if (/steam/i.test(error.message)) handleSteamFailure(error.message);
      else showResult({ kind: 'error', title: '取消订阅失败', note: error.message });
    });
}

/* ==========================================================================
   重新订阅
   ========================================================================== */

export function openResubConfirm() {
  const items = resubTargets();
  if (!items.length) return;
  if (!requireSteam(items.length === 1 ? items[0].wid : null)) return;

  setText($('resub-count'), String(items.length));
  fillPickList($('resub-list'), items, (item) => (
    item.declared_bytes ? `重新下载约 ${fmtSize(item.declared_bytes)}` : '下载大小未知'
  ));

  openDialog('dlg-resub');
}

export function closeResubConfirm() {
  closeDialog('dlg-resub');
}

export function doResubscribe() {
  const items = resubTargets();
  if (!items.length) return;
  const wids = items.map((item) => item.wid);

  closeResubConfirm();
  setBusy(true);
  hideResult();
  scheduleDialog('正在重新订阅');

  api('/api/resubscribe', { body: { wids, scan_id: scanId() } })
    .then(({ job_id }) => {
      pollJob(job_id, async (job) => {
        const result = job.result || {};
        await loadState();

        const resubscribed = (result.resubscribed || []).length;
        const unconfirmed = (result.unconfirmed || []).length;
        const failed = (result.failed || []).length;
        showResult({
          kind: failed ? 'warn' : 'ok',
          title: `已重新订阅 ${resubscribed} 项`,
          summary: [
            summaryRow('待 Steam 确认', unconfirmed),
            summaryRow('失败', failed),
          ].filter(Boolean),
          note: 'Steam 会在后台把这些壁纸重新下载到磁盘上。',
          actions: [{ label: '重新扫描', action: 'rescan' }],
        });
      }, null, (message) => {
        if (/steam/i.test(message)) handleSteamFailure(message);
        else showResult({ kind: 'error', title: '重新订阅失败', note: message });
      });
    })
    .catch((error) => {
      clearProgress();
      setBusy(false);
      if (/steam/i.test(error.message)) handleSteamFailure(error.message);
      else showResult({ kind: 'error', title: '重新订阅失败', note: error.message });
    });
}

/* ==========================================================================
   接线
   ========================================================================== */

export function initActions() {
  const bind = (id, fn) => {
    const el = $(id);
    if (el) el.addEventListener('click', fn);
  };

  bind('btn-delete', openConfirm);
  bind('btn-cancel-delete', closeConfirm);
  bind('btn-confirm-delete', doDelete);

  const recycle = $('opt-recycle');
  if (recycle) {
    recycle.addEventListener('change', () => {
      setHidden($('permanent-warning'), recycle.checked);
    });
  }

  bind('btn-unsubscribe', openUnsubConfirm);
  bind('btn-cancel-unsub', closeUnsubConfirm);
  bind('btn-confirm-unsub', doUnsubscribe);
  ['mode-only', 'mode-delete'].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener('change', syncUnsubMode);
  });
  const unsubRecycle = $('opt-unsub-recycle');
  if (unsubRecycle) {
    unsubRecycle.addEventListener('change', () => {
      setHidden($('unsub-permanent-warning'), unsubRecycle.checked);
    });
  }

  bind('btn-resubscribe', openResubConfirm);
  bind('btn-cancel-resub', closeResubConfirm);
  bind('btn-confirm-resub', doResubscribe);
}
