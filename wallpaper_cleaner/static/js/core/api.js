// 与后端的唯一出口：统一带 token、统一把错误归一成 Error(message)、统一上报连接状态
//
// 两件以前没做的事：
// 1. 超时。fetch 默认不超时，后端卡住时请求会一直挂着，界面看起来只是"没反应"。
// 2. 连接状态。以前后端重启之后，所有轮询都在 catch 里静默 return，
//    界面永远停在旧数据上，既不报错也不恢复。现在网络层失败会广播出去，
//    由 ui/connection.js 挂出横幅并自动重连。
import { PANEL_TOKEN } from './config.js';

const TIMEOUT_MS = 10000;

const listeners = [];
let down = false;

export function onConnectionChange(fn) {
  listeners.push(fn);
}

export function isConnectionDown() {
  return down;
}

function setDown(next, error) {
  if (down === next) return;
  down = next;
  listeners.forEach((fn) => fn(next, error));
}

// 面板 token 是每次运行现生成的，所以后端一重启，这个页面手里的 token 就作废了：
// 读接口还能用（它们不要 token），写接口一律 403。这种"看起来连上了、其实一写就失败"
// 的状态必须明确说出来，并且只有刷新页面能解决（新 token 只在新页面里）。
const staleListeners = [];
let tokenStale = false;

export function onTokenStale(fn) {
  staleListeners.push(fn);
}

export function isTokenStale() {
  return tokenStale;
}

function setTokenStale() {
  if (tokenStale) return;
  tokenStale = true;
  staleListeners.forEach((fn) => fn());
}

export async function api(path, options) {
  const opts = Object.assign({}, options);
  if (opts.body !== undefined) {
    opts.method = opts.method || 'POST';
    opts.headers = Object.assign({}, opts.headers, {
      'Content-Type': 'application/json',
      'X-Panel-Token': PANEL_TOKEN,
    });
    opts.body = JSON.stringify(opts.body);
  }

  // 超时用 AbortController：到点主动掐掉，而不是无限等
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  opts.signal = controller.signal;

  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    // fetch 抛异常 = 网络层就没走通（后端没了、端口关了、或者超时）。
    // 这与"后端返回 4xx/5xx"是两回事：后者说明后端活着，不该报连接故障。
    const message = e && e.name === 'AbortError'
      ? `请求超时（超过 ${TIMEOUT_MS / 1000} 秒没有响应）`
      : '无法连接到面板后端';
    const err = new Error(message);
    err.network = true;
    setDown(true, err);
    throw err;
  } finally {
    clearTimeout(timer);
  }

  setDown(false);

  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const message = (data && data.error) || `请求失败（HTTP ${res.status}）`;
    // 403 + 提到令牌 = 后端换了一个进程，不是用户操作有问题
    if (res.status === 403 && message.includes('令牌')) setTokenStale();
    const err = new Error(message);
    err.payload = data;
    err.status = res.status;
    throw err;
  }
  return data;
}
