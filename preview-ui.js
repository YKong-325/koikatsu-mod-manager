(() => {
  const viewer = document.createElement('dialog');
  viewer.className = 'image-viewer';
  viewer.setAttribute('aria-label', '图片预览');
  const heading = document.createElement('header');
  const title = document.createElement('h2');
  const close = document.createElement('button');
  close.textContent = '×'; close.title = '关闭预览';
  close.setAttribute('aria-label', '关闭预览');
  heading.append(title, close);
  const image = document.createElement('img');
  const nav = document.createElement('nav');
  const previous = document.createElement('button');
  previous.textContent = '←'; previous.title = '上一张';
  previous.setAttribute('aria-label', '上一张预览');
  const count = document.createElement('span');
  const next = document.createElement('button');
  next.textContent = '→'; next.title = '下一张';
  next.setAttribute('aria-label', '下一张预览');
  nav.append(previous, count, next); viewer.append(heading, image, nav);
  document.body.append(viewer);
  let images = [], current = 0;
  function show() {
    const entry = images[current];
    image.src = entry.src; image.alt = title.textContent = entry.name;
    count.textContent = `${current + 1} / ${images.length}`;
    previous.disabled = current === 0; next.disabled = current === images.length - 1;
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('.images a');
    const cardImage = event.target.closest('.library-card > img');
    if (!link && !cardImage) return;
    event.preventDefault();
    const target = link || cardImage;
    const siblings = link ? [...link.parentElement.querySelectorAll('a')] : [cardImage];
    images = siblings.map(item => ({
      src: item.href || item.src, name: item.querySelector('img')?.alt || item.alt || '预览'
    }));
    current = siblings.indexOf(target); show(); viewer.showModal();
  });
  close.onclick = () => viewer.close();
  previous.onclick = () => { if (current > 0) { current--; show(); } };
  next.onclick = () => { if (current + 1 < images.length) { current++; show(); } };
  viewer.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft') { event.preventDefault(); previous.onclick(); }
    if (event.key === 'ArrowRight') { event.preventDefault(); next.onclick(); }
  });
  viewer.addEventListener('click', event => {
    if (event.target !== viewer) return;
    const box = viewer.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right ||
        event.clientY < box.top || event.clientY > box.bottom) viewer.close();
  });
  viewer.addEventListener('close', () => { image.removeAttribute('src'); images = []; });

  /* Hover zoom. A card shows up to six covers at roughly 105 px, which is not
   * enough to tell two recolours apart, so hovering floats the picture at its
   * own resolution next to the cursor. The panel takes no pointer events: the
   * click still belongs to the card, and leaving the picture closes it. */
  const tip = document.createElement('div');
  tip.className = 'zoom-tip';
  tip.hidden = true;
  const tipPicture = document.createElement('img');
  tipPicture.alt = '';
  tip.append(tipPicture);
  document.body.append(tip);

  let timer = 0;
  let anchor = null;
  let pointer = {x: 0, y: 0};
  const DELAY = 180;      // long enough to skip a card the mouse is just crossing
  const GAP = 12;
  const MARGIN = 10;
  const FRAME = 540;      // the panel is a fixed frame, the picture scales into it
  const UPSCALE = 6;      // a 16 px icon blown up 30x is noise, not a preview

  function cover(node) {
    if (!node || !node.closest) return null;
    const link = node.closest('.images a');
    if (link) return link.hidden ? null : link;
    return node.closest('.library-card > img');
  }

  function hide() {
    clearTimeout(timer);
    timer = 0;
    anchor = null;
    tip.hidden = true;
    tipPicture.removeAttribute('src');
  }

  /* Most package previews in this library are only 128 px square, i.e. no
   * bigger than the card cell they sit in, so drawing them at their natural
   * size defeats the point of hovering. Every picture is scaled into the same
   * frame instead; the upscale is capped so tiny icons do not turn to mush. */
  function fit() {
    const width = tipPicture.naturalWidth, height = tipPicture.naturalHeight;
    if (!width || !height) return;
    const wide = Math.min(FRAME, window.innerWidth * 0.5);
    const high = Math.min(FRAME, window.innerHeight * 0.62);
    const scale = Math.min(wide / width, high / height, UPSCALE);
    tipPicture.style.width = Math.round(width * scale) + 'px';
    tipPicture.style.height = Math.round(height * scale) + 'px';
  }

  function place() {
    if (!anchor || tip.hidden) return;
    fit();
    const box = anchor.getBoundingClientRect();
    const panel = tip.getBoundingClientRect();
    const width = window.innerWidth, height = window.innerHeight;
    let left = box.right + GAP;
    if (left + panel.width > width - MARGIN) left = box.left - GAP - panel.width;
    let top = box.top + (box.height - panel.height) / 2;
    tip.style.left = Math.round(Math.min(Math.max(left, MARGIN),
      Math.max(MARGIN, width - panel.width - MARGIN))) + 'px';
    tip.style.top = Math.round(Math.min(Math.max(top, MARGIN),
      Math.max(MARGIN, height - panel.height - MARGIN))) + 'px';
  }

  function show(node) {
    const source = node.getAttribute('href') || node.getAttribute('src');
    if (!source) return;
    tipPicture.src = source;
    tip.hidden = false;
    // A cached picture is already complete: no load event is coming.
    if (tipPicture.complete) place();
    tipPicture.onload = place;
    requestAnimationFrame(place);
  }

  document.addEventListener('mouseover', event => {
    const node = cover(event.target);
    if (!node || node === anchor) return;
    clearTimeout(timer);
    anchor = node;
    pointer = {x: event.clientX || 0, y: event.clientY || 0};
    timer = setTimeout(() => show(node), DELAY);
  });
  document.addEventListener('mouseout', event => {
    const node = cover(event.target);
    if (!node || cover(event.relatedTarget) === node) return;
    hide();
  });
  document.addEventListener('click', hide);
  window.addEventListener('scroll', hide, true);
  window.addEventListener('blur', hide);
  /* A resize moves every cover, so the panel would sit next to the old place.
     It stays only when the pointer is still over the same picture. */
  window.addEventListener('resize', () => {
    if (!anchor) return;
    const under = document.elementFromPoint(pointer.x, pointer.y);
    if (under && (under === anchor || anchor.contains(under))) place();
    else hide();
  });
})();
