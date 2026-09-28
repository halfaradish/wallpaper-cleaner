// 首帧缩放引导：把服务端注入到 <meta name="panel-zoom"> 的级别立刻应用到根字号，
// 赶在首次绘制之前。交互（Ctrl+滚轮 / Ctrl+0）与持久化在 ui/zoom.js，这里只有
// 一个目的——页面不许先按 100% 画一遍再跳档。
//
// 只在桌面窗口（WebView2）里生效：浏览器有自己的页面缩放，缩放级别是桌面窗口的
// 偏好，不该在浏览器里再叠一层应用级缩放（见 ui/zoom.js 文件头）。
//
// 为什么用 <meta> + 外部脚本而不是行内 style：CSP 的 style-src 只放行 'self'，
// 行内样式属性会被拦；而 CSSOM 赋值（element.style.fontSize = ...）不算行内样式。
(function () {
  'use strict';

  function inDesktopWindow() {
    // WebView2 运行时注入的宿主对象，普通浏览器（含 Edge）没有这个属性
    return !!(window.chrome && window.chrome.webview);
  }

  function apply() {
    if (!inDesktopWindow()) return;
    var meta = document.querySelector('meta[name="panel-zoom"]');
    var pct = parseInt(meta && meta.content, 10);
    // 区间与 web.py 的 _valid_zoom 保持一致；100% 就是默认值，不必留行内样式
    if (pct >= 70 && pct <= 160 && pct !== 100) {
      document.documentElement.style.fontSize = pct + '%';
    }
  }

  apply();
  // 宿主对象理论上在文档开始前就已注入；万一晚到，DOMContentLoaded 时再补一次
  document.addEventListener('DOMContentLoaded', apply);
})();
