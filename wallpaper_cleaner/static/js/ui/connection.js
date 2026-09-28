// 连接状态：后端失联与令牌失效
//
// 这两种状态以前是完全静默的——后端没了，所有轮询在 catch 里 return，界面停在旧数据上
// 既不报错也不恢复，是最难排查的一类问题。现在各自有一条横幅，而且区分得很清楚：
//
//   失联：读接口也不通了，会自动重试，恢复了就继续用。
//   令牌失效：读接口还通，只有写接口 403。这时重试没有意义，唯一的出路是刷新页面
//             （新 token 只在新页面里），所以横幅上只给"刷新页面"。
import { $, setText, setHidden } from '../core/dom.js';
import { isTokenStale, onConnectionChange, onTokenStale } from '../core/api.js';
import { clearTimer, setTimer } from '../core/poll.js';
import { state, loadState } from '../core/store.js';
import { toast } from './toast.js';
import { setStatusText } from './statusbar.js';

const RETRY_MS = 2000;

let wasDown = false;

function showConnBanner(message) {
  setText($('banner-conn-text'), message);
  setHidden($('banner-conn'), false);
  setStatusText('与后端失去连接');
}

function hideConnBanner() {
  setHidden($('banner-conn'), true);
}

export function reconnectNow() {
  clearTimer('reconnect');
  loadState()
    .then(() => toast('已重新连接', 'ok'))
    .catch(() => scheduleReconnect());
}

function scheduleReconnect() {
  setTimer('reconnect', () => {
    loadState().catch(() => scheduleReconnect());
  }, RETRY_MS);
}

export function reloadPage() {
  window.location.reload();
}

export function initConnection() {
  onConnectionChange((down, error) => {
    // 令牌失效时横幅由下面那条接管：这时候后端是活的，再叠一条"失联"只会误导
    if (isTokenStale()) return;

    if (down) {
      wasDown = true;
      showConnBanner(error && error.message ? error.message : '与面板后端失去连接');
      scheduleReconnect();
      return;
    }

    hideConnBanner();
    setStatusText('就绪');
    if (wasDown) {
      wasDown = false;
      toast('已重新连接到面板后端', 'ok');
      // 断线期间状态可能已经变了（比如另一个窗口扫完了），补一次
      loadState().catch(() => {});
    }
  });

  onTokenStale(() => {
    clearTimer('reconnect');
    hideConnBanner();
    setHidden($('banner-token'), false);
    setStatusText('访问令牌已失效，请刷新页面');
  });
}

// 面板启动时会自动扫一遍，扫描期间不该被重连逻辑打断
export function connectionIsHealthy() {
  return !state.busy;
}
