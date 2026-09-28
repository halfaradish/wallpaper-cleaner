// 定时器按名字统一管理
//
// 原先五个 timer（任务轮询 / Steam 探测 / 检查更新 / 日志刷新 / 进度条延时）各自
// 挂在 state 上，谁开的、什么时候该停全靠调用方记得。改成具名注册表后：
// 同名再开自动顶掉前一个（不会出现两个轮询同时跑），也能一次清干净。
//
// 周期性定时器还会跟着窗口可见性走：桌面窗口最小化、或浏览器切到别的标签页时
// 暂停，恢复时立刻补跑一次。这个面板会长时间开着，不可见时还每 400ms 打一次接口
// 纯属白烧 CPU。
const timers = new Map();

function cancel(entry) {
  if (entry.id === null) return;
  if (entry.repeating) clearInterval(entry.id);
  else clearTimeout(entry.id);
  entry.id = null;
}

export function clearTimer(name) {
  const entry = timers.get(name);
  if (entry === undefined) return;
  cancel(entry);
  timers.delete(name);
}

export function clearAllTimers() {
  Array.from(timers.keys()).forEach(clearTimer);
}

export function hasTimer(name) {
  return timers.has(name);
}

// 一次性：到期后自己从表里摘掉
export function setTimer(name, fn, delay) {
  clearTimer(name);
  const entry = { id: null, fn, delay, repeating: false };
  entry.id = setTimeout(() => {
    timers.delete(name);
    fn();
  }, delay);
  timers.set(name, entry);
}

// 周期性：要停就 clearTimer(name)
export function setRepeating(name, fn, delay) {
  clearTimer(name);
  const entry = { id: null, fn, delay, repeating: true };
  if (!isHidden()) entry.id = setInterval(fn, delay);
  timers.set(name, entry);
}

function isHidden() {
  return typeof document !== 'undefined' && document.hidden;
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    timers.forEach((entry) => {
      if (!entry.repeating) return;
      if (document.hidden) {
        cancel(entry);
      } else if (entry.id === null) {
        entry.id = setInterval(entry.fn, entry.delay);
        // 立刻补一次：暂停期间状态可能已经变了，回来看到旧进度最误导人
        entry.fn();
      }
    });
  });
}
