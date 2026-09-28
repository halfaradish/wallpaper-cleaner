// 图片灯箱：点缩略图放大看原图
//
// 用委托而不是逐行绑定：表格每次重绘都会换掉全部行，逐行绑定要么漏要么重。
import { $, hide, show } from '../core/dom.js';

export function openLightbox(src) {
  const box = $('lightbox');
  const img = $('lightbox-img');
  if (!box || !img) return;
  img.src = src;
  show(box);
}

export function closeLightbox() {
  const box = $('lightbox');
  const img = $('lightbox-img');
  if (!box || box.classList.contains('hidden')) return;
  hide(box);
  // 清掉 src，否则 gif 会在隐藏后继续解码播放
  if (img) img.removeAttribute('src');
}

export function isLightboxOpen() {
  const box = $('lightbox');
  return !!box && !box.classList.contains('hidden');
}

export function initLightbox() {
  const box = $('lightbox');
  if (!box) return;

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-thumb-src]');
    if (trigger) {
      openLightbox(trigger.dataset.thumbSrc);
      return;
    }
    // 点遮罩或图片本身都关：这里只有一张图，没有别的可点的地方
    if (event.target === box || event.target.id === 'lightbox-img') closeLightbox();
  });
}
