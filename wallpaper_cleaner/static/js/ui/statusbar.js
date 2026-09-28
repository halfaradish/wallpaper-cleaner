// 状态栏：常驻在窗口底部的一行，回答"现在在干什么"
//
// 它是这块界面上唯一始终存在的状态出口。扫描进度、上次扫描时间、连接状态都写在这里，
// 所以必须只有一个模块负责写它——两处各写各的，最后一定是互相覆盖。
import { $, setHidden, setText } from '../core/dom.js';

const DEFAULT_TEXT = '就绪';

export function setStatusText(text) {
  setText($('status-text'), text || DEFAULT_TEXT);
}

export function setStatusCount(text) {
  setText($('status-count'), text || '');
}

/**
 * ratio 为 null 时收起进度条；0 到 1 之间时显示并推到位。
 * 进度条是状态栏上沿那条 2px 的线，不占布局高度，所以显示与隐藏不会让内容跳动。
 */
export function setStatusProgress(ratio) {
  const wrap = $('status-progress');
  const fill = $('status-progress-fill');
  if (!wrap || !fill) return;
  if (ratio === null || ratio === undefined) {
    setHidden(wrap, true);
    fill.classList.remove('indeterminate');
    return;
  }
  setHidden(wrap, false);
  if (ratio < 0) {
    // 总量还不知道：换成来回扫的条，而不是停在 0% 不动
    fill.classList.add('indeterminate');
    fill.style.transform = '';
    return;
  }
  fill.classList.remove('indeterminate');
  fill.style.transform = `scaleX(${Math.max(0, Math.min(1, ratio)).toFixed(4)})`;
}

export function setStatusLogButton(visible) {
  setHidden($('btn-status-log'), !visible);
}
