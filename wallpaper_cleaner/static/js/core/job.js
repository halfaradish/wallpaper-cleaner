// 任务引擎：长操作（扫描 / 清理 / 取消订阅 / 重新订阅）的统一轮询
//
// 后端同一时刻只跑一个任务，提交后拿 job_id，这里按 400ms 轮询它的进度。
// 进度弹窗延迟 400ms 才打开（见 ui/progress.js）：任务很快时不要闪一下。
import { api } from './api.js';
import { loadState, setBusy } from './store.js';
import { clearTimer, setRepeating } from './poll.js';
import { renderJob, clearProgress } from '../ui/progress.js';
import { toast } from '../ui/toast.js';

export function stopPolling() {
  clearTimer('job');
}

/**
 * onDone(job)     任务成功后的收尾（刷新状态、呈现结果）
 * onSettled(job)  等忙碌标记清掉之后才跑：它触发的任务会被 startScan 开头的
 *                 if (state.busy) return 直接吞掉，所以顺序不能提前
 * onError(message) 任务失败时的呈现方式（Steam 相关的失败要弹引导弹窗，而不是一句 toast）
 */
export function pollJob(jobId, onDone, onSettled, onError) {
  stopPolling();
  setRepeating('job', async () => {
    let job;
    try {
      job = await api(`/api/job/${encodeURIComponent(jobId)}`);
    } catch (e) {
      stopPolling();
      clearProgress();
      setBusy(false);
      toast(e.message, 'error');
      return;
    }

    renderJob(job);
    if (job.status === 'running') return;

    stopPolling();
    clearProgress();

    if (job.status === 'error') {
      setBusy(false);
      const message = job.error || '任务执行失败';
      if (onError) onError(message); else toast(message, 'error');
      loadState().catch(() => {});
      return;
    }

    try {
      if (onDone) await onDone(job);
    } finally {
      setBusy(false);
    }
    if (onSettled) onSettled(job);
  }, 400);
}

// 另一个窗口正在扫描时，等它结束再读结果，别抢同一个任务槽
export async function waitUntilIdle(timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 120000);
  while (Date.now() < deadline) {
    let data;
    try {
      data = await api('/api/state');
    } catch (e) {
      return;
    }
    if (!data.active_job) return;
    // 这里是一次性的本地等待，不进具名定时器表：那个表管的是跨调用生命周期，
    // 放进去反而会被别处的清理误伤
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
