// 放大看预览图（GIF 在这里也是动的）
import { $, show, hide, isHidden } from '../core/dom.js';

export function openLightbox(src) {
  $('lightbox-img').src = src;
  show($('lightbox'));
}

export function closeLightbox() {
  const box = $('lightbox');
  // 已经关掉就别再动 src：重绘时把 src 摘掉会让正在解码的图白跑一趟
  if (isHidden(box)) return;
  hide(box);
  $('lightbox-img').removeAttribute('src');
}

export function initLightbox() {
  document.addEventListener('click', (e) => {
    const target = e.target;
    // 缩略图外面套了 <button>（键盘可达），鼠标点图会冒泡到它，所以只认这一个入口
    const trigger = target && target.closest ? target.closest('[data-thumb-src]') : null;
    if (trigger) {
      openLightbox(trigger.dataset.thumbSrc);
    } else if (target === $('lightbox') || target === $('lightbox-img')) {
      closeLightbox();
    }
  });
}
