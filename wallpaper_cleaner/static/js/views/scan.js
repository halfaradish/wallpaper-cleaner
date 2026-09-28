// 扫描：打开面板自动跑一次，也可以手动点「重新扫描」
import { api } from '../core/api.js';
import { state, loadState, setBusy } from '../core/store.js';
import { pollJob, waitUntilIdle } from '../core/job.js';
import { scheduleTaskbar, clearProgress } from '../ui/progress.js';
import { scheduleSkeleton, clearSkeleton } from '../ui/skeleton.js';
import { fmtSize } from '../core/format.js';
import { toast } from '../ui/toast.js';
import { selectAllOrphans } from './orphans.js';

export const SCAN_REUSE_MS = 60000; // 服务端的扫描结果超过这个时间就重新扫，别拿几分钟前的状态糊弄人

export function scanAgeMs(scan) {
  // scanned_at 是本地时间的 'YYYY-MM-DD HH:MM:SS'，解析不了就当过期处理
  if (!scan || !scan.scanned_at) return Infinity;
  const parsed = Date.parse(String(scan.scanned_at).replace(' ', 'T'));
  return Number.isNaN(parsed) ? Infinity : Date.now() - parsed;
}

export async function startScan(options) {
  const opts = options || {};
  if (state.busy) return;
  setBusy(true);

  // 扫描走顶栏内联条而不是全屏模态：打开面板会自动扫一遍，每次都挡住整页太重。
  // 扫得快就不显示，免得进度条闪一下反而让人以为出错。
  scheduleTaskbar('正在检查壁纸文件夹');
  // 还没有数据时先把表格的形状铺出来（延迟 300ms，扫得快就看不到）
  scheduleSkeleton();

  let jobId;
  try {
    const data = await api('/api/scan', { body: {} });
    jobId = data.job_id;
  } catch (e) {
    clearProgress();
    clearSkeleton();
    if (opts.auto) {
      // 多半是另一个窗口正在扫描：等它结束，直接读它的结果
      await waitUntilIdle();
      await loadState();
      selectAllOrphans();
      setBusy(false);
      return;
    }
    setBusy(false);
    toast(e.message, 'error');
    return;
  }

  pollJob(jobId, async (job) => {
    // 数据马上到位，撤掉骨架并清掉待铺的定时器
    clearSkeleton();
    await loadState();
    // 自动检查完就把可清理的选好，用户不必自己去勾
    selectAllOrphans();

    if (opts.silent) return;
    const result = job.result;
    if (!result) return;
    const cleanable = result.orphans.length + result.unknown.length;
    const freed = (Number(result.orphan_usage_bytes) || Number(result.orphan_bytes) || 0)
      + (Number(result.unknown_usage_bytes) || Number(result.unknown_bytes) || 0);
    toast(
      cleanable
        ? `扫描完成：待清理 ${cleanable} 个，可释放 ${fmtSize(freed)}`
        : '扫描完成：没有需要清理的内容',
      cleanable ? '' : 'ok',
    );
  });
}
