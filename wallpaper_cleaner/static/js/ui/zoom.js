// 缩放：Ctrl+滚轮调级，Ctrl+加/减号与 Ctrl+0 走键盘，级别存服务端 prefs
//
// 只在桌面窗口（WebView2）里接管，浏览器模式完全不碰。浏览器有自己的页面缩放
// （Ctrl+滚轮，整页放大、含图片、可以到 500%），那才是浏览器里正确的形态；把它
// 拦下来换成只缩放 rem 标度的应用级缩放属于夺人所爱，两层叠加还更乱。桌面窗口
// 则相反：pywebview 在非 debug 下关掉了「浏览器加速键」
// （AreBrowserAcceleratorKeysEnabled=False），Ctrl+滚轮原生缩放是死的，而且级别
// 记在 per-origin 的用户数据目录里，随机端口 + private mode 每次启动都会重置，
// 所以这里自己接管，级别随 prefs.json 走。
//
// 应用方式是改根字号而不是 CSS zoom：字号与间距令牌全部是 rem（tokens.css 的硬
// 规则），改根字号等于整张标度表跟着走；1px 发丝线与圆角按设计意图保持 px 不缩放。
// 初始级别由服务端注入 <meta name="panel-zoom">，首帧由 zoom-boot.js 在渲染前
// 应用，这里只负责交互与持久化。
import { toast } from './toast.js';
import { savePrefs } from '../core/prefs.js';

const MIN = 70;     // 再小文字就读不动了
const MAX = 160;    // 再大固定 px 的图标与控件就明显失衡（web.py 的 ZOOM_MIN/MAX 同步）
const STEP = 10;
const DEFAULT = 100;
const NOTCH = 100;      // 一个滚轮刻度约 100px（Chromium 像素模式）
const WHEEL_IDLE = 160; // 停手这么久算一次手势结束，累积的余量清零
const PERSIST_DELAY = 350;

let current = DEFAULT;
let wheelAcc = 0;
let wheelIdle = null;
let persistTimer = null;

function apply(pct) {
  if (pct === DEFAULT) {
    document.documentElement.style.removeProperty('font-size');
  } else {
    document.documentElement.style.fontSize = `${pct}%`;
  }
}

function clamp(pct) {
  return Math.min(MAX, Math.max(MIN, pct));
}

// 滚轮一下连跳好几级的话级别就不可控了；停手后统一落盘并报一次当前值
function schedulePersist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    persistTimer = null;
    toast(`缩放 ${current}%`, 'info');
    savePrefs({ zoom: current }).then((error) => {
      if (error) toast(`缩放已调整，但存不下来：${error}`, 'error');
    });
  }, PERSIST_DELAY);
}

export function setZoom(pct) {
  const next = clamp(pct);
  if (next === current) return;
  current = next;
  apply(current);
  schedulePersist();
}

function onWheel(event) {
  if (!event.ctrlKey || event.deltaY === 0) return;
  // 桌面窗口里原生缩放本来就是死的，这里是兜底：万一以后 pywebview 打开了
  // 加速键，也不会有两层缩放叠在一起
  event.preventDefault();
  // deltaMode 1（行）/ 2（页）按 40px 一行、一页 400px 折算；Chromium 实际发的是像素
  const factor = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 400 : 1;
  wheelAcc += event.deltaY * factor;
  // 余量跨事件累积：高精度触控板一次手势会发很多小增量，只看单个事件的话
  // 永远到不了阈值——被 preventDefault 拦掉的缩放就表现为「完全没反应」。
  // 停手后清零，免得下一次手势一上来就补走一步。
  clearTimeout(wheelIdle);
  wheelIdle = setTimeout(() => { wheelAcc = 0; }, WHEEL_IDLE);
  // 往下滚（正值）是缩小，与所有缩放手势的习惯一致
  while (wheelAcc >= NOTCH || wheelAcc <= -NOTCH) {
    setZoom(current + (wheelAcc > 0 ? -STEP : STEP));
    wheelAcc += wheelAcc > 0 ? -NOTCH : NOTCH;
  }
}

function onKeyDown(event) {
  // WebView2 的加速键是关的，这些键原本是死键；接管后键盘与滚轮才有同一套行为
  if (!event.ctrlKey || event.altKey || event.metaKey) return;
  if (event.key === '0') setZoom(DEFAULT);
  else if (event.key === '=' || event.key === '+') setZoom(current + STEP);
  else if (event.key === '-') setZoom(current - STEP);
  else return;
  event.preventDefault();
}

export function initZoom() {
  // 浏览器模式不接管：把原生页面缩放留给浏览器（见文件头）
  if (!(window.chrome && window.chrome.webview)) return;
  const meta = document.querySelector('meta[name="panel-zoom"]');
  const pct = parseInt(meta && meta.content, 10);
  current = Number.isFinite(pct) && pct >= MIN && pct <= MAX ? pct : DEFAULT;
  // zoom-boot.js 通常已经在首帧应用过了；这里再执行一遍是无害的幂等操作，
  // 兜住引导脚本没跑成的极端情况
  apply(current);
  // passive 必须显式为 false：兜底用的 preventDefault 在 passive 监听里是空操作
  document.addEventListener('wheel', onWheel, { passive: false });
  document.addEventListener('keydown', onKeyDown);
}
