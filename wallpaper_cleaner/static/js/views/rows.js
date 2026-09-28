// 两张表共用的行零件
//
// 「待清理」和「已订阅」的行结构有九成相同（勾选框、预览图、ID、标题、两个尺寸列），
// 原先各写一份模板，改一处就得记得改另一处。这里只留一个 buildRow，差异靠参数表达：
// 已订阅表没有「类型」列，刚取消订阅的标记挂在 ID 格，待清理表的标记挂在类型格。
import { $ } from '../core/dom.js';
import { esc, fmtSize, diskBytes, diskHint, NO_TITLE, NO_TITLE_TEXT, NO_TITLE_HINT } from '../core/format.js';
import { api } from '../core/api.js';
import { toast } from '../ui/toast.js';

// 预览图是作者随内容一起发布的、就躺在壁纸目录里（project.json 的 preview 字段），
// 服务端只把字节发过来，解码、缩放、GIF 播放全交给浏览器。
// 目录里的图被 Steam 连着内容一起清掉时，服务端会退回 Wallpaper Engine 缓存的浏览
// 缩略图，这时 thumb_source 是 'we'——图还在，只是原图没了，标题里说明一下。
//
// 外面套一个 <button>：放大预览原来只能靠鼠标点图，键盘用户到不了。
// 图片本身 alt 留空（它是装饰），动作的名字挂在按钮的 aria-label 上。
export function thumbCell(item) {
  if (!item.thumb_source) {
    const label = item.wp_type ? esc(item.wp_type) : '—';
    const tip = item.content_missing
      ? '内容已被 Steam 清理，这个文件夹里也没有可用的预览图'
      : '这个文件夹里没有预览图';
    return `<td class="col-thumb"><span class="thumb thumb-empty"
      title="${esc(tip)}">${label}</span></td>`;
  }
  const src = `/api/thumb?wid=${encodeURIComponent(item.wid)}`;
  const tip = item.thumb_source === 'we'
    ? '来自 Wallpaper Engine 的缩略图缓存（原预览图已不在），点击放大'
    : '点击放大';
  const name = (item.title || '').trim();
  const label = NO_TITLE.includes(name) || !name ? item.wid : name;
  return `<td class="col-thumb"><button type="button" class="thumb-btn" data-thumb-src="${esc(src)}"
    aria-label="放大预览：${esc(label)}" title="${esc(tip)}"
    ><img class="thumb" src="${src}" alt="" loading="lazy" decoding="async"></button></td>`;
}

// 标题前的文件夹图标。用内联 SVG 而不是 emoji 或字体私有码位：彩色 emoji 会跟这套
// 单色面板打架，私有码位（Segoe MDL2 之类）换个环境就可能变豆腐块。
// stroke 用 currentColor，所以它跟着文字一起变亮；24 的 viewBox 缩到 13px 后
// 描边约 1px，与表格分隔线、点状下划线同一个量级，不显笨重。
const FOLDER_ICON = `<svg class="title-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><path
  d="M3.5 18.5V5.5h5.5l2 2.5h9.5v10.5z"/></svg>`;

// 标题文字可点：用系统文件管理器打开这张壁纸的目录。热区只有标题文字本身，
// 不做整行/整格可点——那样会和行内勾选框、行 hover 选中态打架。
// 取不到标题的行也给一个可点的占位文字：内容被 Steam 清理过的残留本来就只剩一串 ID，
// 能不能打开目录不该由标题决定。
//
// 用 <button> 而不是 <span> 绑点击：span 不在 Tab 序列里，键盘用户根本到不了
// 「打开目录」这个功能。按钮自带 Enter/Space 激活与焦点环，样式已在 CSS 里重置过。
export function titleLink(item, text) {
  const title = (text || '').trim();
  // 要么是真的标题，要么是没有标题：后端给已订阅行的兜底是「未知」，历史上还用过「—」
  const named = Boolean(title) && !NO_TITLE.includes(title);
  const label = named ? title : NO_TITLE_TEXT;
  return `<button type="button" class="title-link${named ? '' : ' muted'}" data-wid="${esc(item.wid)}"
    title="${esc(named ? title : NO_TITLE_HINT)}"
    aria-label="在资源管理器中打开「${esc(label)}」的目录"
    >${FOLDER_ICON}<span class="title-text">${esc(label)}</span></button>`;
}

// 内容被 Steam 清理掉（目录里没有 project.json）时的行内提示。
// 放在标题格里：窄屏隐藏的是「类型」列，标题列一直都在。
export function missingBadge(item) {
  return item.content_missing
    ? ' <span class="badge missing" title="目录里没有 project.json：可能已被 Steam 清理，也可能还没下载完">内容已缺失</span>'
    : '';
}

/**
 * 一行。opts:
 *   checked   勾选态
 *   pending   等待 Steam 同步（整行压暗）
 *   disabled  勾选框禁用（刚重新订阅的目录不再允许勾选删除）
 *   title     覆盖标题（待清理表传 item.title || ''，已订阅表传 item.title）
 *   showType  标题后面是否跟「· 视频」这类类型后缀（只有待清理表显示）
 *   kindCell  「类型」格的内容；不传就不输出该列（已订阅表没有这一列）
 *   widMark   ID 格里的附加标记（已订阅表用它标「已取消订阅」）
 */
export function buildRow(item, opts) {
  const o = opts || {};
  const checked = Boolean(o.checked);
  const pending = Boolean(o.pending);
  const title = o.title !== undefined ? o.title : (item.title || '');
  const type = o.showType && item.wp_type
    ? ` <span class="wp-type">· ${esc(item.wp_type)}</span>`
    : '';
  const kindCell = o.kindCell ? `<td class="col-kind">${o.kindCell}</td>` : '';
  // 每行的勾选框都要有可访问名：读屏念"复选框"而不说这是哪一行的话，整张表等于读不了。
  // 取不到标题的行（内容已被清理）就用 ID 当名字。
  const named = title.trim() && !NO_TITLE.includes(title.trim());
  const boxLabel = named ? `选择 ${title.trim()}（${item.wid}）` : `选择 ${item.wid}`;
  return `<tr class="${checked ? 'selected' : ''}${pending ? ' pending' : ''}">
      <td class="col-check"><input type="checkbox" data-wid="${esc(item.wid)}"
        aria-label="${esc(boxLabel)}"${checked ? ' checked' : ''}${o.disabled ? ' disabled' : ''}></td>
      ${thumbCell(item)}
      <td class="wid">${esc(item.wid)}${o.widMark || ''}</td>
      <td class="title-cell" title="${esc(title)}">${titleLink(item, title)}${type}${missingBadge(item)}</td>
      ${kindCell}
      <td class="col-size col-declared">${esc(item.declared_size)}</td>
      <td class="col-size col-usage" title="${esc(diskHint(item))}">${fmtSize(diskBytes(item))}</td>
    </tr>`;
}

async function openFolder(wid) {
  try {
    await api('/api/reveal', { body: { wid } });
  } catch (e) {
    toast(`打不开文件夹：${e.message}`, 'error');
  }
}

/**
 * 行勾选用委托：以前每重绘一次就 querySelectorAll 逐个挂监听，行多了是白花开销，
 * 也容易在重绘与挂监听之间漏掉状态。委托只挂一次，重绘多少次都不受影响。
 *
 * onChange(wid, checked)  单行变化
 * onBatchEnd()            一次 Shift 连选之后的收尾（按钮文案只需更新一次）
 */
export function initRowCheckboxes(tbodyId, onChange, onBatchEnd) {
  const body = $(tbodyId);
  if (!body) return;
  // Shift 连选的锚点：上一次点过的行
  let anchor = null;

  // 用 click 而不是 change：change 事件里拿不到 shiftKey。
  // 复选框的 checked 在 click 派发前就已经翻好了，所以这里读到的就是新状态。
  body.addEventListener('click', (e) => {
    const box = e.target;
    if (!box || box.type !== 'checkbox' || !box.dataset.wid) return;

    const rows = Array.from(body.children).filter((tr) => tr.tagName === 'TR');
    const current = box.closest('tr');
    const index = rows.indexOf(current);

    // Shift 连选：从上次点的那一行到这一行整段一起改。
    // 批量操作本来就是"连着这几行一起处理"，一行一行点太慢。
    if (e.shiftKey && anchor !== null && anchor !== index && index >= 0) {
      const from = Math.min(anchor, index);
      const to = Math.max(anchor, index);
      for (let i = from; i <= to; i += 1) {
        // 被点中的那一行要跳过：它的 checked 已经被浏览器翻过了，
        // 再按"状态不同才改"处理会把它整个漏掉（看起来勾上了，实际没记进状态）
        if (i === index) continue;
        const row = rows[i];
        const rowBox = row.querySelector('input[type="checkbox"]');
        if (!rowBox || rowBox.disabled) continue;
        if (rowBox.checked !== box.checked) {
          rowBox.checked = box.checked;
          row.classList.toggle('selected', box.checked);
          onChange(rowBox.dataset.wid, box.checked);
        }
      }
      current.classList.toggle('selected', box.checked);
      onChange(box.dataset.wid, box.checked);
      if (onBatchEnd) onBatchEnd();
    } else {
      current.classList.toggle('selected', box.checked);
      onChange(box.dataset.wid, box.checked);
    }

    anchor = index;
  });
}

// 点标题用系统文件管理器打开这张壁纸的目录；只认标题文字，不占用整行
export function initTitleLinks() {
  document.addEventListener('click', (e) => {
    const target = e.target;
    const link = target && target.closest ? target.closest('.title-link[data-wid]') : null;
    if (link) openFolder(link.dataset.wid);
  });
}

// 扫描之后目录被删掉或换掉时缩略图会 404，把坏图换成占位框，别留一个破图标
export function initThumbFallback() {
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (!img || img.tagName !== 'IMG' || !img.classList.contains('thumb')) return;
    const span = document.createElement('span');
    span.className = 'thumb thumb-empty';
    span.title = '预览图读不出来了';
    span.textContent = '—';
    img.replaceWith(span);
  }, true);
}
