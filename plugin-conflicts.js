/* Plugin: two packages, one GUID.
 *
 * Sideloader loads one package per GUID and skips the rest without saying so,
 * which makes a pack that looks installed the copy that never loads. The
 * manager cannot decide which copy wins, but it can say the collision exists --
 * on the card, where the "why is this mod doing nothing" question starts, and
 * behind one button that lists the packs by their covers.
 *
 * This lives on the Mod grid, not in the dependency report: a collision is a
 * fact about the packages on this page, and it is here that the extra copy gets
 * disabled. The report answers a different question -- which card misses what.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {data, el} = KK;

  /* The catalog is the authority, not deps.json: deps carries only a boolean
   * per package, and a list is more useful when it can name the packs that
   * share the GUID. Walking 7 000 records once beats rescanning them for every
   * card on every page. */
  let groupsByGuid = null;
  let knownLength = -1;

  function byGuid() {
    if (groupsByGuid && knownLength === data.catalog.length) return groupsByGuid;
    groupsByGuid = new Map();
    for (const record of data.catalog) {
      const guid = (record.guid || '').trim().toLowerCase();
      if (!guid) continue;
      const rows = groupsByGuid.get(guid);
      if (rows) rows.push(record);
      else groupsByGuid.set(guid, [record]);
    }
    // Disabling or archiving a package is the one thing that can end a
    // collision, and it shortens the catalog, so the length notices it.
    knownLength = data.catalog.length;
    return groupsByGuid;
  }

  function sharesGuid(record) {
    const guid = (record.guid || '').trim().toLowerCase();
    if (!guid) return [];
    return (byGuid().get(guid) || []).filter(other => other.path !== record.path);
  }

  function clashCount() {
    let groups = 0, packs = 0;
    for (const rows of byGuid().values()) {
      if (rows.length < 2) continue;
      groups += 1;
      packs += rows.length;
    }
    return {groups, packs};
  }

  /* Biggest pile first, then by path inside a group, so the same clash is in
   * the same place every time the dialog opens. */
  function clashGroups() {
    const rows = [];
    for (const [guid, list] of byGuid()) {
      if (list.length < 2) continue;
      rows.push([guid, list.slice().sort((a, b) => a.path.localeCompare(b.path))]);
    }
    return rows.sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  }

  /* ------------------------------------------------------------- the dialog */

  const dialog = el('dialog');
  dialog.className = 'card-refs';
  dialog.id = 'guidClashDialog';
  dialog.setAttribute('aria-label', '共用同一个 GUID 的包');
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

  function closeDialog() {
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.open = false;
  }
  closeButton.onclick = closeDialog;
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right ||
        event.clientY < box.top || event.clientY > box.bottom) closeDialog();
  });

  /* "Does anything wear this GUID at all?" is the same question the Mod grid
   * answers on every card, so the dialog answers it in the same words. Both
   * copies carry the identical number -- cards name the GUID, not the file --
   * and that is the useful reading: zero means either copy can go, a number
   * means one of them has to stay. */
  const REF_KINDS = [['角色卡', 'cards'], ['服装卡', 'outfits'], ['场景卡', 'scenes']];

  function usageOf(record) {
    const verdict = KK.deps.mod(record.path);
    if (!verdict) return null;
    const parts = [];
    for (const [kind, key] of REF_KINDS) {
      const count = (verdict[key] || []).length;
      if (count) parts.push(`${count} 张${kind}`);
    }
    return parts.length
      ? {text: `被 ${parts.join('、')}引用`, used: true}
      : {text: '未被任何卡片引用', used: false};
  }

  /* Same card shape as the Mod grid, scaled down: a cover is what tells two
   * similarly named packs apart, and a collision is exactly the case where the
   * names are similar. */
  function packCard(record) {
    const card = el('article', undefined, 'library-card');
    const thumb = (record.images || [])[0];
    if (thumb) card.append(KK.media.thumb(thumb.src, record.name || record.file));
    else card.append(el('div', '暂无预览', 'empty'));
    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', record.name || record.file, 'name'),
      el('div', record.path, 'path'),
      el('div', `${(record.bytes / 1048576).toFixed(1)} MB`, 'count'));
    const usage = usageOf(record);
    if (usage) meta.append(el('div', usage.text, usage.used ? 'guid-usage' : 'guid-usage unused'));
    card.append(meta);
    return card;
  }

  /* No argument opens every collision, which is what the toolbar button asks.
   * A GUID narrows it to the group the clicked card belongs to, so the banner
   * answers its own question instead of opening a list to search in. */
  function showClashes(guid) {
    const all = clashGroups();
    const narrow = guid ? all.filter(([key]) => key === guid) : null;
    // A GUID that no longer collides (the user disabled one copy from this very
    // dialog) falls back to the whole list rather than an empty answer.
    const rows = narrow && narrow.length ? narrow : all;
    const packs = rows.reduce((sum, [, list]) => sum + list.length, 0);
    title.textContent = narrow && narrow.length
      ? `GUID 冲突 · ${packs} 个包共用这个 GUID`
      : `GUID 冲突 · ${rows.length} 组共 ${packs} 个包`;
    body.replaceChildren();
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.open = true;
    if (!rows.length) {
      body.append(el('p', '现在没有冲突。', 'notice'));
      return;
    }
    body.append(el('p', '同一组的包共用一个 GUID，游戏只会加载其中一个。'
      + '留下哪个由包内的版本号决定，版本相同时按路径长度；停用多余的那个就能消除冲突。',
      'notice'));
    const box = el('div', undefined, 'guid-groups');
    for (const [key, list] of rows) {
      const group = el('section', undefined, 'guid-group');
      group.append(el('h3', key, 'guid-key'));
      for (const record of list) group.append(packCard(record));
      box.append(group);
    }
    body.append(box);
  }

  /* ------------------------------------------------------------- the toolbar */

  /* One door, and only while there is something behind it: on a clean install
   * a permanent "0 组" button would be a control with no answers. */
  KK.registerModTool(() => {
    const clash = clashCount();
    if (!clash.groups) return null;
    const button = el('button', undefined, 'conflict-open');
    button.id = 'showGuidClashes';
    button.type = 'button';
    button.title = '查看哪些包共用一个 GUID';
    button.append(el('span', `GUID 冲突 ${clash.groups} 组（${clash.packs.toLocaleString()} 个包）`),
      el('span', '查看 ›', 'conflict-go'));
    button.onclick = () => showClashes();
    return button;
  });

  /* ---------------------------------------------------------------- cards */

  /* A collision is a fact about the package, not about any filter, and it goes
   * above the package name so it cannot be read as a footnote to it. The whole
   * banner is the way into the list: the card is where the user notices it. */
  function conflictBanner(record) {
    const peers = sharesGuid(record);
    const others = peers.length === 1 ? '另有 1 个包使用同一 GUID'
      : peers.length ? `另有 ${peers.length} 个包使用同一 GUID`
        : '与其他包使用了同一 GUID';
    const banner = el('button',
      `GUID 冲突：${others}，游戏只会加载其中一个 · 查看 ›`, 'conflict-flag');
    banner.type = 'button';
    banner.title = `查看这 ${peers.length + 1} 个包`;
    /* The tail rides inside the sentence instead of being pushed to the right
     * edge: a second flex item left the first line dangling mid-clause on a
     * narrow card. */
    banner.onclick = () => showClashes((record.guid || '').trim().toLowerCase());
    return banner;
  }

  KK.onModCard((record, card, meta) => {
    const verdict = KK.deps.mod(record.path);
    if (sharesGuid(record).length || (verdict && verdict.duplicateGuid)) {
      meta.prepend(conflictBanner(record));
    }
  });
})();
