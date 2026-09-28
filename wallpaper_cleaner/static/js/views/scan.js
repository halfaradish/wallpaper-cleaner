// 扫描：打开面板时自动跑一次，也是所有数据更新的唯一入口
//
// 扫描是非破坏性的、而且很常发生（每次打开面板都会跑），所以它走状态栏那条细进度线，
// 不弹模态。只有破坏性操作才配得上挡住整个界面。
import { api } from '../core/api.js';
import { state, setBusy, loadState } from '../core/store.js';
import { pollJob, waitUntilIdle } from '../core/job.js';
import { clearProgress, scheduleStatus } from '../ui/progress.js';
import { clearSkeleton, scheduleSkeleton } from '../ui/skeleton.js';
import { toast } from '../ui/toast.js';
import { selectAllOrphans } from './orphans.js';
import { renderNotice } from './shell.js';

// 一分钟内扫过的结果直接复用：频繁切窗口、刷新页面时没必要反复读盘
export const SCAN_REUSE_MS = 60000;

export function scanAgeMs(scan) {
  if (!scan || !scan.scanned_at) return Infinity;
  // 后端给的是本地时间字符串（YYYY-MM-DD HH:MM:SS），没有时区信息，
  // 用同样的格式解析才能得到正确的差值
  const parsed = Date.parse(scan.scanned_at.replace(' ', 'T'));
  return Number.isNaN(parsed) ? Infinity : Date.now() - parsed;
}

export async function startScan(options) {
  const opts = options || {};
  if (state.busy) return;

  // 骨架屏由两个视图自己在忙碌态里画：它们才知道自己现在有没有数据，
  // 而"已经有数据"时再叠一层骨架屏是退步
  setBusy(true);
  scheduleStatus('正在检查壁纸文件夹');

  let jobId;
  try {
    const data = await api('/api/scan', { body: {} });
    jobId = data.job_id;
  } catch (e) {
    clearSkeleton('orphan-body');
    clearSkeleton('sub-body');
    clearProgress();
    setBusy(false);
    // 提交失败最常见的原因是另一个窗口正在扫（后端同一时刻只跑一个任务）。
    // 自动扫描遇到这种情况不该报错，等它扫完直接读结果就好
    if (opts.auto) {
      await waitUntilIdle();
      try {
        await loadState();
        selectAllOrphans();
      } catch (ignored) { /* 连不上时由连接横幅负责说明 */ }
      return;
    }
    toast(e.message, 'error');
    return;
  }

  pollJob(jobId, async (job) => {
    clearSkeleton('orphan-body');
    clearSkeleton('sub-body');
    await loadState();
    const result = job.result || {};
    selectAllOrphans();
    if (!opts.silent) {
      const cleanable = (result.orphans || []).length + (result.unknown || []).length;
      toast(
        cleanable
          ? `扫描完成：${result.total_folders || 0} 个文件夹，其中 ${cleanable} 项待清理`
          : `扫描完成：${result.total_folders || 0} 个文件夹，没有待清理的残留`,
        'ok',
      );
    }
  }, () => {
    renderNotice();
  }, (message) => {
    clearSkeleton('orphan-body');
    clearSkeleton('sub-body');
    toast(message, 'error');
  });
}
