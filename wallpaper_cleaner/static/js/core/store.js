// 单一状态 + 渲染订阅
//
// 仍然是一个可变的全局对象（渲染函数直接读它，重绘只读不写），但"谁来重画"改成注册制：
// 各视图在加载时登记自己的渲染函数，loadState 之后统一触发。这样新增一个视图不用回头
// 去改 renderAll 的调用列表。
import { api } from './api.js';
import { setVersion } from './config.js';

export const state = {
  paths: null,
  scan: null,
  logFile: null,
  recycleSupported: true,
  selected: new Set(),            // 待清理列表的勾选（要删的）
  subSelected: new Set(),         // 已订阅列表的勾选（要取消订阅的）
  sessionUnsubscribed: new Set(), // 本会话已取消订阅，等待 Steam 同步
  sessionResubscribed: new Set(), // 本会话已重新订阅，等待 Steam 同步
  steam: null,
  update: null,
  subscribedOpen: false,
  subFilter: '',
  sortOrphans: 'default',
  sortSub: 'default',
  busy: false,
};

const renderers = [];

export function onRender(fn) {
  renderers.push(fn);
}

export function renderAll() {
  for (const fn of renderers) fn();
}

// 忙碌标记放在 store 里而不是某个视图里：任务引擎（core/job.js）也要能清掉它，
// 而任务引擎不该反过来依赖视图。谁负责把按钮画成禁用态，由视图自己登记。
const busyListeners = [];

export function onBusyChange(fn) {
  busyListeners.push(fn);
}

export function setBusy(busy) {
  state.busy = busy;
  for (const fn of busyListeners) fn(busy);
}

// 待清理 + 无法确定的：两处显示必须一致，所以只有这一个来源
export function cleanupItems() {
  const scan = state.scan;
  if (!scan) return [];
  return scan.orphans.concat(scan.unknown);
}

export function subscribedItems() {
  return state.scan ? state.scan.subscribed : [];
}

// 可以被清理/重新订阅的残留项：重新订阅过的不在其中
export function selectableOrphans() {
  const scan = state.scan;
  if (!scan) return [];
  return scan.orphans.filter((item) => !state.sessionResubscribed.has(item.wid));
}

export async function loadState() {
  const data = await api('/api/state');
  state.paths = data.paths;
  state.scan = data.scan;
  state.logFile = data.log_file;
  state.recycleSupported = data.recycle_supported;
  if (data.steam) state.steam = data.steam;
  if (data.update) state.update = data.update;
  state.sessionUnsubscribed = new Set(data.session_unsubscribed || []);
  state.sessionResubscribed = new Set(data.session_resubscribed || []);
  setVersion(data.version);

  // 丢弃已经不在列表里的选中项
  const valid = new Set(cleanupItems().map((i) => i.wid));
  Array.from(state.selected).forEach((wid) => {
    if (!valid.has(wid)) state.selected.delete(wid);
  });
  const validSub = new Set(subscribedItems().map((i) => i.wid));
  Array.from(state.subSelected).forEach((wid) => {
    if (!validSub.has(wid)) state.subSelected.delete(wid);
  });
  // 刚重新订阅的目录不再允许删除
  state.sessionResubscribed.forEach((wid) => state.selected.delete(wid));

  renderAll();
  return data;
}
