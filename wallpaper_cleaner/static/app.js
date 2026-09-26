'use strict';

const TOKEN = window.__PANEL_TOKEN__;
const OVERLAY_DELAY = 400;   // 任务很快时不要闪一下进度弹窗
const LOG_REFRESH_MS = 2000;
const SCAN_REUSE_MS = 60000; // 服务端的扫描结果超过这个时间就重新扫，别拿几分钟前的状态糊弄人

const state = {
  paths: null,
  scan: null,
  logFile: null,
  recycleSupported: true,
  selected: new Set(),
  subscribedOpen: false,
  subFilter: '',
  sortOrphans: 'default',
  sortSub: 'default',
  busy: false,
  jobTimer: null,
  logTimer: null,
  overlayTimer: null,
};

const $ = (id) => document.getElementById(id);
const HEX = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/[&<>"']/g, (c) => HEX[c]);
}

function fmtSize(bytes) {
  let n = Number(bytes) || 0;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(2)} ${units[i]}`;
}

function dirname(path) {
  if (!path) return '';
  const cut = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'));
  return cut > 0 ? path.slice(0, cut) : path;
}

async function api(path, options) {
  const opts = Object.assign({}, options);
  if (opts.body !== undefined) {
    opts.method = opts.method || 'POST';
    opts.headers = Object.assign({}, opts.headers, {
      'Content-Type': 'application/json',
      'X-Panel-Token': TOKEN,
    });
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const message = (data && data.error) || `请求失败（HTTP ${res.status}）`;
    const err = new Error(message);
    err.payload = data;
    throw err;
  }
  return data;
}

let toastTimer = null;

function toast(message, kind) {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast ${kind || ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast hidden'; }, kind === 'error' ? 8000 : 4000);
}

/* ---------------- 顶栏状态 ---------------- */

function renderConfigLine() {
  const paths = state.paths;
  const el = $('config-line');
  if (!paths) return;

  if (paths.source === 'none') {
    el.textContent = '还没找到 Wallpaper Engine，请在「高级」里设置位置';
    el.classList.add('warn');
    return;
  }

  el.classList.remove('warn');
  const count = state.scan ? state.scan.subscribed.length : null;
  el.textContent = count === null
    ? '已找到 Wallpaper Engine'
    : `已找到 Wallpaper Engine · ${count} 张壁纸已订阅`;
}

/* ---------------- 提示条 ---------------- */

function renderNotice() {
  const el = $('notice');
  const paths = state.paths;
  const scan = state.scan;

  if (paths && paths.source === 'none') {
    el.className = 'notice warn';
    el.innerHTML = `<span>${esc(paths.hint || '还没设置 Wallpaper Engine 的位置')}</span>
      <span class="notice-actions"><button class="btn ghost" id="notice-advanced">打开高级</button></span>`;
    $('notice-advanced').addEventListener('click', () => openDrawer('advanced-drawer'));
    return;
  }

  if (scan && scan.orphans.length === 0 && scan.unknown.length === 0) {
    // 只有"已订阅但还没下载"时不能说成完全一致，那句话会让人以为本地就是全部
    const missing = scan.missing ? scan.missing.length : 0;
    el.className = missing ? 'notice' : 'notice ok';
    el.innerHTML = missing
      ? `<span>没有需要清理的内容。另有 ${missing} 张已订阅的壁纸还没下载到本地。</span>`
      : '<span>很干净，没有需要清理的内容。磁盘上的文件夹和订阅列表完全一致。</span>';
    return;
  }

  if (!scan) {
    el.className = 'notice';
    el.innerHTML = '<span>正在检查壁纸文件夹…</span>';
    return;
  }

  el.className = 'notice hidden';
  el.innerHTML = '';
}

/* ---------------- 概览数字 ---------------- */

function cleanupItems() {
  const scan = state.scan;
  if (!scan) return [];
  return scan.orphans.concat(scan.unknown);
}

function renderStats() {
  const scan = state.scan;
  if (!scan) {
    ['stat-subscribed', 'stat-folders', 'stat-orphans', 'stat-freed'].forEach((id) => {
      $(id).textContent = '—';
    });
    $('stat-missing').textContent = '';
    $('stat-unknown').textContent = '';
    $('stat-scanned-at').textContent = '';
    return;
  }

  // 「待清理」在两个位置必须一致，所以这里数的是孤儿 + 无法确定的，
  // 与待清理卡片上的徽标同源；主按钮则精确显示"这次会清理几个"
  const cleanable = scan.orphans.length + scan.unknown.length;

  $('stat-subscribed').textContent = String(scan.subscribed.length);
  $('stat-folders').textContent = String(scan.total_folders);
  $('stat-orphans').textContent = String(cleanable);
  $('stat-freed').textContent = fmtSize(scan.orphan_bytes);
  $('stat-missing').textContent = scan.missing.length
    ? `${scan.missing.length} 张已订阅的壁纸还没下载到本地`
    : '';
  $('stat-unknown').textContent = scan.unknown.length
    ? `含 ${scan.unknown.length} 个无法确定的文件夹`
    : '';
  $('stat-scanned-at').textContent = `扫描于 ${scan.scanned_at}`;
}

/* ---------------- 缩略图 ---------------- */

// 预览图是作者随内容一起发布的、就躺在壁纸目录里（project.json 的 preview 字段），
// 服务端只把字节发过来，解码、缩放、GIF 播放全交给浏览器
function thumbCell(item) {
  if (!item.preview) {
    const label = item.wp_type ? esc(item.wp_type) : '—';
    return `<td class="col-thumb"><span class="thumb thumb-empty"
      title="这个文件夹里没有预览图">${label}</span></td>`;
  }
  const src = `/api/thumb?wid=${encodeURIComponent(item.wid)}`;
  return `<td class="col-thumb"><img class="thumb" src="${src}" alt=""
    loading="lazy" decoding="async" title="点击放大"></td>`;
}

/* ---------------- 标题（可点开目录） ---------------- */

// 标题文字可点：用系统文件管理器打开这张壁纸的目录。热区只有标题文字本身，
// 不做整行/整格可点——那样会和行内勾选框、行 hover 选中态打架。
// 没有标题的行（待清理表显示 —、已订阅表显示 未知）原样输出纯文本，不给点。
const NO_TITLE = ['—', '未知'];

function titleLink(item, text) {
  if (!text || NO_TITLE.includes(text)) return esc(text || '—');
  return `<span class="title-link" data-wid="${esc(item.wid)}"
    title="${esc(text)}">${esc(text)}</span>`;
}

async function openFolder(wid) {
  try {
    await api('/api/reveal', { body: { wid } });
  } catch (e) {
    toast(`打不开文件夹：${e.message}`, 'error');
  }
}

function openLightbox(src) {
  $('lightbox-img').src = src;
  $('lightbox').classList.remove('hidden');
}

function closeLightbox() {
  const box = $('lightbox');
  if (box.classList.contains('hidden')) return;
  box.classList.add('hidden');
  $('lightbox-img').removeAttribute('src');
}

/* ---------------- 列排序（占用大小） ---------------- */

// 三态循环：默认 → 大到小 → 小到大 → 默认。默认态不干预后端顺序（待清理本来就是
// 大到小、已订阅是 workshop ID 升序），也不显示方向指示，只留一个「这里可以点」的提示。
// 状态放在 state 里而不是 DOM 上：重扫、清理完成、勾选引起的重绘都只读它，天然保持。
const SORT_CYCLE = { default: 'desc', desc: 'asc', asc: 'default' };
const SORT_ARIA = { default: 'none', desc: 'descending', asc: 'ascending' };

function sortItems(items, mode) {
  if (mode !== 'desc' && mode !== 'asc') return items;
  const wantDesc = mode === 'desc';
  return items.slice().sort((a, b) => {
    // diff 是「a 比 b 大多少」：大到小就得让更大的排前面，返回负数把它顶到前面去
    const diff = (Number(a.size_bytes) || 0) - (Number(b.size_bytes) || 0);
    if (diff) return wantDesc ? -diff : diff;
    // 体积相同用 ID 兜底，比较器才是全序：连点排序不会让两行互换位置
    return a.wid < b.wid ? -1 : (a.wid > b.wid ? 1 : 0);
  });
}

function toggleSort(id, mode) {
  const next = SORT_CYCLE[mode] || 'desc';
  const btn = $(id);
  btn.dataset.mode = next;
  btn.closest('th').setAttribute('aria-sort', SORT_ARIA[next]);
  return next;
}

/* ---------------- 待清理列表 ---------------- */

function renderOrphans() {
  const items = sortItems(cleanupItems(), state.sortOrphans);
  const body = $('orphan-body');
  const wrap = $('orphan-wrap');
  const empty = $('orphan-empty');
  const selectAll = $('check-all');

  $('orphan-count').textContent = String(items.length);

  if (!state.scan) {
    wrap.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = '尚未扫描。';
    selectAll.checked = false;
    selectAll.disabled = true;
    updateDeleteButton();
    return;
  }

  if (items.length === 0) {
    wrap.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = '没有待清理的文件夹。';
    selectAll.checked = false;
    selectAll.disabled = true;
    updateDeleteButton();
    return;
  }

  wrap.classList.remove('hidden');
  empty.classList.add('hidden');
  selectAll.disabled = state.busy;

  body.innerHTML = items.map((item) => {
    const checked = state.selected.has(item.wid);
    const kindLabel = item.kind === 'orphan' ? '已取消订阅' : '无法确定的文件夹';
    // 标题来自残留目录自己的 project.json，取不到就显示占位符
    const title = item.title || '';
    const type = item.wp_type ? ` <span class="wp-type">· ${esc(item.wp_type)}</span>` : '';
    return `<tr class="${checked ? 'selected' : ''}">
      <td class="col-check"><input type="checkbox" data-wid="${esc(item.wid)}"${checked ? ' checked' : ''}></td>
      ${thumbCell(item)}
      <td class="wid">${esc(item.wid)}</td>
      <td class="title-cell" title="${esc(title)}">${titleLink(item, title || '—')}${type}</td>
      <td class="col-kind"><span class="badge ${item.kind}">${kindLabel}</span></td>
      <td class="col-size">${fmtSize(item.size_bytes)}</td>
    </tr>`;
  }).join('');

  body.querySelectorAll('input[type="checkbox"]').forEach((box) => {
    box.addEventListener('change', () => {
      const wid = box.dataset.wid;
      if (box.checked) state.selected.add(wid); else state.selected.delete(wid);
      box.closest('tr').classList.toggle('selected', box.checked);
      updateDeleteButton();
      syncSelectAll();
    });
  });

  syncSelectAll();
  updateDeleteButton();
}

function syncSelectAll() {
  const selectAll = $('check-all');
  const orphans = state.scan ? state.scan.orphans : [];
  if (!orphans.length) { selectAll.checked = false; selectAll.indeterminate = false; return; }
  const picked = orphans.filter((o) => state.selected.has(o.wid)).length;
  selectAll.checked = picked === orphans.length;
  selectAll.indeterminate = picked > 0 && picked < orphans.length;
}

function selectAllOrphans() {
  state.selected.clear();
  if (state.scan) state.scan.orphans.forEach((item) => state.selected.add(item.wid));
  renderOrphans();
}

function updateDeleteButton() {
  const chosen = cleanupItems().filter((i) => state.selected.has(i.wid));
  const bytes = chosen.reduce((sum, i) => sum + (Number(i.size_bytes) || 0), 0);
  const btn = $('btn-delete');
  btn.disabled = state.busy || chosen.length === 0;
  btn.textContent = chosen.length
    ? `清理选中 ${chosen.length} 项 · ${fmtSize(bytes)}`
    : '清理选中';
}

/* ---------------- 已订阅列表 ---------------- */

function renderSubscribed() {
  const scan = state.scan;
  const body = $('sub-body');
  const wrap = $('sub-wrap');
  const empty = $('sub-empty');

  if (!scan) {
    $('sub-count').textContent = '0';
    wrap.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = '尚未扫描。';
    return;
  }

  $('sub-count').textContent = String(scan.subscribed.length);
  const filter = state.subFilter.trim().toLowerCase();
  const matched = filter
    ? scan.subscribed.filter((i) =>
        i.wid.toLowerCase().includes(filter) ||
        String(i.title || '').toLowerCase().includes(filter))
    : scan.subscribed;
  // 先筛选后排序：排序作用于当前看得见的这些行
  const items = sortItems(matched, state.sortSub);

  if (items.length === 0) {
    wrap.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = filter ? '没有匹配的壁纸。' : '没有已订阅的壁纸。';
    return;
  }

  wrap.classList.remove('hidden');
  empty.classList.add('hidden');
  body.innerHTML = items.map((item) => `<tr>
    ${thumbCell(item)}
    <td class="wid">${esc(item.wid)}</td>
    <td class="title-cell" title="${esc(item.title)}">${titleLink(item, item.title)}</td>
    <td class="col-size">${esc(item.declared_size)}</td>
    <td class="col-size">${fmtSize(item.size_bytes)}</td>
  </tr>`).join('');
}

/* ---------------- 关于 ---------------- */

function renderAbout() {
  const paths = state.paths;
  const version = window.__PANEL_VERSION__ || '—';
  $('about-version').textContent = `版本：wallpaper-cleaner ${version}`;
  $('about-config').textContent = (paths && paths.config_file)
    ? `配置文件：${paths.config_file}`
    : '配置文件：尚未创建，保存后写入';
  $('about-logs').textContent = state.logFile
    ? `日志目录：${dirname(state.logFile)}`
    : '日志目录：—';
}

function renderAll() {
  renderConfigLine();
  renderNotice();
  renderStats();
  renderOrphans();
  renderSubscribed();
  renderAbout();
}

/* ---------------- 状态加载 ---------------- */

async function loadState() {
  const data = await api('/api/state');
  state.paths = data.paths;
  state.scan = data.scan;
  state.logFile = data.log_file;
  state.recycleSupported = data.recycle_supported;
  if (data.version) window.__PANEL_VERSION__ = data.version;

  // 丢弃已经不在列表里的选中项
  const valid = new Set(cleanupItems().map((i) => i.wid));
  Array.from(state.selected).forEach((wid) => {
    if (!valid.has(wid)) state.selected.delete(wid);
  });

  renderAll();
  fillSettings();
  return data;
}

/* ---------------- 任务 ---------------- */

function setBusy(busy) {
  state.busy = busy;
  $('btn-scan').disabled = busy;
  updateDeleteButton();
  const selectAll = $('check-all');
  selectAll.disabled = busy || !(state.scan && state.scan.orphans.length);
}

function openProgressOverlay(title) {
  $('progress-title').textContent = title;
  $('progress-bar').style.width = '0%';
  $('progress-bar').classList.add('indeterminate');
  $('progress-message').textContent = '准备中…';
  $('progress-count').textContent = '';
  $('progress-log').innerHTML = '';
  $('progress-overlay').classList.remove('hidden');
}

function closeProgressOverlay() {
  $('progress-overlay').classList.add('hidden');
}

function clearOverlayTimer() {
  clearTimeout(state.overlayTimer);
  state.overlayTimer = null;
}

function renderJob(job) {
  const pct = job.total ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
  const bar = $('progress-bar');
  bar.style.width = `${pct}%`;
  bar.classList.toggle('indeterminate', !job.total);

  $('progress-message').textContent = job.message || '';
  $('progress-count').textContent = job.total ? `${job.done} / ${job.total}` : '';

  // debug 是给排查用的（订阅缓存路径、逐个目录算大小），不该出现在普通用户眼前
  const log = $('progress-log');
  log.innerHTML = job.lines
    .filter((line) => line.level !== 'debug')
    .map((line) => `<span class="lv-${esc(line.level)}">[${esc(line.time)}] ${esc(line.text)}</span>`)
    .join('\n');
  log.scrollTop = log.scrollHeight;
}

function stopPolling() {
  clearInterval(state.jobTimer);
  state.jobTimer = null;
}

function pollJob(jobId, onDone, onSettled) {
  stopPolling();
  state.jobTimer = setInterval(async () => {
    let job;
    try {
      job = await api(`/api/job/${encodeURIComponent(jobId)}`);
    } catch (e) {
      stopPolling();
      clearOverlayTimer();
      closeProgressOverlay();
      setBusy(false);
      toast(e.message, 'error');
      return;
    }

    renderJob(job);
    if (job.status === 'running') return;

    stopPolling();
    clearOverlayTimer();
    closeProgressOverlay();

    if (job.status === 'error') {
      setBusy(false);
      toast(job.error || '任务执行失败', 'error');
      loadState().catch(() => {});
      return;
    }

    try {
      if (onDone) await onDone(job);
    } finally {
      setBusy(false);
    }
    // onSettled 必须等忙碌标记清掉之后再跑，否则它触发的任务会被
    // startScan 开头的 if (state.busy) return 直接吞掉
    if (onSettled) onSettled(job);
  }, 400);
}

async function waitUntilIdle(timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 120000);
  while (Date.now() < deadline) {
    let data;
    try {
      data = await api('/api/state');
    } catch (e) {
      return;
    }
    if (!data.active_job) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

async function startScan(options) {
  const opts = options || {};
  if (state.busy) return;
  setBusy(true);

  if (!opts.silent) {
    // 扫得快就不弹窗，免得进度条闪一下反而让人以为出错
    state.overlayTimer = setTimeout(() => openProgressOverlay('正在检查壁纸文件夹'), OVERLAY_DELAY);
  }

  let jobId;
  try {
    const data = await api('/api/scan', { body: {} });
    jobId = data.job_id;
  } catch (e) {
    clearOverlayTimer();
    closeProgressOverlay();
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
    await loadState();
    // 自动检查完就把可清理的选好，用户不必自己去勾
    selectAllOrphans();

    if (opts.silent) return;
    const result = job.result;
    if (!result) return;
    const cleanable = result.orphans.length + result.unknown.length;
    toast(
      cleanable
        ? `扫描完成：待清理 ${cleanable} 个，可释放 ${fmtSize(result.orphan_bytes)}`
        : '扫描完成：没有需要清理的内容',
      cleanable ? '' : 'ok',
    );
  });
}

/* ---------------- 清理 ---------------- */

function openConfirm() {
  const chosen = cleanupItems().filter((i) => state.selected.has(i.wid));
  if (!chosen.length) return;

  const bytes = chosen.reduce((sum, i) => sum + (Number(i.size_bytes) || 0), 0);
  $('confirm-count').textContent = String(chosen.length);
  $('confirm-size').textContent = fmtSize(bytes);

  const shown = chosen.slice(0, 40);
  const rows = shown.map((i) => {
    // 删除前的最后一眼，带上标题才认得出是什么
    const name = i.title ? ` · ${esc(i.title)}` : '';
    const note = i.kind === 'unknown' ? '（无法确定的文件夹）' : '';
    return `<li><span>${esc(i.wid)}${name}${note}</span><span>${fmtSize(i.size_bytes)}</span></li>`;
  });
  if (chosen.length > shown.length) {
    rows.push(`<li class="more">…… 另有 ${chosen.length - shown.length} 个文件夹</li>`);
  }
  $('confirm-list').innerHTML = rows.join('');

  const recycleRow = $('recycle-row');
  if (state.recycleSupported) {
    recycleRow.classList.remove('hidden');
    $('opt-recycle').checked = true;
    $('permanent-warning').classList.add('hidden');
  } else {
    recycleRow.classList.add('hidden');
    $('permanent-warning').classList.remove('hidden');
  }

  $('confirm-overlay').classList.remove('hidden');
}

function closeConfirm() {
  $('confirm-overlay').classList.add('hidden');
}

async function doDelete() {
  const chosen = cleanupItems().filter((i) => state.selected.has(i.wid));
  if (!chosen.length) return;

  const recycle = state.recycleSupported && $('opt-recycle').checked;
  closeConfirm();

  if (state.busy) return;
  setBusy(true);
  state.overlayTimer = setTimeout(
    () => openProgressOverlay(recycle ? '正在移入回收站' : '正在删除'), OVERLAY_DELAY);

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
    clearOverlayTimer();
    closeProgressOverlay();
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
    state.selected.clear();
    await loadState();

    let message = `已清理 ${deleted} 项，释放 ${fmtSize(result.freed_bytes || 0)}`;
    if (skipped) {
      // 分开说清是被订阅状态拦下的，还是目录刚改动过（后者稍后重试即可）
      const parts = [];
      if (skippedSub) parts.push(`${skippedSub} 个仍在订阅`);
      if (skippedFresh) parts.push(`${skippedFresh} 个目录刚改动过，稍后可重试`);
      message += parts.length ? `，跳过 ${skipped} 个（${parts.join('；')}）` : `，跳过 ${skipped} 个`;
    }
    if (failed) message += `，失败 ${failed} 个`;
    toast(message, failed ? 'error' : 'ok');
  }, () => {
    // 清理后重扫一遍，保持面板与实际磁盘一致
    startScan({ silent: true, auto: true });
  });
}

/* ---------------- 抽屉 ---------------- */

function openDrawer(id) {
  $(id).classList.remove('hidden');
  if (id !== 'advanced-drawer') return;
  // 每次打开都清掉上次的检测提示，避免残留的"检测成功"误导
  clearNotice($('autodetect-result'));
  renderAbout();
  refreshLogs();
  syncLogTimer();
}

function closeDrawer(id) {
  $(id).classList.add('hidden');
  if (id === 'advanced-drawer') stopLogTimer();
}

function clearNotice(el) {
  el.className = 'notice hidden';
  el.textContent = '';
}

function stopLogTimer() {
  clearInterval(state.logTimer);
  state.logTimer = null;
}

function syncLogTimer() {
  stopLogTimer();
  if (!$('opt-log-auto').checked) return;
  if ($('advanced-drawer').classList.contains('hidden')) return;
  state.logTimer = setInterval(refreshLogs, LOG_REFRESH_MS);
}

function fillSettings() {
  const paths = state.paths;
  if (!paths) return;
  if (document.activeElement !== $('input-json')) $('input-json').value = paths.json_path || '';
  if (document.activeElement !== $('input-workshop')) $('input-workshop').value = paths.workshop_dir || '';
  $('settings-meta').textContent = paths.config_file
    ? `配置文件：${paths.config_file}`
    : '配置文件尚未创建，保存后写入配置所在目录的 config.yml';
}

async function refreshLogs() {
  try {
    const data = await api('/api/logs?lines=400');
    state.logFile = data.path || state.logFile;
    $('logs-meta').textContent = data.path || '当前还没有日志文件';
    const body = $('logs-body');
    body.textContent = data.lines && data.lines.length ? data.lines.join('\n') : '（暂无日志）';
    body.scrollTop = body.scrollHeight;
  } catch (e) {
    $('logs-body').textContent = `读取日志失败：${e.message}`;
  }
}

/* ---------------- 事件绑定 ---------------- */

function bind() {
  $('btn-scan').addEventListener('click', () => startScan());
  $('btn-advanced').addEventListener('click', () => openDrawer('advanced-drawer'));

  document.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeDrawer(btn.dataset.close));
  });

  $('btn-delete').addEventListener('click', openConfirm);
  $('btn-cancel-delete').addEventListener('click', closeConfirm);
  $('btn-confirm-delete').addEventListener('click', doDelete);

  $('opt-recycle').addEventListener('change', (e) => {
    $('permanent-warning').classList.toggle('hidden', e.target.checked);
  });

  $('check-all').addEventListener('change', (e) => {
    const orphans = state.scan ? state.scan.orphans : [];
    orphans.forEach((item) => {
      if (e.target.checked) state.selected.add(item.wid);
      else state.selected.delete(item.wid);
    });
    renderOrphans();
  });

  $('toggle-subscribed').addEventListener('click', () => {
    state.subscribedOpen = !state.subscribedOpen;
    $('subscribed-body').classList.toggle('hidden', !state.subscribedOpen);
    $('toggle-subscribed').setAttribute('aria-expanded', String(state.subscribedOpen));
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

  $('btn-autodetect').addEventListener('click', async () => {
    const box = $('autodetect-result');
    box.className = 'notice';
    box.textContent = '正在检测…';
    try {
      const data = await api('/api/autodetect', { body: {} });
      if (data.found) {
        $('input-json').value = data.json_path;
        $('input-workshop').value = data.workshop_dir;
        box.className = 'notice ok';
        box.textContent = '检测成功，已填入下方路径，确认后点击「保存」。';
      } else {
        box.className = 'notice warn';
        box.textContent = data.error || '未能自动检测到路径，请手动填写。';
      }
    } catch (e) {
      box.className = 'notice error';
      box.textContent = e.message;
    }
  });

  $('btn-save-config').addEventListener('click', async () => {
    try {
      const data = await api('/api/config', {
        body: {
          json_path: $('input-json').value.trim(),
          workshop_dir: $('input-workshop').value.trim(),
        },
      });
      await loadState();
      if (data.warnings && data.warnings.length) {
        const box = $('autodetect-result');
        box.className = 'notice warn';
        box.textContent = `已保存，但请注意：${data.warnings.join('；')}`;
      } else {
        toast('配置已保存', 'ok');
        closeDrawer('advanced-drawer');
        startScan({ auto: true });
      }
    } catch (e) {
      toast(e.message, 'error');
    }
  });

  $('opt-log-auto').addEventListener('change', syncLogTimer);

  // 扫描之后目录被删掉或换掉时缩略图会 404，把坏图换成占位框，别留一个破图标
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (!img || img.tagName !== 'IMG' || !img.classList.contains('thumb')) return;
    const span = document.createElement('span');
    span.className = 'thumb thumb-empty';
    span.title = '预览图读不出来了';
    span.textContent = '—';
    img.replaceWith(span);
  }, true);

  // 点标题用系统文件管理器打开这张壁纸的目录；只认标题文字，不占用整行
  document.addEventListener('click', (e) => {
    const target = e.target;
    const link = target && target.closest ? target.closest('.title-link[data-wid]') : null;
    if (link) openFolder(link.dataset.wid);
  });

  // 点缩略图放大看原图，点别处或按 Esc 关掉
  document.addEventListener('click', (e) => {
    const target = e.target;
    const img = target && target.closest ? target.closest('img.thumb') : null;
    if (img) {
      openLightbox(img.src);
    } else if (target === $('lightbox') || target === $('lightbox-img')) {
      closeLightbox();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    closeLightbox();
    closeConfirm();
    closeDrawer('advanced-drawer');
  });

  $('confirm-overlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeConfirm();
  });
}

/* ---------------- 启动 ---------------- */

function scanAgeMs(scan) {
  // scanned_at 是本地时间的 'YYYY-MM-DD HH:MM:SS'，解析不了就当过期处理
  if (!scan || !scan.scanned_at) return Infinity;
  const parsed = Date.parse(String(scan.scanned_at).replace(' ', 'T'));
  return Number.isNaN(parsed) ? Infinity : Date.now() - parsed;
}

async function bootstrap() {
  bind();
  try {
    await loadState();
  } catch (e) {
    toast(`载入失败：${e.message}`, 'error');
    return;
  }

  // 打开面板就自动检查一遍，普通用户不必自己去找「重新扫描」
  if (!state.paths || state.paths.source === 'none') return;
  if (state.scan && scanAgeMs(state.scan) < SCAN_REUSE_MS) {
    // 刚扫过（刷新页面、开第二个窗口）就直接复用，选中状态只存在页面内存里，这里补上
    selectAllOrphans();
  } else {
    startScan({ auto: true });
  }
}

bootstrap();
