/* Deleted mods: the durable counterpart of the undo button.
 *
 * Disabling a mod renames the archive in place to
 * "<name>.modmanager-<stamp>.disabled", so nothing is actually lost - but the
 * undo button only knows the batches of the tab that performed them, and that
 * stack dies with the tab. The server's delete log does not, so this view lists
 * every deletion that can still be reversed and restores any selection of them.
 * The same list is where a deletion is made permanent: the red button erases the
 * .disabled file, which is the one action here that cannot be undone.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {el, api, setStatus, preserveViewport, data} = KK;

  let items = [];
  let selected = new Set();
  let busy = false;
  let loading = false;

  /* The bar rides along the top of the window, like the Mod grid's batch bar:
     with several hundred rows, the buttons that act on the rows you just ticked
     must not be twenty screens above them. It lives outside <header>, where a
     sticky element would be trapped by a parent it is taller than. */
  const controls = el('div', undefined, 'bulk');
  controls.id = 'trashControls';

  const count = el('span', '', 'bulk-count');
  count.id = 'trashCount';

  const selectAll = el('button', '全选可恢复', 'bulk-action');
  selectAll.id = 'trashSelectAll';
  selectAll.type = 'button';

  const restoreButton = el('button', '恢复选中', 'bulk-action');
  restoreButton.id = 'trashRestore';
  restoreButton.type = 'button';

  const purgeButton = el('button', '彻底删除选中', 'bulk-action bulk-danger');
  purgeButton.id = 'trashPurge';
  purgeButton.type = 'button';

  /* Rows whose .disabled file is gone cannot be restored, so their checkbox
     stays disabled and "select all restorable" can never reach them. Without
     this button such a record would sit in the list forever, one click at a
     time: it only drops log lines and never touches a file. */
  const cleanButton = el('button', '清理失效记录', 'bulk-action');
  cleanButton.id = 'trashClean';
  cleanButton.type = 'button';

  const refreshButton = el('button', '刷新', 'bulk-action');
  refreshButton.id = 'trashRefresh';
  refreshButton.type = 'button';

  controls.append(count, selectAll, restoreButton, purgeButton, cleanButton,
    refreshButton);

  const element = el('div');
  element.id = 'trashList';

  /* Two sentences, and only the thing people actually worry about: did I just
     lose the file? The buttons say the rest themselves. */
  const help = el('p',
    '这些 Mod 还在硬盘上，只是后缀被改了，点「恢复」就回来。'
    + '「彻底删除」才会真的删文件。', 'notice trash-help');
  element.append(help);

  const list = el('div', '', 'trash-rows');
  element.append(list);

  const canRestore = item => item.available && !item.restored
    && !item.otherRoot && !item.conflict;

  /* No .disabled file behind the record: nothing to restore and nothing to
     erase, so dropping the line is the only thing left to do with it. Records
     from another mods folder are excluded - the server refuses those, because
     it cannot check a path it is not currently pointed at. */
  const canClean = item => !item.otherRoot && !item.available;

  function megabytes(bytes) {
    if (!bytes) return '—';
    return (Math.round(bytes / 10485.76) / 100) + ' MB';
  }

  function totalSize(rows) {
    const sum = rows.reduce((bytes, item) => bytes + (item.bytes || 0), 0);
    return sum ? (Math.round(sum / 104857.6) / 10) + ' MB' : '';
  }

  function stamp(text) {
    return String(text || '').replace('T', ' ').slice(0, 16);
  }

  function reason(item) {
    if (item.restored) return ['已恢复', 'done'];
    if (item.otherRoot) return ['删除时用的 Mod 目录与当前不同，请先切回该目录', 'blocked'];
    if (!item.available) return ['原文件已不在（可能被手动删除或移走）', 'blocked'];
    if (item.conflict) return ['同名文件已存在，请先处理该文件', 'blocked'];
    return ['可恢复', 'ready'];
  }

  function updateChrome() {
    const usable = items.filter(canRestore);
    const chosen = usable.filter(item => selected.has(item.id));
    const stale = items.filter(canClean);
    const parts = [usable.length ? `可恢复 ${usable.length} 个` : '没有可恢复的记录'];
    if (usable.length) parts.push(`已选 ${chosen.length}`);
    if (stale.length) parts.push(`失效 ${stale.length} 条`);
    count.textContent = parts.join(' · ');
    selectAll.disabled = !usable.length || busy;
    selectAll.textContent = chosen.length && chosen.length === usable.length
      ? '取消全选' : '全选可恢复';
    restoreButton.disabled = !chosen.length || busy;
    restoreButton.textContent = chosen.length ? `恢复选中（${chosen.length}）` : '恢复选中';
    purgeButton.disabled = !chosen.length || busy;
    purgeButton.textContent = chosen.length
      ? `彻底删除选中（${chosen.length}）` : '彻底删除选中';
    cleanButton.disabled = !stale.length || busy;
    cleanButton.textContent = stale.length
      ? `清理失效记录（${stale.length}）` : '清理失效记录';
    refreshButton.disabled = busy || loading;
  }

  function preview(item) {
    // The server hands out one picture per row, extracting it from the disabled
    // archive the first time and then serving the copy the index already had.
    const box = el('div', '', 'trash-thumb');
    const picture = KK.media
      ? KK.media.thumb('/api/deletion-thumb?id=' + encodeURIComponent(item.id), item.file)
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
    if (!items.length) {
      list.append(el('p', '还没有任何删除记录。', 'notice'));
      updateChrome();
      return;
    }
    for (const item of items) {
      const row = el('div', '', 'trash-row');
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

      const single = el('button', '恢复', 'bulk-action trash-one');
      single.type = 'button';
      single.disabled = !canRestore(item) || busy;
      single.onclick = () => restore(item.id);

      const erase = el('button', '彻底删除', 'bulk-action trash-purge-one');
      erase.type = 'button';
      // A record logged against another mods folder cannot be matched to a file
      // here, so the server refuses it; say so instead of failing on click.
      erase.disabled = busy || item.otherRoot;
      if (item.otherRoot) erase.title = '请先切回删除该 Mod 时使用的目录';
      erase.onclick = () => purge(item.id);

      row.append(pick, preview(item), main, state, single, erase);
      list.append(row);
    }
    updateChrome();
  }

  async function load() {
    if (loading) return;
    loading = true;
    updateChrome();
    try {
      const payload = await api.read('/api/deletions');
      items = Array.isArray(payload.items) ? payload.items : [];
      const live = new Set(items.filter(canRestore).map(item => item.id));
      selected = new Set([...selected].filter(id => live.has(id)));
      renderRows();
    } catch (error) {
      items = [];
      list.replaceChildren(el('p', '读取删除记录失败：' + error.message, 'notice'));
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
    setStatus(`正在恢复 ${wanted.length} 个 Mod · 写入索引…`);
    try {
      const result = await api.mutate('/api/restore', {ids: wanted});
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
        setStatus(`已恢复 ${back.length} 个 Mod` +
          (failures.length ? ` · ${failures.length} 个未能恢复` : '') +
          (result.warning ? ' · ' + result.warning : ''));
      });
      return;
    } catch (error) {
      setStatus('恢复失败：' + error.message);
    } finally {
      busy = false;
      renderRows();
    }
  }

  async function purge(ids) {
    const wanted = (Array.isArray(ids) ? ids : [ids])
      .filter(id => items.some(item => item.id === id));
    if (!wanted.length || busy) return;
    const chosen = items.filter(item => wanted.includes(item.id));
    const files = chosen.filter(item => item.available).length;
    const remaining = chosen.length - files;
    const subject = wanted.length === 1
      ? `「${chosen[0].file || chosen[0].source}」` : `${wanted.length} 条删除记录`;
    const detail = files
      ? `会从硬盘永久移除 ${files} 个 .disabled 文件（${totalSize(chosen)}），` +
        '之后无法再用本工具恢复。'
      : '这些记录对应的文件已不在硬盘上，只清除列表里的记录。';
    const extra = files && remaining
      ? `另有 ${remaining} 条记录没有对应文件，只清除记录。` : '';
    if (!window.confirm(`彻底删除${subject}？\n\n${detail}${extra}`)) return;
    busy = true;
    selected = new Set();
    renderRows();
    setStatus(`正在彻底删除 ${wanted.length} 条记录…`);
    try {
      const result = await api.mutate('/api/purge', {ids: wanted});
      const failures = result.failures || [];
      busy = false;
      await load();
      preserveViewport(() => {
        setStatus(`已彻底删除 ${result.purged || 0} 条记录` +
          (failures.length ? ` · ${failures.length} 条未能删除` : ''));
      });
      return;
    } catch (error) {
      setStatus('彻底删除失败：' + error.message);
    } finally {
      busy = false;
      renderRows();
    }
  }

  selectAll.type = 'button';
  /* Drop the log lines whose archive is gone. The server never unlinks a file it
     cannot match to its own log entry, and a record without a .disabled file has
     none to match, so this rewrites the log and nothing else. Requests are
     chunked because one purge call is capped. */
  async function cleanStale() {
    const wanted = items.filter(canClean);
    if (!wanted.length || busy) return;
    if (!window.confirm(`清理 ${wanted.length} 条失效记录？\n\n`
      + '这些记录对应的 .disabled 文件都已经不在硬盘上，只能从列表里移除，'
      + '不会删除任何文件。')) return;
    const CHUNK = 400;
    const ids = wanted.map(item => item.id);
    const failures = [];
    let purged = 0;
    busy = true;
    selected = new Set();
    renderRows();
    setStatus(`正在清理 ${ids.length} 条失效记录…`);
    try {
      for (let start = 0; start < ids.length; start += CHUNK) {
        const result = await api.mutate('/api/purge',
          {ids: ids.slice(start, start + CHUNK)});
        purged += result.purged || 0;
        failures.push(...(result.failures || []));
        setStatus(`正在清理失效记录 · ${Math.min(start + CHUNK, ids.length)}`
          + `/${ids.length}…`);
      }
      busy = false;
      await load();
      preserveViewport(() => {
        setStatus(`已清理 ${purged} 条失效记录` +
          (failures.length ? ` · ${failures.length} 条未能清理` : ''));
      });
      return;
    } catch (error) {
      setStatus('清理失效记录失败：' + error.message);
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
  purgeButton.onclick = () => purge([...selected]);
  cleanButton.onclick = () => cleanStale();
  refreshButton.onclick = () => { load(); };

  KK.registerView({
    id: 'trash',
    label: '已删除',
    dock: controls,
    element,
    render() {
      renderRows();
      load();
    }
  });
})();
