// 外壳：顶栏的路径行、内容区顶部的提示条、状态栏的常态文字
//
// 这三处都在回答同一个问题："这个面板现在看的是哪儿、看到了什么"。
// 它们不重复：路径行说数据来源，提示条说需要你处理的事，状态栏说刚才发生了什么。
import { $, h, hide, show } from '../core/dom.js';
import { state, onRender, onBusyChange } from '../core/store.js';
import { setStatusCount, setStatusText } from '../ui/statusbar.js';

const SOURCE_LABEL = { config: '配置文件', autodetect: '自动检测', none: '' };

export function renderPathLine() {
  const line = $('path-line');
  if (!line) return;
  const paths = state.paths;

  if (!paths || paths.source === 'none' || !paths.workshop_dir) {
    line.textContent = '还没有找到 Wallpaper Engine，去「设置」里指定路径';
    line.classList.add('warn');
    return;
  }

  line.classList.remove('warn');
  line.textContent = paths.workshop_dir;
  line.title = paths.workshop_dir;
}

export function renderNotice() {
  const box = $('notice');
  if (!box) return;
  box.textContent = '';
  box.className = 'notice';

  const paths = state.paths;
  const scan = state.scan;

  // 还没找到 Wallpaper Engine：这是唯一一条必须先解决才能干别的事的提示
  if (!paths || paths.source === 'none') {
    box.append(
      h('span', { class: 'notice-text', text: paths && paths.hint ? paths.hint : '还没有找到 Wallpaper Engine 的安装位置。' }),
      h('button', {
        class: 'btn ghost',
        type: 'button',
        dataset: { action: 'go-settings' },
        text: '打开设置',
      }),
    );
    show(box);
    box.classList.add('warn');
    return;
  }

  if (!scan) {
    // 扫描进行中时状态栏已经在说这件事了，这里不必再说一遍
    if (state.busy) { hide(box); return; }
    box.append(
      h('span', { class: 'notice-text', text: '还没有扫描结果。' }),
      h('button', {
        class: 'btn ghost',
        type: 'button',
        dataset: { action: 'rescan' },
        text: '立即扫描',
      }),
    );
    show(box);
    return;
  }

  // 有订阅记录但磁盘上没有对应文件夹：不是错误，是"还没下载"，说清楚就行
  if (scan.missing && scan.missing.length) {
    box.append(h('span', {
      class: 'notice-text',
      text: `有 ${scan.missing.length} 个已订阅的壁纸还没有下载到磁盘上，它们不占空间，也不需要处理。`,
    }));
    show(box);
    return;
  }

  hide(box);
}

function renderStatusIdle() {
  const scan = state.scan;
  if (!scan) {
    setStatusText(state.busy ? '正在检查…' : '就绪');
    setStatusCount('');
    return;
  }
  setStatusText(`扫描于 ${scan.scanned_at}，共 ${scan.total_folders} 个文件夹`);
  const source = SOURCE_LABEL[(state.paths && state.paths.source) || ''] || '';
  setStatusCount(source ? `路径来自${source}` : '');
}

export function initShell() {
  onRender(() => {
    renderPathLine();
    renderNotice();
    renderStatusIdle();
  });

  // 扫描期间状态栏归进度管，扫完再交回常态文字
  onBusyChange((busy) => {
    if (!busy) renderStatusIdle();
    renderNotice();
  });
}
