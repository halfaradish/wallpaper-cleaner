// 后端失联时的常驻横幅与自动重连
//
// 场景很实在：面板开着，用户在别处重启了程序（或者程序崩了）。以前的表现是
// 所有轮询静默失败、界面停在旧数据上、点什么都没反应也不报错——最难排查的那种。
// 现在网络层一失败就挂出横幅，并按固定间隔自动重连。
//
// 间隔取固定的 2 秒而不是指数退避：这是本机回环上的一个进程，要么在要么不在；
// 用户把程序重新启动之后，越快接上越好，退避到几十秒反而添乱。
import { $, show, hide, setText, isHidden } from '../core/dom.js';
import { onConnectionChange, onTokenStale, isTokenStale } from '../core/api.js';
import { setRepeating, clearTimer } from '../core/poll.js';
import { loadState, state } from '../core/store.js';
import { toast } from './toast.js';
import { startScan } from '../views/scan.js';

const RETRY_MS = 2000;

function showBanner(reason) {
  setText($('connbar-text'), `${reason || '与面板后端失去连接'}，正在自动重试…`);
  show($('connbar'));
  setRepeating('reconnect', retry, RETRY_MS);
}

function hideBanner() {
  hide($('connbar'));
  clearTimer('reconnect');
}

async function retry() {
  try {
    await loadState();
  } catch (e) {
    // 还是连不上，等下一轮
  }
}

// 手动重试：立即试一次，不用等下一个周期
export async function reconnectNow() {
  setText($('connbar-text'), '正在重试…');
  try {
    await loadState();
    toast('已重新连上面板后端', 'ok');
  } catch (e) {
    toast(e.message, 'error');
  }
}

export function reloadPage() {
  window.location.reload();
}

export function initConnection() {
  onConnectionChange((isDown, error) => {
    if (isDown) {
      showBanner(error && error.message);
      return;
    }
    // 只在"确实挂着横幅"时才处理恢复，避免正常的首次加载也走一遍
    if (isHidden($('connbar'))) return;
    hideBanner();
    toast('已重新连上面板后端', 'ok');
    // 重连上来的多半是一个刚启动的后端，它手里没有上次的扫描结果。
    // 界面上"打开面板会自动检查一遍"这个承诺要兑现，否则用户面对的是
    // 一个空的界面和一句不成立的说明。
    // 令牌已经作废时不要试：那一发请求必然 403，只会多一条没用的报错。
    if (!state.scan && !isTokenStale()) startScan({ auto: true });
  });

  onTokenStale(() => {
    hide($('connbar'));
    clearTimer('reconnect');
    show($('stalebar'));
  });
}
