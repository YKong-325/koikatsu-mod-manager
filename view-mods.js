/* Mod grid: browse packages, edit classification, mark for review, disable.
 *
 * This is the resident view. It owns its own filter state; plugins add filters
 * through KK.registerModFacet and decorate cards through KK.onModCard, so no
 * plugin ever reassigns filter() or render() behind its back.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {data, el, search, setStatus, preserveViewport, api} = KK;
  const records = data.catalog;
  const SIZE = 36;

  /* Selection is one concept, not two: the same set feeds the review list and
   * the batch disable action. Two overlapping selections would be a way to
   * delete something the user only meant to mark. */
  let selected = new Set();
  try { selected = new Set(JSON.parse(localStorage.getItem('kk-mod-review-v1') || '[]')); } catch (_) {}
  // One entry per disable action, so undoing a batch of twenty brings back
  // twenty packages rather than only the last one.
  let undoBatches = [];
  try {
    const stored = JSON.parse(sessionStorage.getItem('kk-mod-undo') || '[]');
    undoBatches = stored.map(entry => Array.isArray(entry) ? entry : [entry])
      .filter(batch => batch.length);
  } catch (_) {}

  let filtered = [], page = 0, pending = false, rendering = false;

  const controls = el('div');
  controls.id = 'modControls';

  const modName = el('input');
  modName.id = 'modName'; modName.type = 'search';
  modName.placeholder = '搜索 Mod 名称 / 压缩包文件名';
  modName.setAttribute('aria-label', '搜索 Mod 名称');

  const query = el('input');
  query.id = 'q'; query.type = 'search';
  query.placeholder = '综合搜索：路径 / 作者 / GUID / 物品名';
  query.setAttribute('aria-label', '搜索');

  const facets = el('div', undefined, 'mod-facets');
  /* The author is the one dimension worth its own field: work and character
   * names are spelled differently in almost every package, so those two filters
   * mostly repeated the general search (and, while no package is labelled, they
   * literally searched the same text). */
  const authorInput = el('input');
  authorInput.type = 'search';
  authorInput.id = 'modAuthor';
  authorInput.placeholder = '作者关键词';
  authorInput.setAttribute('aria-label', '作者关键词');
  authorInput.setAttribute('autocomplete', 'off');
  authorInput.title = '按包内作者或标注作者筛选';
  authorInput.oninput = () => schedule();
  const authorList = el('datalist');
  authorList.id = 'modAuthorOptions';
  authorInput.setAttribute('list', authorList.id);
  const reset = el('button', '\u21ba');
  reset.id = 'resetModFacets';
  reset.type = 'button';
  reset.title = '清空作者筛选';
  reset.setAttribute('aria-label', '清空作者筛选');
  reset.onclick = () => {
    authorInput.value = '';
    apply();
  };
  facets.append(authorInput, authorList, reset);

  const slot = el('select');
  slot.id = 'slot';
  slot.setAttribute('aria-label', '分类');
  // The empty value matters: an option without one reports its own text as the
  // value, which would filter every mod out.
  const allSlots = el('option', '全部分类');
  allSlots.value = '';
  slot.append(allSlots);

  const image = el('select');
  image.id = 'image';
  image.setAttribute('aria-label', '预览状态');
  [['', '全部预览状态'], ['yes', '有原始预览图'], ['no', '暂无预览图'],
    ['bundle', '有缩略图资源包'], ['empty', '未解析出条目']]
    .forEach(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      image.append(option);
    });

  const sort = el('select');
  sort.id = 'sort';
  sort.setAttribute('aria-label', '排序');
  [['path', '按路径'], ['size', '按体积降序'], ['items', '按条目数降序']]
    .forEach(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      sort.append(option);
    });

  const pluginFacets = el('div');
  pluginFacets.id = 'modPluginFacets';
  pluginFacets.style.display = 'contents';
  let renderedFacets = -1;

  /* Where a plugin puts a control that opens something rather than filtering
   * (the GUID-collision list). Repainted on every render: what it reports
   * changes as packages leave the catalog. */
  const pluginTools = el('div');
  pluginTools.id = 'modPluginTools';
  pluginTools.style.display = 'contents';

  const exportButton = el('button', '导出待审清单', 'bulk-action');
  exportButton.id = 'export';
  exportButton.type = 'button';
  exportButton.onclick = () => {
    const chosen = records.filter(record => selected.has(record.path)).map(record => ({
      Mod: record.path, Guid: record.guid, ModName: record.name,
      SizeMB: Math.round(record.bytes / 10485.76) / 100
    }));
    const url = URL.createObjectURL(new Blob([JSON.stringify(chosen, null, 2)],
      {type: 'application/json'}));
    const anchor = el('a');
    anchor.href = url; anchor.download = 'mod-review-list.json'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const undoButton = el('button', '撤销删除', 'bulk-action');
  undoButton.id = 'undo';
  undoButton.type = 'button';
  undoButton.onclick = () => restoreLast();

  /* Batch bar. Always visible: it is also the only place that explains how to
   * select, which a drag-rectangle UI needs to say out loud. */
  const bulkBar = el('div', undefined, 'bulk');
  const bulkCount = el('span', undefined, 'bulk-count');
  bulkCount.id = 'bulkCount';
  const pageButton = el('button', '全选本页', 'bulk-action');
  pageButton.id = 'pickPage';
  pageButton.type = 'button';
  pageButton.onclick = () => {
    for (const record of pageRecords()) selected.add(record.path);
    persist();
    syncSelection();
    statusLine();
  };
  const clearButton = el('button', '清空选择', 'bulk-action');
  clearButton.id = 'clearSelection';
  clearButton.type = 'button';
  clearButton.onclick = () => {
    selected.clear();
    persist();
    syncSelection();
    statusLine();
  };
  const deleteButton = el('button', '删除选中', 'bulk-action bulk-delete');
  deleteButton.id = 'deleteSelected';
  deleteButton.type = 'button';
  deleteButton.onclick = () => deleteSelected();
  /* Two different intents, two different buttons. Deleting leaves the file in
   * place under a .disabled name - fine for "the game must not load this" but
   * useless to anyone whose backup is "whatever is in mods". Archiving is the
   * one action here that moves the package out of the mods tree. */
  const archiveButton = el('button', '归档选中', 'bulk-action');
  archiveButton.id = 'archiveSelected';
  archiveButton.type = 'button';
  archiveButton.onclick = () => archiveSelected();
  /* Packages without a preview have nothing to recognise them by, and the only
   * thing left inside them is the mesh. Rendering happens on the server as one
   * background job; the page polls it, then repaints once. */
  const renderButton = el('button', '渲染缩略图', 'bulk-action bulk-render');
  renderButton.id = 'renderPreviews';
  renderButton.type = 'button';
  renderButton.onclick = () => renderPreviews();
  bulkBar.append(bulkCount, pageButton, clearButton, renderButton, archiveButton,
    deleteButton, exportButton, undoButton);

  controls.append(modName, query, facets, slot, image, sort, pluginTools, pluginFacets);

  const element = el('main');
  element.id = 'grid';

  /* --------------------------------------------------------------- helpers */

  function persist() {
    try { localStorage.setItem('kk-mod-review-v1', JSON.stringify([...selected])); } catch (_) {}
  }

  function saveUndo() {
    sessionStorage.setItem('kk-mod-undo', JSON.stringify(undoBatches));
    updateBulk();
  }

  function pageRecords() {
    return filtered.slice(page * SIZE, (page + 1) * SIZE);
  }

  function setSelected(path, on) {
    if (on) selected.add(path);
    else selected.delete(path);
    persist();
    syncSelection();
    statusLine();
  }

  /* Repainting the whole grid after every click would throw away image
   * decoding and scroll position, so selection is synced in place. */
  function syncSelection() {
    for (const card of element.children) {
      const path = card.dataset && card.dataset.path;
      if (path === undefined) continue;
      const on = selected.has(path);
      card.classList.toggle('selected', on);
      card.setAttribute('aria-selected', on ? 'true' : 'false');
      const check = card.querySelector('.pick input');
      if (check) check.checked = on;
    }
    updateBulk();
  }

  function updateBulk() {
    const count = selected.size;
    bulkCount.textContent = count
      ? `已选 ${count.toLocaleString()} 个`
      : '未选择 · 勾选缩略图左上角的方框，或点卡片、按住左键拖框';
    deleteButton.textContent = count ? `删除选中（${count.toLocaleString()}）` : '删除选中';
    deleteButton.disabled = !count || location.protocol !== 'http:' || pending || rendering;
    archiveButton.textContent = count ? `归档选中（${count.toLocaleString()}）` : '归档选中';
    archiveButton.title = '把选中的 Mod 移动到 Mod 目录之外的备份文件夹';
    archiveButton.disabled = !count || location.protocol !== 'http:' || pending || rendering;
    renderButton.textContent = rendering ? '取消渲染'
      : (count ? `渲染缩略图（${count.toLocaleString()}）` : '渲染缩略图');
    renderButton.title = count
      ? '为选中的 Mod 渲染缩略图'
      : '为所有没有预览图的 Mod 渲染缩略图';
    renderButton.disabled = pending || location.protocol !== 'http:';
    clearButton.disabled = !count;
    pageButton.disabled = !filtered.length;
    exportButton.disabled = !count;
    const batch = undoBatches[undoBatches.length - 1];
    undoButton.textContent = batch ? `撤销删除（${batch.length}）` : '撤销删除';
    undoButton.disabled = !batch || pending;
  }

  function facetQueries() {
    return {author: authorInput.value};
  }

  function updateFacetOptions() {
    const options = search.options(records);
    authorList.replaceChildren(...options.map(label => {
      const option = el('option');
      option.value = label;
      return option;
    }));
  }

  function statusLine(extra) {
    const line = `${records.length.toLocaleString()} 个包 · ${filtered.length.toLocaleString()} 个匹配` +
      ` · ${selected.size} 个已选`;
    setStatus(extra ? line + ' · ' + extra : line);
  }

  let timer;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(apply, 160);
  }

  /* ---------------------------------------------------------------- filter */

  function apply(options = {}) {
    const name = modName.value, all = query.value;
    const slotValue = slot.value, imageValue = image.value, queries = facetQueries();
    const plugin = KK.facets.map(facet => [facet, KK.facetValue(facet.id)]);

    filtered = records.filter(record => {
      if (!search.contains(search.entry(record).name, name)) return false;
      if (!search.contains(search.entry(record).all, all)) return false;
      if (!search.matches(record, queries)) return false;
      if (slotValue && !record.slots.includes(slotValue)) return false;
      if (imageValue) {
        const images = record.images || [];
        const has = (imageValue === 'yes' && images.length) ||
          (imageValue === 'no' && !images.length) ||
          (imageValue === 'bundle' && record.thumbBundles.length) ||
          (imageValue === 'empty' && !record.items.length);
        if (!has) return false;
      }
      for (const [facet, value] of plugin) {
        if (value && !facet.match(record, value)) return false;
      }
      return true;
    });

    const order = sort.value;
    filtered.sort(order === 'size' ? (a, b) => b.bytes - a.bytes
      : order === 'items' ? (a, b) => b.items.length - a.items.length
        : (a, b) => a.path.localeCompare(b.path));
    if (!options.keepPage) page = 0;
    updateFacetOptions();
    render();
  }

  /* ----------------------------------------------------------------- cards */

  function classificationEditor(record) {
    const box = el('details');
    box.append(el('summary', '编辑作品 / 角色 / 作者'));
    box.addEventListener('toggle', () => {
      if (!box.open || box.dataset.loaded) return;
      box.dataset.loaded = '1';
      const form = el('form', undefined, 'mod-classification-form');
      const fields = {};
      for (const [key, title] of Object.entries(
        {work: '作品', character: '角色名字', author: '作者（留空使用包内作者）'})) {
        const label = el('label', title);
        const input = el('input');
        input.type = 'text'; input.maxLength = 120;
        input.value = (record.classification || {})[key] || '';
        input.setAttribute('aria-label', title);
        label.append(input); form.append(label); fields[key] = input;
      }
      const save = el('button', '保存分类');
      save.type = 'submit';
      save.disabled = location.protocol !== 'http:';
      const message = el('span', undefined, 'count');
      form.append(save, message);
      form.onsubmit = async event => {
        event.preventDefault();
        save.disabled = true;
        try {
          const result = await api.mutate('/api/mod-classification', {
            path: record.path,
            classification: Object.fromEntries(
              Object.entries(fields).map(([key, input]) => [key, input.value]))
          });
          record.classification = result.classification;
          preserveViewport(() => { apply({keepPage: true}); statusLine('分类已保存'); });
        } catch (error) {
          message.textContent = error.message;
          save.disabled = false;
        }
      };
      box.append(form);
    });
    return box;
  }

  function itemDetails(record) {
    const box = el('details');
    box.append(el('summary', '物品明细'));
    box.addEventListener('toggle', () => {
      if (!box.open || box.dataset.loaded) return;
      box.dataset.loaded = '1';
      box.append(el('div', 'GUID: ' + (record.guid || '未标注'), 'path'));
      const list = el('ul');
      record.items.forEach(item => list.append(el('li', item.Slot + ' · ' + item.Name)));
      box.append(list);
      if (!record.items.length) box.append(el('div', '未解析出清单条目', 'path'));
    });
    return box;
  }

  /* A box on the cover, not a word at the bottom of the card: sweeping a
   * rectangle and clicking a card both work, but neither says so on screen.
   * The visible checkbox is what makes a batch action discoverable. */
  function selectionBadge(record) {
    const label = el('label', undefined, 'pick');
    label.title = '勾选加入批量操作（也可直接点击卡片或拖框）';
    const check = el('input');
    check.type = 'checkbox';
    check.checked = selected.has(record.path);
    check.setAttribute('aria-label', '选择 ' + record.file);
    check.onchange = () => setSelected(record.path, check.checked);
    label.append(check);
    return label;
  }

  /* Both card actions are icon-only: the glyph is an SVG mask painted in the
   * button's own colour (see .delete::before / .archive::before), so the label
   * lives in aria-label instead of the text node. A busy button swaps the icon
   * for an ellipsis, which is why it carries a class rather than just text. */
  function markBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    button.classList.toggle('busy', busy);
    button.textContent = busy ? '\u2026' : '';
  }

  function disableButton(record) {
    const button = el('button', '', 'delete');
    button.title = '停用此 Mod（原目录修改后缀）';
    button.setAttribute('aria-label', '停用此 Mod：' + record.file);
    button.disabled = location.protocol !== 'http:';
    button.onclick = () => disableMods([record], button);
    return button;
  }

  /* Archive, not disable: the file leaves the mods folder entirely and lands in
   * the backup directory as the same relative path, which is what makes it safe
   * to hand that folder to anyone or copy it to another drive. */
  function archiveButtonFor(record) {
    const button = el('button', '', 'archive');
    button.title = '归档此 Mod（移动到 Mod 目录之外的备份文件夹）';
    button.setAttribute('aria-label', '归档此 Mod：' + record.file);
    button.disabled = location.protocol !== 'http:';
    button.onclick = () => archiveMods([record], button);
    return button;
  }

  /* Why a package has no cover is worth saying: "author never packed a
   * thumbnail" and "we could not decode it" look identical on screen. */
  function emptyLabel(record) {
    if (!(record.items || []).length) return '无预览图（包内没有物品）';
    if ((record.missingThumbBundles || []).length) return '作者未打包缩略图';
    if ((record.thumbBundles || []).length) return '缩略图资源待解码';
    if ((record.items || []).some(item => (item.ThumbTex || '').trim())) {
      return '缩略图资源待解码';
    }
    return '未声明缩略图';
  }

  function buildCard(record) {
    const card = el('article');
    card.dataset.path = record.path;
    const isSelected = selected.has(record.path);
    card.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    if (isSelected) card.classList.add('selected');
    const meta = el('div', undefined, 'meta');
    card.append(meta);
    const labels = search.entry(record).labels;
    meta.append(el('div', record.name || record.file, 'name'),
      el('div', record.path, 'path'),
      el('div', labels.author || '作者未标注', 'path'));
    if (labels.work || labels.character) {
      meta.append(el('div', [labels.work, labels.character].filter(Boolean).join(' / '),
        'mod-classification'));
    }
    meta.append(classificationEditor(record));
    meta.append(el('div', record.slots.join(' / ') || record.dataDirs.join(' / ') || '未分类', 'tags'));
    meta.append(el('div',
      `${(record.bytes / 1048576).toFixed(1)} MB · ${record.items.length} 条目 · ` +
      `${(record.images || []).length} 张图`, 'count'));

    // A package with no picture of its own stays empty here: borrowing a
    // character card's cover described the card, not the package, and the
    // render job turns those packages into their own picture instead.
    KK.media.attach(card, record.images || [], emptyLabel(record));
    card.append(selectionBadge(record));
    meta.append(itemDetails(record));
    if (record.errors.length) meta.append(el('div', record.errors.join(' | '), 'error'));
    const actions = el('div', undefined, 'actions');
    actions.append(archiveButtonFor(record), disableButton(record));
    meta.append(actions);
    KK.applyModHooks(record, card, meta);
    return card;
  }

  function render() {
    if (renderedFacets !== KK.facets.length) {
      renderedFacets = KK.facets.length;
      KK.renderModFacets(pluginFacets, () => apply());
    }
    KK.renderModTools(pluginTools);
    const pages = Math.max(1, Math.ceil(filtered.length / SIZE));
    page = Math.min(page, pages - 1);
    statusLine();
    updateBulk();
    KK.refreshPager();
    element.replaceChildren();
    for (const record of pageRecords()) {
      element.append(buildCard(record));
    }
  }

  /* -------------------------------------------------------------- selection */

  /* Click a card to toggle it, drag a rectangle starting on a cover to sweep
   * several. A press only becomes a sweep after a few pixels, which is what
   * keeps a plain click usable; a drag that starts on the card text stays a
   * text selection, because copying a path or a name is a normal thing to do. */
  const DRAG = 6;
  const INTERACTIVE = 'button, input, select, textarea, summary, label, details';
  let press = null, marquee = null, suppressClick = false;

  /* The grid is taller than any window, so a sweep that has to reach a card
   * below the fold would otherwise stop at the viewport edge and make the user
   * release, scroll and sweep again. Holding the pointer near an edge scrolls
   * the page for as long as it stays there. */
  const EDGE = 72;
  const EDGE_SPEED = 26;
  let pointerView = {x: 0, y: 0};
  let edgeTimer = null;

  function cardOf(node) {
    const card = node && node.closest ? node.closest('article') : null;
    return card && card.parentElement === element ? card : null;
  }

  function intersects(a, b) {
    return a.right > b.left && a.left < b.right && a.bottom > b.top && a.top < b.bottom;
  }

  /* The rectangle lives in document coordinates, because the page can scroll
   * under the pointer mid-sweep. Reading the offsets through helpers keeps the
   * test shim (which has none of them) working unchanged. */
  function scrollLeft() { return window.scrollX || window.pageXOffset || 0; }
  function scrollTop() { return window.scrollY || window.pageYOffset || 0; }

  function beginMarquee() {
    const box = document.createElement('div');
    box.className = 'marquee';
    document.body.append(box);
    document.body.classList.add('marquee-active');
    marquee = {box, hits: new Set()};
  }

  function updateMarquee(viewX, viewY) {
    if (!marquee) return;
    const sx = scrollLeft(), sy = scrollTop();
    // The two corners are document points: the origin was recorded on press,
    // the moving corner follows the pointer's current position on the page.
    const left = Math.min(press.originX, viewX + sx);
    const top = Math.min(press.originY, viewY + sy);
    const right = Math.max(press.originX, viewX + sx);
    const bottom = Math.max(press.originY, viewY + sy);
    const style = marquee.box.style;
    style.left = (left - sx) + 'px';
    style.top = (top - sy) + 'px';
    style.width = (right - left) + 'px';
    style.height = (bottom - top) + 'px';
    const rect = {left, top, right, bottom};
    const hits = new Set();
    for (const card of element.children) {
      const path = card.dataset && card.dataset.path;
      if (path === undefined) continue;
      const box = card.getBoundingClientRect();
      const boxDoc = {left: box.left + sx, top: box.top + sy,
        right: box.right + sx, bottom: box.bottom + sy};
      if (intersects(boxDoc, rect)) hits.add(path);
      card.classList.toggle('marquee-hit', hits.has(path));
    }
    marquee.hits = hits;
  }

  function edgeStep() {
    if (!marquee) { stopEdgeScroll(); return; }
    const height = window.innerHeight || 0;
    // Nothing to scroll when the window height is unknown (headless shim).
    if (!height) { stopEdgeScroll(); return; }
    const above = pointerView.y;
    const below = height - pointerView.y;
    let delta = 0;
    if (above < EDGE) delta = -edgeSpeed(above);
    else if (below < EDGE) delta = edgeSpeed(below);
    if (!delta) { stopEdgeScroll(); return; }
    const before = scrollTop();
    window.scrollTo(scrollLeft(), before + delta);
    // Already at the top or bottom: keep the timer off until the pointer moves.
    if (scrollTop() === before) { stopEdgeScroll(); return; }
    // The pointer did not move, but the page did, so the sweep has to be redrawn
    // against the new offsets or it would stay glued to the old content.
    updateMarquee(pointerView.x, pointerView.y);
    scheduleEdgeScroll();
  }

  /* The closer to the edge, the faster the page moves: a pointer parked on the
   * border crawls at full speed, one that is just inside the strip nudges. */
  function edgeSpeed(distance) {
    // Clamped at 1: the pointer can leave the window entirely while captured,
    // which must not scroll faster than the top speed.
    const closeness = Math.min(1, Math.max(0, EDGE - distance) / EDGE);
    return Math.max(2, Math.round(EDGE_SPEED * closeness));
  }

  function stopEdgeScroll() {
    if (edgeTimer === null) return;
    clearTimeout(edgeTimer);
    edgeTimer = null;
  }

  function scheduleEdgeScroll() {
    if (edgeTimer !== null) return;
    edgeTimer = setTimeout(() => { edgeTimer = null; edgeStep(); }, 16);
  }

  function startEdgeScroll() {
    scheduleEdgeScroll();
  }

  /* True while the pointer sits in the strip along the top or bottom edge. */
  function nearEdge() {
    const height = window.innerHeight || 0;
    if (!height) return false;
    return pointerView.y < EDGE || height - pointerView.y < EDGE;
  }

  function endMarquee() {
    if (!marquee) return new Set();
    stopEdgeScroll();
    const {box, hits} = marquee;
    marquee = null;
    if (box.remove) box.remove();
    else if (box.parentElement) box.parentElement.children = box.parentElement.children.filter(node => node !== box);
    document.body.classList.remove('marquee-active');
    for (const card of element.children) {
      if (card.classList) card.classList.remove('marquee-hit');
    }
    return hits;
  }

  element.addEventListener('pointerdown', event => {
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.target;
    if (target && target.closest && target.closest(INTERACTIVE)) return;
    /* Dragging is only a sweep when it starts on a picture, a placeholder or
     * the empty grid. Starting on the text block has to stay a text selection,
     * so the browser keeps the gesture. */
    const canSweep = target === element ||
      Boolean(target && target.closest && target.closest('.images, .empty'));
    press = {
      x: event.clientX, y: event.clientY, card: cardOf(target), dragging: false,
      // Fixed content point under the pointer at press time; the rectangle is
      // measured from here so scrolling cannot drag the origin along.
      originX: event.clientX + scrollLeft(), originY: event.clientY + scrollTop(),
      canSweep, moved: false,
      additive: Boolean(event.shiftKey || event.ctrlKey || event.metaKey)
    };
    pointerView = {x: event.clientX, y: event.clientY};
    if (canSweep && element.setPointerCapture && event.pointerId !== undefined) {
      try { element.setPointerCapture(event.pointerId); } catch (_) {}
    }
  });

  element.addEventListener('pointermove', event => {
    if (!press) return;
    pointerView = {x: event.clientX, y: event.clientY};
    if (Math.abs(event.clientX - press.x) < DRAG &&
        Math.abs(event.clientY - press.y) < DRAG) return;
    if (!press.canSweep) {
      // Selecting text: leave the highlight to the browser and remember that
      // this gesture was a drag, so releasing it does not toggle the card.
      press.moved = true;
      return;
    }
    if (!press.dragging) {
      press.dragging = true;
      beginMarquee();
    }
    updateMarquee(event.clientX, event.clientY);
    // Leaving the edge strip stops the run on this move instead of one tick
    // later, so the page does not creep on after the pointer has settled.
    if (nearEdge()) startEdgeScroll();
    else stopEdgeScroll();
    if (event.preventDefault) event.preventDefault();
  });

  element.addEventListener('pointerup', () => {
    if (!press) return;
    const state = press;
    press = null;
    if (state.moved && !state.dragging) return;
    if (state.dragging) {
      const hits = endMarquee();
      if (state.additive) hits.forEach(path => selected.add(path));
      else {
        selected.clear();
        hits.forEach(path => selected.add(path));
      }
      persist();
      syncSelection();
      statusLine(`框选了 ${hits.size} 个 Mod`);
      // A sweep that started on a cover must not also open that cover.
      suppressClick = true;
      setTimeout(() => { suppressClick = false; }, 0);
      return;
    }
    if (state.card) setSelected(state.card.dataset.path, !selected.has(state.card.dataset.path));
  });

  element.addEventListener('pointercancel', () => { press = null; endMarquee(); });

  // A wheel scroll during a sweep moves the grid too; keep the box on the
  // content it started from, and stop the edge timer if the tab loses focus.
  if (window.addEventListener) {
    window.addEventListener('scroll', () => {
      if (marquee) updateMarquee(pointerView.x, pointerView.y);
    }, {passive: true});
    window.addEventListener('blur', stopEdgeScroll);
  }

  // Capture phase: a cover is an <a>, so a sweep ending over one would navigate.
  element.addEventListener('click', event => {
    if (!suppressClick) return;
    suppressClick = false;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  /* ---------------------------------------------------------------- actions */

  /* Disabling rewrites the whole index (about a second), so a selection of
   * twenty is still one request and one write. Doing it per file would take
   * twenty seconds and leave the index half updated if one of them failed. */
  async function disableMods(targets, button) {
    if (pending || !targets.length) return;
    pending = true;
    updateBulk();
    if (button) markBusy(button, true);
    setStatus(targets.length === 1
      ? '正在停用 ' + targets[0].file + ' · 写入索引…'
      : `正在停用 ${targets.length} 个 Mod · 写入索引…`);
    try {
      const result = await api.mutate('/api/delete',
        {paths: targets.map(record => record.path)});
      const removed = new Set(targets.map(record => record.path));
      for (let index = records.length - 1; index >= 0; index--) {
        if (removed.has(records[index].path)) records.splice(index, 1);
      }
      removed.forEach(path => selected.delete(path));
      persist();
      const ids = result.ids || (result.id ? [result.id] : []);
      if (ids.length) { undoBatches.push(ids); saveUndo(); }
      const failures = result.failures || [];
      await KK.deps.reload();
      preserveViewport(() => {
        apply({keepPage: true});
        statusLine(`已停用 ${result.count || ids.length} 个 Mod，可撤销，或到「已删除」页找回` +
          (failures.length ? ` · ${failures.length} 个未能停用` : '') +
          (result.warning ? ' · ' + result.warning : ''));
      });
    } catch (error) {
      setStatus('停用失败：' + error.message);
      if (button) markBusy(button, false);
    } finally {
      pending = false;
      updateBulk();
    }
  }

  /* A batch delete is the one action here that can ruin an afternoon, so it
   * always stops for a confirmation that names the count and the size. */
  async function deleteSelected() {
    if (pending) return;
    const targets = records.filter(record => selected.has(record.path));
    if (!targets.length) {
      setStatus('没有选中任何 Mod');
      return;
    }
    const megabytes = targets.reduce((sum, record) => sum + record.bytes, 0) / 1048576;
    const question = `停用 ${targets.length} 个 Mod（共 ${megabytes.toFixed(1)} MB）？\n\n`
      + '文件会在原目录改名为 .disabled，之后可在「已删除」页恢复。';
    if (!window.confirm(question)) {
      statusLine('已取消');
      return;
    }
    await disableMods(targets);
  }

  /* Archiving is the "keep it, just not installed" action. Nothing is deleted,
   * so the confirmation only has to say where the files are going - and the
   * server answers with the real folder, which the batch bar cannot know. */
  async function archiveMods(targets, button) {
    if (pending || !targets.length) return;
    pending = true;
    updateBulk();
    if (button) markBusy(button, true);
    setStatus(targets.length === 1
      ? '正在归档 ' + targets[0].file + ' · 移动文件…'
      : `正在归档 ${targets.length} 个 Mod · 移动文件…`);
    try {
      const result = await api.mutate('/api/archive',
        {paths: targets.map(record => record.path)});
      const removed = new Set(targets.map(record => record.path));
      for (let index = records.length - 1; index >= 0; index--) {
        if (removed.has(records[index].path)) records.splice(index, 1);
      }
      removed.forEach(path => selected.delete(path));
      persist();
      const failures = result.failures || [];
      await KK.deps.reload();
      preserveViewport(() => {
        apply({keepPage: true});
        statusLine(`已归档 ${result.count || removed.size} 个 Mod 到 ${result.archiveRoot || '归档文件夹'}` +
          (failures.length ? ` · ${failures.length} 个未能归档` : '') +
          (result.warning ? ' · ' + result.warning : ''));
      });
    } catch (error) {
      setStatus('归档失败：' + error.message);
      if (button) markBusy(button, false);
    } finally {
      pending = false;
      updateBulk();
    }
  }

  /* Where archives go is a setting, so the confirmation quotes the real folder
   * instead of saying "some backup folder". One request at boot; the server
   * answers long before anyone presses the button. */
  let archiveRoot = '';
  if (location.protocol === 'http:') {
    KK.sessionReady
      .then(() => fetch('/api/config'))
      .then(response => (response.ok ? response.json() : null))
      .then(payload => { archiveRoot = (payload && payload.values && payload.values.archive) || ''; })
      .catch(() => {});
  }

  async function archiveSelected() {
    if (pending) return;
    const targets = records.filter(record => selected.has(record.path));
    if (!targets.length) {
      setStatus('没有选中任何 Mod');
      return;
    }
    const megabytes = targets.reduce((sum, record) => sum + record.bytes, 0) / 1048576;
    const where = archiveRoot ? `「${archiveRoot}」` : 'Mod 目录之外的备份文件夹';
    const question = `归档 ${targets.length} 个 Mod（共 ${megabytes.toFixed(1)} MB）？\n\n`
      + `文件会被移动到${where}，游戏不再加载，内容不会被修改，`
      + '之后可在「归档」页取回。';
    if (!window.confirm(question)) {
      statusLine('已取消');
      return;
    }
    await archiveMods(targets);
  }

  /* Rendering reads the package's meshes and textures and paints a product-style
   * picture, which is the only way to recognise a package that shipped neither a
   * thumbnail nor a card that uses it. One job at a time, cancellable. */
  async function renderPreviews() {
    if (pending) return;
    if (rendering) {
      try {
        await api.mutate('/api/render-previews/cancel', {});
        setStatus('正在取消渲染…');
      } catch (error) {
        setStatus('取消失败：' + error.message);
      }
      return;
    }
    const targets = selected.size
      ? records.filter(record => selected.has(record.path)).map(record => record.path)
      : records.filter(record => !(record.images || []).length).map(record => record.path);
    if (!targets.length) {
      statusLine('没有需要生成缩略图的 Mod');
      return;
    }
    const question = `为 ${targets.length} 个 Mod 渲染缩略图？\n\n`
      + '读取包内模型与贴图，每个约 1–4 秒，可随时点“取消渲染”。'
      + (selected.size ? '' : '\n（未选中任何 Mod，将对所有没有自己预览图的包执行，'
        + '包括封面是从资源卡借来的那些）');
    if (!window.confirm(question)) {
      statusLine('已取消');
      return;
    }
    rendering = true;
    updateBulk();
    const fromSelection = selected.size > 0;
    try {
      const started = await api.mutate('/api/render-previews', {paths: targets});
      await watchRender(started.total || targets.length, fromSelection ? targets : []);
    } catch (error) {
      statusLine('渲染失败：' + error.message);
    } finally {
      rendering = false;
      updateBulk();
    }
  }

  /* One poll a second: a single render takes seconds, so streaming would only
     add noise. Results are collected and applied in one repaint at the end.
     `rendered` is empty unless the job came from picked cards: once it is over
     the selection has served its purpose, and leaving it set would point the
     next bulk action (delete, most of all) at packages already handled. Only
     the packages this run covered are cleared — anything picked while it was
     running belongs to the next batch. */
  async function watchRender(total, rendered) {
    let cursor = 0, done = 0, failed = 0, applied = 0;
    for (;;) {
      let state;
      try {
        state = await api.read('/api/render-previews?cursor=' + cursor);
      } catch (error) {
        statusLine('渲染中断：' + error.message);
        return;
      }
      for (const update of state.updates || []) {
        const record = records.find(item => item.path === update.path);
        if (record) { record.images = update.images; applied += 1; }
      }
      cursor = state.cursor || cursor;
      done = state.done;
      failed = state.failed;
      if (!state.running) {
        const cleared = rendered.filter(path => selected.has(path));
        if (cleared.length) {
          for (const path of cleared) selected.delete(path);
          persist();
        }
        if (applied || cleared.length) {
          preserveViewport(() => {
            apply({keepPage: true});
            if (cleared.length) syncSelection();
          });
        }
        statusLine(`已生成 ${done - failed} 张缩略图`
          + (failed ? ` · ${failed} 个包无法渲染` : '')
          + (cleared.length ? ` · 已取消选中 ${cleared.length} 个` : '')
          + (state.error ? ' · ' + state.error : ''));
        return;
      }
      setStatus(`正在渲染 ${done}/${state.total || total}` +
        (state.current ? ' · ' + state.current : ''));
      await new Promise(resolve => setTimeout(resolve, 900));
    }
  }

  async function restoreLast() {
    const batch = undoBatches[undoBatches.length - 1];
    if (pending || !batch || !batch.length) return;
    pending = true;
    updateBulk();
    setStatus(`正在恢复 ${batch.length} 个 Mod · 写入索引…`);
    try {
      const result = await api.mutate('/api/restore', {ids: batch});
      undoBatches.pop();
      saveUndo();
      const back = result.records || (result.record ? [result.record] : []);
      for (const record of back) {
        if (!records.some(existing => existing.path === record.path)) records.push(record);
      }
      const failures = result.failures || [];
      await KK.deps.reload();
      preserveViewport(() => {
        apply({keepPage: true});
        statusLine(`已恢复 ${back.length} 个 Mod` +
          (failures.length ? ` · ${failures.length} 个未能恢复` : '') +
          (result.warning ? ' · ' + result.warning : ''));
      });
    } catch (error) {
      setStatus('恢复失败：' + error.message);
    } finally {
      pending = false;
      updateBulk();
    }
  }

  /* ------------------------------------------------------------------ init */

  const slots = [...new Set(records.flatMap(record => record.slots))].sort();
  for (const name of slots) {
    const option = el('option', name);
    option.value = name;
    slot.append(option);
  }

  modName.oninput = schedule;
  query.oninput = schedule;
  [slot, image, sort].forEach(select => { select.onchange = () => apply(); });

  saveUndo();
  const view = KK.registerView({
    id: 'mods',
    label: 'Mod',
    order: 10,
    controls,
    dock: bulkBar,
    element,
    render(params) {
      if (params && params.query !== undefined) {
        query.value = params.query;
        modName.value = '';
        slot.value = '';
        image.value = '';
        KK.facets.forEach(facet => {
          const select = document.querySelector(`select[data-facet="${facet.id}"]`);
          if (select) select.value = '';
        });
        apply();
        return;
      }
      apply({keepPage: true});
    },
    pageInfo() {
      return {current: page, pages: Math.max(1, Math.ceil(filtered.length / SIZE))};
    },
    go(delta) {
      page = Math.max(0, page + delta);
      render();
    }
  });

  window.KK_MOD_VIEW = view;
})();
