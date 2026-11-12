/* Archived mods: the folder a package lands in when it should stay, but stay
 * out of the way.
 *
 * Disabling renames a mod in place, which is enough for "the game must not load
 * this" and useless for "I want a copy that is clearly not installed". Archiving
 * moves the file, with its path relative to the mods folder, into a directory
 * the game never scans, and writes archive-log.jsonl there. This view lists what
 * that log knows about and moves a selection back to where it came from.
 *
 * Nothing here erases a file: the point of the feature is a backup, so the only
 * destructive action stays on the 已删除 page where it already lives.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {el, api, setStatus, preserveViewport, data} = KK;

  let items = [];
  let selected = new Set();
  let busy = false;
  let loading = false;
  let root = '';

  const controls = el('div', undefined, 'bulk');
  controls.id = 'archiveControls';

  const count = el('span', '', 'bulk-count');
  count.id = 'archiveCount';

  const selectAll = el('button', '全选可取回', 'bulk-action');
  selectAll.id = 'archiveSelectAll';
  selectAll.type = 'button';

  const restoreButton = el('button', '取回选中', 'bulk-action');
  restoreButton.id = 'archiveRestore';
  restoreButton.type = 'button';

  const refreshButton = el('button', '刷新', 'bulk-action');
  refreshButton.id = 'archiveRefresh';
  refreshButton.type = 'button';

  controls.append(count, selectAll, restoreButton, refreshButton);

  const element = el('div');
  element.id = 'archiveList';

  const help = el('p', '', 'notice trash-help');
  element.append(help);

  const list = el('div', '', 'trash-rows');
  element.append(list);

  const canRestore = item => item.available && !item.restored
    && !item.otherRoot && !item.conflict;

  function megabytes(bytes) {
    if (!bytes) return '—';
    return (Math.round(bytes / 10485.76) / 100) + ' MB';
  }

  function stamp(text) {
    return String(text || '').replace('T', ' ').slice(0, 16);
  }

  function reason(item) {
    if (item.restored) return ['已取回', 'done'];
    if (item.otherRoot) return ['归档或 Mod 目录与当时不同，请先切回原目录', 'blocked'];
    if (!item.available) return ['归档文件已不在（可能被手动移走）', 'blocked'];
    if (item.conflict) return ['原位置已有同名文件，请先处理该文件', 'blocked'];
    return ['可取回', 'ready'];
  }

  /* The folder is a setting, so the sentence naming it is rewritten with the
     list: a stale path would be worse than no sentence at all. */
  function renderHelp() {
    const where = root ? `「${root}」` : '归档文件夹';
    help.textContent = `归档的包都在${where}里，游戏不会加载它们。`
      + '点「取回」就放回原来的位置。';
  }

  function updateChrome() {
    const usable = items.filter(canRestore);
    const chosen = usable.filter(item => selected.has(item.id));
    const parts = [usable.length ? `可取回 ${usable.length} 个`
      : (items.length ? '没有可取回的归档' : '还没有任何归档')];
    if (usable.length) parts.push(`已选 ${chosen.length}`);
    count.textContent = parts.join(' · ');
    selectAll.disabled = !usable.length || busy;
    selectAll.textContent = chosen.length && chosen.length === usable.length
      ? '取消全选' : '全选可取回';
    restoreButton.disabled = !chosen.length || busy;
    restoreButton.textContent = chosen.length ? `取回选中（${chosen.length}）` : '取回选中';
    refreshButton.disabled = busy || loading;
  }

  function preview(item) {
    // Same picture the index already had: the archive keeps the relative path,
    // so the preview key the last build wrote still matches.
    const box = el('div', '', 'trash-thumb');
    const picture = KK.media
      ? KK.media.thumb('/api/archive-thumb?id=' + encodeURIComponent(item.id), item.file)
      : null;
    if (!picture) {
      box.classList.add('empty');
      return box;
    }
    picture.addEventListener('error', () => {
      picture.hidden = true;
      box.classList.add('empty');
    });
    box.append(picture);
    return box;
  }

  function renderRows() {
    list.replaceChildren();
    renderHelp();
    if (!items.length) {
      list.append(el('p', '还没有任何归档。在 Mod 页点卡片右上角的归档按钮，或勾选后点「归档选中」。',
        'notice'));
      updateChrome();
      return;
    }
    for (const item of items) {
      const row = el('div', '', 'trash-row archive-row');
      row.dataset.id = item.id;
      if (selected.has(item.id)) row.classList.add('selected');

      const box = el('input');
      box.type = 'checkbox';
      box.className = 'trash-pick';
      box.checked = selected.has(item.id);
      box.disabled = !canRestore(item) || busy;
      box.setAttribute('aria-label', '选择 ' + item.file);
      box.onchange = () => {
        if (box.checked) selected.add(item.id); else selected.delete(item.id);
        row.classList.toggle('selected', box.checked);
        updateChrome();
      };

      const pick = el('label', '', 'trash-pick-box');
      pick.append(box);

      const main = el('div', '', 'trash-main');
      main.append(el('div', item.file || item.source, 'trash-file'));
      const meta = el('div', '', 'trash-meta');
      meta.append(el('span', stamp(item.time)));
      if (item.author) meta.append(el('span', item.author));
      meta.append(el('span', megabytes(item.bytes)));
      main.append(meta);
      main.append(el('div', item.source, 'trash-path'));

      const [label, tone] = reason(item);
      const state = el('div', label, 'trash-state ' + tone);

      const single = el('button', '取回', 'bulk-action trash-one');
      single.type = 'button';
      single.disabled = !canRestore(item) || busy;
      single.onclick = () => restore(item.id);

      row.append(pick, preview(item), main, state, single);
      list.append(row);
    }
    updateChrome();
  }

  async function load() {
    if (loading) return;
    loading = true;
    updateChrome();
    try {
      const payload = await api.read('/api/archives');
      items = Array.isArray(payload.items) ? payload.items : [];
      root = payload.archiveRoot || '';
      const live = new Set(items.filter(canRestore).map(item => item.id));
      selected = new Set([...selected].filter(id => live.has(id)));
      renderRows();
    } catch (error) {
      items = [];
      list.replaceChildren(el('p', '读取归档记录失败：' + error.message, 'notice'));
      updateChrome();
    } finally {
      loading = false;
      updateChrome();
    }
  }

  async function restore(ids) {
    const wanted = (Array.isArray(ids) ? ids : [ids])
      .filter(id => items.some(item => item.id === id && canRestore(item)));
    if (!wanted.length || busy) return;
    busy = true;
    selected = new Set();
    renderRows();
    setStatus(`正在取回 ${wanted.length} 个 Mod · 写入索引…`);
    try {
      const result = await api.mutate('/api/unarchive', {ids: wanted});
      const back = result.records || [];
      for (const record of back) {
        if (!data.catalog.some(existing => existing.path === record.path)) {
          data.catalog.push(record);
        }
      }
      const failures = result.failures || [];
      await KK.deps.reload();
      busy = false;
      await load();
      preserveViewport(() => {
        setStatus(`已取回 ${back.length} 个 Mod` +
          (failures.length ? ` · ${failures.length} 个未能取回` : '') +
          (result.warning ? ' · ' + result.warning : ''));
      });
      return;
    } catch (error) {
      setStatus('取回失败：' + error.message);
    } finally {
      busy = false;
      renderRows();
    }
  }

  selectAll.onclick = () => {
    const usable = items.filter(canRestore);
    const all = usable.every(item => selected.has(item.id));
    selected = all ? new Set() : new Set(usable.map(item => item.id));
    renderRows();
  };
  restoreButton.onclick = () => restore([...selected]);
  refreshButton.onclick = () => { load(); };

  KK.registerView({
    id: 'archive',
    label: '归档',
    dock: controls,
    element,
    render() {
      renderRows();
      load();
    }
  });
})();
