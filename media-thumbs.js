/* Thumbnail loader: only decode what is about to be seen, drop blank frames.
 *
 * Package previews are the extracted originals (or decoded ThumbTex textures),
 * and a fair number of archives ship a 1x1 or fully transparent placeholder.
 * Showing those as a card cover wastes the most valuable space on screen, so a
 * loaded image is sampled once and blank ones are removed from the grid.
 */
(() => {
  'use strict';

  const blank = new Set();
  const pending = [];
  let scheduled = false;
  const LIMIT = 6;

  const observer = 'IntersectionObserver' in window
    ? new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        pending.push(entry.target);
      }
      schedule();
    }, {rootMargin: '600px 0px'})
    : null;

  function schedule() {
    if (scheduled || !pending.length) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      for (let i = 0; i < LIMIT && pending.length; i++) {
        const img = pending.shift();
        if (img.dataset.src) img.src = img.dataset.src;
      }
      if (pending.length) schedule();
    });
  }

  function isBlank(img) {
    try {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 16;
      const context = canvas.getContext('2d', {willReadFrequently: true});
      context.drawImage(img, 0, 0, 16, 16);
      const pixels = context.getImageData(0, 0, 16, 16).data;
      let visible = 0, min = 255, max = 0;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3] < 8) continue;
        visible++;
        const light = (pixels[i] + pixels[i + 1] + pixels[i + 2]) / 3;
        if (light < min) min = light;
        if (light > max) max = light;
      }
      return visible === 0 || (visible > 16 * 16 * 0.99 && max - min < 1.5);
    } catch (_) {
      // Cross-origin or tainted canvas in file:// mode: keep the image.
      return false;
    }
  }

  function image(src, alt) {
    const node = document.createElement('img');
    node.alt = alt || '';
    node.title = alt || '';
    node.loading = 'lazy';
    node.decoding = 'async';
    node.dataset.src = src;
    if (blank.has(src)) node.dataset.blank = '1';
    if (observer) observer.observe(node);
    else pending.push(node);
    schedule();
    return node;
  }

  /* Build the preview grid at the top of a card. The placeholder stays hidden
   * until every requested image turns out to be blank or missing. */
  function attach(card, images, emptyText) {
    const placeholder = document.createElement('div');
    placeholder.className = 'empty';
    placeholder.textContent = emptyText;
    placeholder.hidden = Boolean(images.length);
    card.prepend(placeholder);
    if (!images.length) return placeholder;

    const box = document.createElement('div');
    box.className = 'images';
    const anchors = images.map(entry => {
      const anchor = document.createElement('a');
      anchor.href = entry.src;
      anchor.target = '_blank';
      anchor.rel = 'noopener';
      const node = image(entry.src, entry.name);
      if (node.dataset.blank) anchor.hidden = true;
      else {
        node.addEventListener('load', () => {
          if (!isBlank(node)) return;
          blank.add(entry.src);
          anchor.hidden = true;
          settle();
        });
        node.addEventListener('error', () => {
          anchor.hidden = true;
          settle();
        });
      }
      anchor.append(node);
      box.append(anchor);
      return anchor;
    });
    card.prepend(box);

    function settle() {
      if (anchors.some(anchor => !anchor.hidden)) return;
      box.hidden = true;
      placeholder.hidden = false;
    }
    if (anchors.every(anchor => anchor.hidden)) settle();
    return placeholder;
  }

  function thumb(src, alt) {
    const node = image(src, alt);
    node.addEventListener('error', () => { node.hidden = true; });
    return node;
  }

  const api = window.KK || (window.KK = {});
  api.media = {attach, thumb, image, isBlank};
})();
