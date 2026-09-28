// 界面偏好：主题、导航栏展开状态
//
// 为什么偏好要存在服务端而不是 localStorage：桌面模式每次启动都由系统分配空闲端口，
// 端口变了 origin 就变了，localStorage 里的东西下次启动读不到。所以偏好落在
// prefs.json 里，由 /api/prefs 读写。
import { api } from './api.js';

export async function loadPrefs() {
  try {
    return await api('/api/prefs');
  } catch (e) {
    return null;
  }
}

// 部分更新：只提交要改的键。写失败不算致命（界面已经先变了），
// 但要如实告诉用户"这次改动下次启动不会保留"。
export async function savePrefs(patch) {
  try {
    await api('/api/prefs', { body: patch });
    return null;
  } catch (e) {
    return e.message;
  }
}
