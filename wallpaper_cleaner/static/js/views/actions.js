// 三个破坏性操作：清理残留、取消订阅、重新订阅
//
// 共同点：都要先弹确认框逐条列出将要动的东西，再提交任务并轮询，最后把结果讲清楚。
// 结果里的「跳过」要按原因分开说——被订阅状态拦下的、目录刚改动过的、刚重新订阅过的，
// 用户能采取的行动完全不同，混成一个数字等于没说。
import { $, show, hide, setText } from '../core/dom.js';
import { esc, fmtSize, diskBytes, confirmLabel } from '../core/format.js';
import { api } from '../core/api.js';
import { state, loadState, setBusy } from '../core/store.js';
import { pollJob } from '../core/job.js';
import { scheduleProgressOverlay, clearProgress } from '../ui/progress.js';
import { toast } from '../ui/toast.js';
import { registerOverlay, openOverlay, closeOverlay } from '../ui/overlay.js';
import { showResult } from '../ui/result.js';
import { chosenOrphans, resubTargets } from './orphans.js';
import { chosenSubscribed } from './subscribed.js';
import { startScan } from './scan.js';
import { requireSteam, handleSteamFailure } from './steam.js';

// 确认框里最多逐条列几个：再多就折叠成一句「另有 N 个」
const MAX_LISTED = 40;

function listRows(items, labelOf, moreText) {
  const shown = items.slice(0, MAX_LISTED);
  const rows = shown.map((i) => `<li><span>${labelOf(i)}</span><span>${fmtSize(diskBytes(i))}</span></li>`);
  if (items.length > shown.length) {
    rows.push(`<li class="more">…… 另有 ${items.length - shown.length} 个${moreText}</li>`);
  }
  return rows.join('');
}

/* ---------------- 清理 ---------------- */

export function openConfirm() {
  const chosen = chosenOrphans();
  if (!chosen.length) return;

  const bytes = chosen.reduce((sum, i) => sum + diskBytes(i), 0);
  setText($('confirm-count'), String(chosen.length));
  setText($('confirm-size'), fmtSize(bytes));

  // 删除前的最后一眼，带上标题才认得出是什么
  $('confirm-list').innerHTML = listRows(chosen, (i) => {
    const note = i.kind === 'unknown' ? '（无法确定的文件夹）' : '';
    return `${confirmLabel(i)}${note}`;
  }, '文件夹');

  const recycleRow = $('recycle-row');
  if (state.recycleSupported) {
    show(recycleRow);
    $('opt-recycle').checked = true;
    hide($('permanent-warning'));
  } else {
    hide(recycleRow);
    show($('permanent-warning'));
  }

  openOverlay('confirm-overlay');
}

export function closeConfirm() {
  closeOverlay('confirm-overlay');
}

export async function doDelete() {
  const chosen = chosenOrphans();
  if (!chosen.length) return;

  const recycle = state.recycleSupported && $('opt-recycle').checked;
  closeConfirm();

  if (state.busy) return;
  setBusy(true);
  scheduleProgressOverlay(recycle ? '正在移入回收站' : '正在删除');

  let jobId;
  try {
    const data = await api('/api/delete', {
      body: {
        wids: chosen.map((i) => i.wid),
        scan_id: state.scan.scanned_at,
        recycle,
      },
    });
    jobId = data.job_id;
  } catch (e) {
    clearProgress();
    setBusy(false);
    toast(e.message, 'error');
    return;
  }

  pollJob(jobId, async (job) => {
    const result = job.result || {};
    const deleted = (result.deleted || []).length;
    const failed = (result.failed || []).length;
    const skipped = (result.skipped || []).length;
    const skippedSub = (result.skipped_subscribed || []).length;
    const skippedFresh = (result.skipped_fresh || []).length;
    const skippedResub = (result.skipped_resubscribed || []).length;
    state.selected.clear();
    await loadState();

    // 逐条说清"为什么没删"，而不是压成一个数字：三种跳过的原因对应三种不同的
    // 下一步动作（等 Steam 同步 / 稍后重试 / 什么都不用做）
    const details = [];
    if (skippedSub) details.push(`${skippedSub} 个仍在订阅，没有动它们`);
    if (skippedFresh) details.push(`${skippedFresh} 个目录刚改动过，过一会儿重试即可`);
    if (skippedResub) details.push(`${skippedResub} 个刚重新订阅，本地记录还没刷新`);
    if (failed) details.push(`${failed} 个删除失败`);

    showResult({
      kind: failed ? 'error' : (skipped ? 'warn' : 'ok'),
      title: `已清理 ${deleted} 项，释放 ${fmtSize(result.freed_bytes || 0)}`,
      details,
      note: recycle
        ? '文件在回收站里，误删可以从那里恢复。'
        : '这些文件已被永久删除，无法恢复。',
      actions: [{ action: 'rescan', label: '重新扫描' }],
    });
  }, () => {
    // 清理后重扫一遍，保持面板与实际磁盘一致
    startScan({ silent: true, auto: true });
  });
}

/* ---------------- 取消订阅 ---------------- */

export function openUnsubConfirm() {
  const chosen = chosenSubscribed();
  if (!chosen.length) return;

  const bytes = chosen.reduce((sum, i) => sum + diskBytes(i), 0);
  setText($('unsub-count'), String(chosen.length));
  setText($('unsub-size'), fmtSize(bytes));

  $('unsub-list').innerHTML = listRows(chosen, confirmLabel, '张壁纸');

  // 每次都回到最保守的默认：仅取消订阅、删除走回收站
  $('mode-only').checked = true;
  $('opt-unsub-recycle').checked = true;
  syncUnsubMode();
  openOverlay('unsub-overlay');
}

export function closeUnsubConfirm() {
  closeOverlay('unsub-overlay');
}

export function syncUnsubMode() {
  const withDelete = $('mode-delete').checked;
  $('unsub-delete-options').classList.toggle('hidden', !withDelete);
  const recycleOn = state.recycleSupported && $('opt-unsub-recycle').checked;
  $('unsub-recycle-row').classList.toggle('hidden', !state.recycleSupported);
  // 只有"要删且不走回收站"才需要警告
  $('unsub-permanent-warning').classList.toggle('hidden', !withDelete || recycleOn);
}

export async function doUnsubscribe() {
  const chosen = chosenSubscribed();
  if (!chosen.length) return;

  const mode = $('mode-delete').checked ? 'with_delete' : 'only';
  const recycle = state.recycleSupported && $('opt-unsub-recycle').checked;
  closeUnsubConfirm();
  if (state.busy) return;

  // 连不上 Steam 时不报错，先把"为什么 + 怎么办"讲清楚
  if (!requireSteam(chosen.length === 1 ? chosen[0].wid : '')) return;

  setBusy(true);
  scheduleProgressOverlay(mode === 'with_delete' ? '正在取消订阅并清理文件' : '正在取消订阅');

  let jobId;
  try {
    const data = await api('/api/unsubscribe', {
      body: {
        wids: chosen.map((i) => i.wid),
        scan_id: state.scan.scanned_at,
        mode,
        recycle,
      },
    });
    jobId = data.job_id;
  } catch (e) {
    clearProgress();
    setBusy(false);
    toast(e.message, 'error');
    return;
  }

  pollJob(jobId, async (job) => {
    const result = job.result || {};
    const done = (result.unsubscribed || []).length;
    const unconfirmed = (result.unconfirmed || []).length;
    const failed = (result.failed || []).length;
    const skipped = (result.skipped || []).length;
    const deleted = (result.deleted || []).length;
    const deleteFailed = (result.delete_failed || []).length;
    state.subSelected.clear();
    await loadState();

    const details = [];
    if (mode === 'with_delete' && done) {
      if (deleted) details.push(`${deleted} 个目录已删除，释放 ${fmtSize(result.freed_bytes || 0)}`);
      if (result.already_gone) details.push(`另有 ${result.already_gone} 张的内容已由 Steam 删除`);
      if (deleteFailed) details.push(`${deleteFailed} 个目录没删掉，重新扫描后可以再清理一次`);
    }
    if (unconfirmed) details.push(`${unconfirmed} 张已提交但未确认生效，本地文件没有被动`);
    if (skipped) details.push(`${skipped} 张在 Steam 里已经不在订阅列表`);
    if (failed) details.push(`${failed} 张取消订阅失败`);

    showResult({
      kind: failed || deleteFailed ? 'error' : (unconfirmed || skipped ? 'warn' : 'ok'),
      title: done ? `已取消订阅 ${done} 张` : '没有壁纸被取消订阅',
      details,
      // 不自动重扫的原因写在这里：用户看不到列表变化会以为操作没生效
      note: 'Steam 改订阅和本地记录刷新之间有时间差，所以这里不自动重新扫描——'
        + '否则刚取消的壁纸会立刻又显示成「已订阅」。过一会儿点「重新扫描」即可看到最新状态。',
      actions: [{ action: 'rescan', label: '重新扫描' }],
    });
  }, null, handleSteamFailure);
}

/* ---------------- 重新订阅 ---------------- */

export function openResubConfirm() {
  const targets = resubTargets();
  if (!targets.length) {
    if (chosenOrphans().length) {
      toast('选中的都是「无法确定的文件夹」，它们没有可用的 workshop ID，无法重新订阅', 'error');
    }
    return;
  }

  setText($('resub-count'), String(targets.length));
  // 重新订阅会让 Steam 把内容重新下载一遍，所以这里摆出"要下多少"：数字来自
  // Steam/WE 记录的标注大小。被 Steam 删掉内容记录的残留查不到，只能标未知
  const known = targets.filter((i) => Number(i.declared_bytes) > 0);
  const download = known.reduce((sum, i) => sum + Number(i.declared_bytes), 0);
  const shown = targets.slice(0, MAX_LISTED);
  const rows = shown.map((i) => {
    const label = Number(i.declared_bytes) > 0
      ? `重新下载约 ${fmtSize(i.declared_bytes)}`
      : '下载大小未知';
    return `<li><span>${confirmLabel(i)}</span><span
      title="当前磁盘占用 ${esc(fmtSize(diskBytes(i)))}">${esc(label)}</span></li>`;
  });
  if (targets.length > shown.length) {
    rows.push(`<li class="more">…… 另有 ${targets.length - shown.length} 张壁纸</li>`);
  }
  if (known.length) {
    const unknown = targets.length - known.length;
    rows.push(`<li class="more">重新下载合计约 ${fmtSize(download)}`
      + `${unknown ? `（另有 ${unknown} 张没有记录，未计入）` : ''}</li>`);
  }
  $('resub-list').innerHTML = rows.join('');
  openOverlay('resub-overlay');
}

export function closeResubConfirm() {
  closeOverlay('resub-overlay');
}

export async function doResubscribe() {
  const targets = resubTargets();
  closeResubConfirm();
  if (!targets.length || state.busy) return;
  if (!requireSteam(targets.length === 1 ? targets[0].wid : '')) return;

  setBusy(true);
  scheduleProgressOverlay('正在重新订阅');

  let jobId;
  try {
    const data = await api('/api/resubscribe', {
      body: { wids: targets.map((i) => i.wid), scan_id: state.scan.scanned_at },
    });
    jobId = data.job_id;
  } catch (e) {
    clearProgress();
    setBusy(false);
    toast(e.message, 'error');
    return;
  }

  pollJob(jobId, async (job) => {
    const result = job.result || {};
    const done = (result.resubscribed || []).length;
    const unconfirmed = (result.unconfirmed || []).length;
    const failed = (result.failed || []).length;
    const skipped = (result.skipped || []).length;
    const chosen = targets.length;
    await loadState();

    const details = [];
    if (unconfirmed) details.push(`${unconfirmed} 张已提交但未确认生效`);
    if (skipped) details.push(`${skipped} 张已经在订阅列表里`);
    if (failed) details.push(`${failed} 张重新订阅失败`);
    if (done && done < chosen) details.push(`${chosen - done} 张没能提交成功`);

    showResult({
      kind: failed ? 'error' : (unconfirmed || skipped ? 'warn' : 'ok'),
      title: done ? `已重新订阅 ${done} 张，Steam 正在后台下载` : '没有壁纸被重新订阅',
      details,
      note: '重新订阅成功后已经把它们从勾选里移除，避免误删。'
        + '想确认下载进度，可以到 Steam 客户端里看，或过一会儿重新扫描。',
      actions: [{ action: 'rescan', label: '重新扫描' }],
    });
  }, null, handleSteamFailure);
}

export function initActions() {
  registerOverlay('confirm-overlay', { close: closeConfirm });
  registerOverlay('unsub-overlay', { close: closeUnsubConfirm });
  registerOverlay('resub-overlay', { close: closeResubConfirm });
}
