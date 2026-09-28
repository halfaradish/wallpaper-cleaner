// 面板 token 与版本号：由 web.py 注入 <meta>，每次请求现替换
//
// 以前这两项写在 index.html 的内联 <script> 里，那会逼着 CSP 放行 unsafe-inline，
// 等于留着「注入脚本也能读到 token」这条路。改成 <meta> 后 script-src 只用 'self'。
// 页面本身没有任何内联脚本，所以严格 CSP 成立。
const tokenMeta = document.querySelector('meta[name="panel-token"]');
const versionMeta = document.querySelector('meta[name="panel-version"]');

export const PANEL_TOKEN = tokenMeta ? tokenMeta.content : '';

let version = versionMeta ? versionMeta.content : '';

export function getVersion() {
  return version || '未知';
}

// /api/state 也带版本号：程序升级后旧页面还开着时，以服务端为准
export function setVersion(value) {
  if (value) version = value;
}
