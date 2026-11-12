/* Plugin: the cards behind a reference count.
 *
 * "被 4 张服装卡引用" answers whether a package is used; the next question is by
 * whom, and answering it used to mean switching views and searching for a path.
 * Two screens print that count -- the Mod grid and the GUID collision list --
 * and a collision is exactly where the answer decides something, so both open
 * this dialog instead of each growing its own.
 *
 * The paths already exist in the dependency snapshot; only the thumbnails have
 * to come from the server, and they are asked for by package path and kind
 * rather than by sending a thousand paths back.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {el} = KK;

  // The key names the kind on the server; the label names it to the user.
  const KIND_KEYS = {'角色卡': 'cards', '服装卡': 'outfits', '场景卡': 'scenes'};

  const dialog = el('dialog');
  dialog.className = 'card-refs';
  dialog.id = 'cardRefsDialog';
  dialog.setAttribute('aria-label', '引用这个 Mod 的卡片');
  const head = el('header');
  const title = el('h2');
  const closeButton = el('button', '×');
  closeButton.type = 'button';
  closeButton.title = '关闭';
  closeButton.setAttribute('aria-label', '关闭');
  head.append(title, closeButton);
  const body = el('div', undefined, 'card-refs-body');
  dialog.append(head, body);
  document.body.append(dialog);

  function close() {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.open = false;
  }
  closeButton.onclick = close;
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right ||
        event.clientY < box.top || event.clientY > box.bottom) close();
  });

  /* A card without a thumbnail still gets a row: the name and the path are
   * enough to find it, and hiding it would make the count a lie. */
  function refRow(row) {
    const card = el('article', undefined, 'library-card');
    if (row.thumb) card.append(KK.media.thumb(row.thumb, row.file));
    else card.append(el('div', '暂无缩略图', 'empty'));
    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', row.file || row.path, 'name'),
      el('div', row.path, 'path'));
    card.append(meta);
    return card;
  }

  function paint(answer) {
    const rows = answer.cards || [];
    body.replaceChildren();
    if (!rows.length) {
      body.append(el('p', '这些卡片已经不在资源卡目录里了。', 'notice'));
      return;
    }
    const list = el('div', undefined, 'card-refs-list');
    for (const row of rows) list.append(refRow(row));
    body.append(list);
    /* The snapshot can name a card the library no longer holds -- it was
     * deleted after the snapshot was taken. The count is still true, so the
     * list says how many it could not draw instead of quietly showing fewer. */
    const total = answer.total || rows.length;
    if (rows.length < total) {
      body.append(el('p', `另有 ${(total - rows.length).toLocaleString()} 张卡片不在资源卡目录里。`,
        'notice'));
    }
  }

  let request = 0;

  /* Two counts pressed in a row must not let the first answer paint over the
   * second list; only the latest request is allowed to fill the dialog. */
  function show(record, kind, count) {
    const ticket = ++request;
    title.textContent = `被 ${count} 张${kind}引用 · ${record.name || record.path}`;
    body.replaceChildren();
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.open = true;
    body.append(el('p', '正在读取卡片…', 'notice'));
    const endpoint = `/api/library/cards?mod=${encodeURIComponent(record.path)}`
      + `&kind=${KIND_KEYS[kind]}`;
    return KK.api.read(endpoint)
      .then(answer => { if (ticket === request) paint(answer); })
      .catch(error => {
        if (ticket !== request) return;
        body.replaceChildren(el('p', '卡片读取失败：' + error.message, 'notice'));
      });
  }

  KK.refs = {show};
})();
