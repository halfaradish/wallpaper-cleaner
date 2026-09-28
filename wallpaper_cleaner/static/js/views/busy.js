// 忙碌标记落到界面上的样子
//
// 只负责「把哪些控件画成不可用」，判断什么时候忙由 store.setBusy 决定：
// 任务引擎要能清掉忙碌标记，但它不该反过来依赖视图。
import { $ } from '../core/dom.js';
import { state, onBusyChange, subscribedItems, selectableOrphans } from '../core/store.js';
import { updateDeleteButton, updateResubscribeButton } from './orphans.js';
import { updateUnsubscribeButton } from './subscribed.js';

function applyBusy() {
  $('btn-scan').disabled = state.busy;
  updateDeleteButton();
  updateResubscribeButton();
  updateUnsubscribeButton();
  $('check-all').disabled = state.busy || !selectableOrphans().length;
  $('check-all-sub').disabled = state.busy || !subscribedItems().length;
}

export function initBusy() {
  onBusyChange(applyBusy);
}
