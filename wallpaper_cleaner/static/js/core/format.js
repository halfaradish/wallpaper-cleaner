// 纯格式化函数：不含 DOM，也不读全局状态，所以可以直接推理、单独验证

const HEX = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/[&<>"']/g, (c) => HEX[c]);
}

export function fmtSize(bytes) {
  let n = Number(bytes) || 0;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(2)} ${units[i]}`;
}

// 「磁盘占用」：服务端量出来的实际占用（按簇对齐的估算）；拿不到簇大小时它是 0，
// 这时退回精确的文件字节数，宁可给个偏小的准确值，也不显示 0
export function diskBytes(item) {
  return Number(item.alloc_bytes) || Number(item.size_bytes) || 0;
}

// 「磁盘占用」单元格的悬停说明：带上精确的文件字节数，方便对照两处口径
export function diskHint(item) {
  const exact = `其中文件字节合计 ${fmtSize(item.size_bytes)}`;
  return item.alloc_bytes
    ? `磁盘占用：按卷的簇大小对齐后的实际占用（每个子目录也算一个簇），接近资源管理器的「占用空间」。${exact}`
    : `磁盘占用：拿不到卷的簇大小，退回文件字节数（${exact}）`;
}

export function dirname(path) {
  if (!path) return '';
  const cut = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'));
  return cut > 0 ? path.slice(0, cut) : path;
}

// 取不到标题的行（目录里没有 project.json）显示占位文字而不是空白：
// 内容被 Steam 清理过的残留本来就只剩一串 ID，能不能打开目录不该由标题决定
export const NO_TITLE = ['—', '未知'];
export const NO_TITLE_TEXT = '打开目录';
export const NO_TITLE_HINT = '这个文件夹里没有 project.json（内容可能已被 Steam 清理），点这里打开目录';

// 确认框里逐条列出的名字：这两张表都可能取不到标题，标一下免得只剩一串 ID
export function confirmLabel(item) {
  const name = item.title ? ` · ${esc(item.title)}` : '';
  return `${esc(item.wid)}${name}${item.content_missing ? '（内容已缺失）' : ''}`;
}
