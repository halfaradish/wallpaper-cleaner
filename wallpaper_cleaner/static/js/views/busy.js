// 忙碌态：任务进行中，把会打架的入口都禁掉
//
// 后端同一时刻只跑一个任务，所以这期间点"重新扫描"只会拿到 409。
// 与其让用户点出一个错误，不如让按钮当场说明"现在不行"。
import { $ } from '../core/dom.js';
import { onBusyChange, state } from '../core/store.js';
import { updateOrphanButtons } from './orphans.js';
import { updateUnsubscribeButton } from './subscribed.js';

export function initBusy() {
  onBusyChange(() => {
    const scan = $('btn-scan');
    if (scan) {
      scan.disabled = state.busy;
      scan.textContent = state.busy ? '正在扫描…' : '重新扫描';
    }

    const checkAll = $('check-all');
    if (checkAll) checkAll.disabled = state.busy;
    const checkAllSub = $('check-all-sub');
    if (checkAllSub) checkAllSub.disabled = state.busy;

    // 这两个按钮的禁用条件里也含 busy，交给它们自己的更新函数统一算
    updateOrphanButtons();
    updateUnsubscribeButton();
  });
}
