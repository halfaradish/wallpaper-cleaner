'use strict';

const TOKEN = window.__PANEL_TOKEN__;

const state = {
  paths: null,
  scan: null,
  recycleSupported: true,
  selected: new Set(),
  subscribedOpen: false,
  subFilter: '',
  jobTimer: null,
  logTimer: null,
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

/* ---------------- 渲染 ---------------- */

function renderConfigLine() {
  const paths = state.paths;
  if (!paths) return;

  if (paths.source === 'none') {
    $('config-line').textContent = '尚未配置路径';
    return;
  }

  const tag = paths.source === 'config'
    ? (paths.config_name || 'config.yml')
    : '自动检测';
  const parts = [tag, paths.json_path, paths.workshop_dir];
  const line = parts.join('  ·  ');
  const el = $('config-line');
  el.textContent = line;
  el.title = `订阅缓存: ${paths.json_path}\n壁纸目录: ${paths.workshop_dir}\n配置文件: ${paths.config_file || '（未创建）'}`;
}

function renderNotice() {
  const el = $('notice');
  const paths = state.paths;
  const scan = state.scan;

  if (paths && paths.source === 'none') {
    el.className = 'notice warn';
    el.innerHTML = `<span>${esc(paths.hint || '尚未配置路径')}</span>
      <span class="notice-actions"><button class="btn ghost" id="notice-settings">打开设置</button></span>`;
    $('notice-settings').addEventListener('click', () => openDrawer('settings-drawer'));
    return;
  }

  if (scan && scan.orphans.length === 0 && scan.unknown.length === 0) {
    el.className = 'notice ok';
    el.innerHTML = '<span>没有需要清理的内容，workshop 目录与订阅列表完全一致。</span>';
    return;
  }

  if (!scan) {
    el.className = 'notice';
    el.innerHTML = '<span>点击右上角「重新扫描」，查看磁盘上与订阅列表不一致的目录。</span>';
    return;
  }

  el.className = 'notice hidden';
  el.innerHTML = '';
}

function renderStats() {
  const scan = state.scan;
  if (!scan) {
    $('stat-subscribed').textContent = '—';
    $('stat-folders').textContent = '—';
    $('stat-orphans').textContent = '—';
    $('stat-freed').textContent = '—';
    $('stat-missing').textContent = '';
    $('stat-unknown').textContent = '';
    $('stat-scanned-at').textContent = '';
    return;
  }

  $('stat-subscribed').textContent = String(scan.subscribed.length);
  $('stat-folders').textContent = String(scan.total_folders);
  $('stat-orphans').textContent = String(scan.orphans.length);
  $('stat-freed').textContent = fmtSize(scan.orphan_bytes);
  $('stat-missing').textContent = scan.missing.length
    ? `订阅但磁盘缺失 ${scan.missing.length} 个`
    : '';
  $('stat-unknown').textContent = scan.unknown.length
    ? `另有未知目录 ${scan.unknown.length} 个`
    : '';
  $('stat-scanned-at').textContent = `扫描于 ${scan.scanned_at}`;
}

function visibleCleanupItems() {
  const scan = state.scan;
  if (!scan) return [];
  return scan.orphans.concat(scan.unknown);
}

function renderOrphans() {
  const items = visibleCleanupItems();
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
    empty.textContent = '没有待清理的目录。';
    selectAll.checked = false;
    selectAll.disabled = true;
    updateDeleteButton();
    return;
  }

  wrap.classList.remove('hidden');
  empty.classList.add('hidden');
  selectAll.disabled = false;

  body.innerHTML = items.map((item) => {
    const checked = state.selected.has(item.wid);
    const kindLabel = item.kind === 'orphan' ? '孤儿目录' : '未知目录';
    return `<tr class="${checked ? 'selected' : ''}">
      <td class="col-check"><input type="checkbox" data-wid="${esc(item.wid)}"${checked ? ' checked' : ''}></td>
      <td class="wid">${esc(item.wid)}</td>
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

function updateDeleteButton() {
  const chosen = visibleCleanupItems().filter((i) => state.selected.has(i.wid));
  const bytes = chosen.reduce((sum, i) => sum + (Number(i.size_bytes) || 0), 0);
  const btn = $('btn-delete');
  btn.disabled = chosen.length === 0;
  btn.textContent = chosen.length
    ? `删除选中 ${chosen.length} 项 · ${fmtSize(bytes)}`
    : '删除选中';
}

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
  const items = filter
    ? scan.subscribed.filter((i) =>
        i.wid.toLowerCase().includes(filter) ||
        String(i.title || '').toLowerCase().includes(filter))
    : scan.subscribed;

  if (items.length === 0) {
    wrap.classList.add('hidden');
    empty.classList.remove('hidden');
    empty.textContent = filter ? '没有匹配的壁纸。' : '没有已订阅的壁纸。';
    return;
  }

  wrap.classList.remove('hidden');
  empty.classList.add('hidden');
  body.innerHTML = items.map((item) => `<tr>
    <td class="wid">${esc(item.wid)}</td>
    <td class="title-cell" title="${esc(item.title)}">${esc(item.title)}</td>
    <td class="col-size">${esc(item.declared_size)}</td>
    <td class="col-size">${fmtSize(item.size_bytes)}</td>
  </tr>`).join('');
}

function renderAll() {
  renderConfigLine();
  renderNotice();
  renderStats();
  renderOrphans();
  renderSubscribed();
  $('version').textContent = window.__PANEL_VERSION__ || '';
}

/* ---------------- 状态加载 ---------------- */

async function loadState() {
  const data = await api('/api/state');
  state.paths = data.paths;
  state.scan = data.scan;
  state.recycleSupported = data.recycle_supported;
  if (data.version) window.__PANEL_VERSION__ = data.version;

  // 丢弃已经不在列表里的选中项（例如删除后重新扫描）
  const valid = new Set(visibleCleanupItems().map((i) => i.wid));
  Array.from(state.selected).forEach((wid) => {
    if (!valid.has(wid)) state.selected.delete(wid);
  });

  renderAll();
  fillSettings();
  return data;
}

/* ---------------- 任务 ---------------- */

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

function renderJob(job) {
  const pct = job.total ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
  const bar = $('progress-bar');
  bar.style.width = `${pct}%`;
  bar.classList.toggle('indeterminate', !job.total);

  $('progress-message').textContent = job.message || '';
  $('progress-count').textContent = job.total ? `${job.done} / ${job.total}` : '';

  const log = $('progress-log');
  log.innerHTML = job.lines.map((line) =>
    `<span class="lv-${esc(line.level)}">[${esc(line.time)}] ${esc(line.text)}</span>`).join('\n');
  log.scrollTop = log.scrollHeight;
}

function stopPolling() {
  clearInterval(state.jobTimer);
  state.jobTimer = null;
}

function pollJob(jobId, onDone) {
  stopPolling();
  state.jobTimer = setInterval(async () => {
    let job;
    try {
      job = await api(`/api/job/${encodeURIComponent(jobId)}`);
    } catch (e) {
      stopPolling();
      closeProgressOverlay();
      toast(e.message, 'error');
      return;
    }
    renderJob(job);
    if (job.status === 'running') return;
    stopPolling();
    if (job.status === 'error') {
      closeProgressOverlay();
      toast(job.error || '任务执行失败', 'error');
      loadState().catch(() => {});
      return;
    }
    closeProgressOverlay();
    if (onDone) await onDone(job);
  }, 400);
}

async function startScan(options) {
  const opts = options || {};
  if (!opts.silent) openProgressOverlay('正在扫描');
  try {
    const { job_id: jobId } = await api('/api/scan', { body: {} });
    pollJob(jobId, async (job) => {
      const result = job.result || null;
      await loadState();
      if (opts.silent) return;
      if (result) {
        const cleanable = result.orphans.length + result.unknown.length;
        toast(
          cleanable
            ? `扫描完成：待清理 ${cleanable} 个目录，可释放 ${fmtSize(result.orphan_bytes)}`
            : '扫描完成：没有需要清理的内容',
          cleanable ? '' : 'ok',
        );
      }
    });
  } catch (e) {
    if (!opts.silent) closeProgressOverlay();
    toast(e.message, 'error');
  }
}

/* ---------------- 删除 ---------------- */

function openConfirm() {
  const chosen = visibleCleanupItems().filter((i) => state.selected.has(i.wid));
  if (!chosen.length) return;

  const bytes = chosen.reduce((sum, i) => sum + (Number(i.size_bytes) || 0), 0);
  $('confirm-count').textContent = String(chosen.length);
  $('confirm-size').textContent = fmtSize(bytes);

  const shown = chosen.slice(0, 40);
  const rows = shown.map((i) =>
    `<li><span>${esc(i.wid)}${i.kind === 'unknown' ? '（未知目录）' : ''}</span><span>${fmtSize(i.size_bytes)}</span></li>`);
  if (chosen.length > shown.length) {
    rows.push(`<li class="more">…… 另有 ${chosen.length - shown.length} 个目录</li>`);
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
  const chosen = visibleCleanupItems().filter((i) => state.selected.has(i.wid));
  if (!chosen.length) return;

  const recycle = state.recycleSupported && $('opt-recycle').checked;
  closeConfirm();
  openProgressOverlay(recycle ? '正在移入回收站' : '正在删除');

  try {
    const { job_id: jobId } = await api('/api/delete', {
      body: {
        wids: chosen.map((i) => i.wid),
        scan_id: state.scan.scanned_at,
        recycle,
      },
    });
    pollJob(jobId, async (job) => {
      const result = job.result || {};
      const deleted = (result.deleted || []).length;
      const failed = (result.failed || []).length;
      const skipped = (result.skipped || []).length;
      state.selected.clear();
      await loadState();

      let message = `已处理 ${deleted} 个目录，释放 ${fmtSize(result.freed_bytes || 0)}`;
      if (skipped) message += `，跳过 ${skipped} 个（已重新订阅）`;
      if (failed) message += `，失败 ${failed} 个`;
      toast(message, failed ? 'error' : 'ok');
      // 删除后刷新列表，保持面板与实际磁盘一致
      startScan({ silent: true });
    });
  } catch (e) {
    closeProgressOverlay();
    toast(e.message, 'error');
  }
}

/* ---------------- 抽屉 ---------------- */

function openDrawer(id) {
  $(id).classList.remove('hidden');
  if (id === 'logs-drawer') refreshLogs();
}

function closeDrawer(id) {
  $(id).classList.add('hidden');
  if (id === 'logs-drawer') {
    clearInterval(state.logTimer);
    state.logTimer = null;
  }
}

function fillSettings() {
  const paths = state.paths;
  if (!paths) return;
  if (document.activeElement !== $('input-json')) $('input-json').value = paths.json_path || '';
  if (document.activeElement !== $('input-workshop')) $('input-workshop').value = paths.workshop_dir || '';
  $('settings-meta').textContent = paths.config_file
    ? `配置文件：${paths.config_file}`
    : `配置文件尚未创建，保存后将写入项目根目录的 config.yml`;
}

async function refreshLogs() {
  try {
    const data = await api('/api/logs?lines=400');
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

  $('btn-settings').addEventListener('click', () => openDrawer('settings-drawer'));
  $('btn-logs').addEventListener('click', () => openDrawer('logs-drawer'));

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
        closeDrawer('settings-drawer');
      }
    } catch (e) {
      toast(e.message, 'error');
    }
  });

  $('opt-log-auto').addEventListener('change', (e) => {
    clearInterval(state.logTimer);
    state.logTimer = null;
    if (e.target.checked) {
      state.logTimer = setInterval(() => {
        if ($('logs-drawer').classList.contains('hidden')) return;
        refreshLogs();
      }, 2000);
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    closeConfirm();
    closeDrawer('settings-drawer');
    closeDrawer('logs-drawer');
  });

  // 点击遮罩关闭弹窗
  $('confirm-overlay').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeConfirm();
  });
}

bind();
loadState().catch((e) => toast(`载入失败：${e.message}`, 'error'));
