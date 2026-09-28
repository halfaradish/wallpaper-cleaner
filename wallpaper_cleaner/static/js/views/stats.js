// 概览的四个数字
import { $, setText } from '../core/dom.js';
import { fmtSize } from '../core/format.js';
import { state, onRender } from '../core/store.js';

const IDS = ['stat-subscribed', 'stat-folders', 'stat-orphans', 'stat-freed'];

export function renderStats() {
  const scan = state.scan;
  const bar = $('stat-bar');
  if (!scan) {
    IDS.forEach((id) => setText($(id), '—'));
    setText($('stat-missing'), '');
    setText($('stat-unknown'), '');
    setText($('stat-scanned-at'), '');
    if (bar) bar.classList.add('hidden');
    return;
  }

  // 「待清理」在两个位置必须一致，所以这里数的是孤儿 + 无法确定的，
  // 与待清理卡片上的徽标同源；主按钮则精确显示"这次会清理几个"
  const cleanable = scan.orphans.length + scan.unknown.length;

  setText($('stat-subscribed'), String(scan.subscribed.length));
  setText($('stat-folders'), String(scan.total_folders));
  setText($('stat-orphans'), String(cleanable));
  // 「可释放空间」按磁盘占用算：删掉后真正腾出来的就是这些簇。
  // 和待清理列表一样把两类目录都算上（以前漏了「无法确定的文件夹」）
  const freed = (Number(scan.orphan_usage_bytes) || Number(scan.orphan_bytes) || 0)
    + (Number(scan.unknown_usage_bytes) || Number(scan.unknown_bytes) || 0);
  setText($('stat-freed'), fmtSize(freed));
  setText($('stat-missing'), scan.missing.length
    ? `${scan.missing.length} 张已订阅的壁纸还没下载到本地`
    : '');
  setText($('stat-unknown'), scan.unknown.length
    ? `含 ${scan.unknown.length} 个无法确定的文件夹`
    : '');
  setText($('stat-scanned-at'), `扫描于 ${scan.scanned_at}`);

  // 占比条：让"4 个"有个分母。总数拿不到时（分母为 0）就把这条藏起来，
  // 宁可没有也不画一条没有意义的满格/空格。
  if (bar) {
    const total = Number(scan.total_folders) || 0;
    if (!total) {
      bar.classList.add('hidden');
    } else {
      bar.classList.remove('hidden');
      const ratio = Math.min(1, cleanable / total);
      $('stat-bar-fill').style.transform = `scaleX(${ratio})`;
      // 全是残留时用警告色，干净时用绿色——颜色只是补充，数字本身已经说明了
      bar.classList.toggle('empty', cleanable === 0);
    }
  }
}

export function initStats() {
  onRender(renderStats);
}
