/* Deleted-mods view: the durable list of restorable deletions.
 *
 * Deleting only renames the archive on disk, so the view is a recovery screen:
 * it has to show every logged deletion, refuse the ones the server would also
 * refuse, and restore a selection in one request. The same rows are where a
 * deletion becomes permanent, which is the one action that needs a confirmation.
 */
const assert = require('node:assert/strict');
const {createApp, descendants} = require('./DomShim');
const {catalog, deps} = require('./Fixture');

const items = [
  {id: 'aaa', time: '2026-10-31T16:12:38', source: 'Aumi/guilty.zipmod',
    file: 'guilty.zipmod', bytes: 8356769, name: 'guilty mods', author: 'Aumi',
    guid: 'guilty.mods', restored: false, otherRoot: false, conflict: false, available: true},
  {id: 'bbb', time: '2026-10-31T15:16:21', source: 'ATP/jacket.zipmod',
    file: 'jacket.zipmod', bytes: 10715552, name: 'Amiya', author: 'ATP',
    guid: 'Amiya_Jacket', restored: true, otherRoot: false, conflict: false, available: false},
  {id: 'ccc', time: '2026-10-31T15:26:29', source: 'Augh/tag.zipmod',
    file: 'tag.zipmod', bytes: 468531, name: 'Ear Tag', author: 'Augh',
    guid: 'tag', restored: false, otherRoot: false, conflict: true, available: true},
  {id: 'ddd', time: '2026-10-31T15:01:02', source: 'Old/x.zipmod',
    file: 'x.zipmod', bytes: 1024, name: 'x', author: '',
    guid: 'x', restored: false, otherRoot: true, conflict: false, available: true}
];

const calls = [];
const purges = [];
const routes = {
  '/api/session': () => ({token: 'test-token'}),
  '/api/deps': () => deps,
  '/api/deletions': () => ({items, pending: 1, modsRoot: 'D:\\Koikatu\\mods'}),
  '/api/purge': body => {
    purges.push(body);
    return {ok: true, purged: body.ids.length, failures: [], remaining: 4 - body.ids.length};
  },
  '/api/restore': body => {
    calls.push(body);
    const wanted = new Set(body.ids);
    return {
      ok: true,
      records: items.filter(item => wanted.has(item.id))
        .map(item => ({path: item.source, file: item.file, bytes: item.bytes})),
      failures: [], count: wanted.size
    };
  }
};

const app = createApp({protocol: 'http:', catalog: [...catalog], deps, routes});
const KK = app.load(['mod-search.js', 'app-core.js', 'media-thumbs.js',
  'view-mods.js', 'view-trash.js', 'plugin-deps.js']);
const $ = id => app.byId.get(id);
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

/* --- registration ------------------------------------------------------- */

const tabs = $('tabs').children.filter(node => node.tagName === 'BUTTON');
assert.deepEqual(tabs.map(tab => tab.textContent), ['Mod', '已删除', '依赖检查']);
assert.ok($('trashControls'), 'the view ships its own action bar');
assert.equal($('trashControls').hidden, true, 'the bar stays hidden until opened');
// The list runs to hundreds of rows, so the buttons that act on the rows you
// ticked have to follow the scroll instead of sitting in the header.
assert.ok($('dock').children.includes($('trashControls')),
  'the bar is docked, so it stays on screen while the list scrolls');
assert.ok(!descendants($('controls')).includes($('trashControls')),
  'and it is not one of the filter rows inside <header>');

/* --- listing ------------------------------------------------------------ */

KK.openView('trash');

tick().then(() => {
  assert.equal($('dock').hidden, false, 'the sticky strip shows for this view');
  const rows = $('trashList').querySelectorAll('.trash-row');
  assert.equal(rows.length, 4, 'every logged deletion is listed, regardless of state');

  // Only the rows the server would actually accept can be selected.
  const boxes = rows.map(row => row.querySelector('input'));
  assert.equal(boxes[0].disabled, false, 'a pending deletion is selectable');
  assert.equal(boxes[1].disabled, true, 'an already restored one is not');
  assert.equal(boxes[2].disabled, true, 'a same-name conflict is not');
  assert.equal(boxes[3].disabled, true, 'a deletion from another mods folder is not');

  assert.match($('trashCount').textContent, /可恢复 1 个/);
  // A row whose .disabled file is gone can never be ticked, so the count has to
  // say it is there - otherwise the list looks clean while it is not.
  assert.match($('trashCount').textContent, /失效 1 条/);
  assert.match($('trashClean').textContent, /清理失效记录（1）/);
  assert.equal($('trashRestore').disabled, true, 'nothing selected yet');
  assert.equal($('trashPurge').disabled, true, 'nothing selected yet');
  // Restoring is the safe half of the pair, so it stays a plain white button;
  // only the irreversible purge borrows the accent colour.
  assert.equal($('trashRestore').classList.contains('bulk-action'), true);
  assert.equal($('trashRestore').classList.contains('bulk-render'), false,
    'restoring must not wear the accent that marks the destructive action');
  assert.equal($('trashPurge').classList.contains('bulk-danger'), true);

  /* --- covers ------------------------------------------------------------ */

  // Every row asks the server for its own cover, keyed by log id: the archive
  // has been renamed, so its path is no longer an address the page can use.
  const covers = rows.map(row => row.querySelector('.trash-thumb'));
  assert.equal(covers.length, 4, 'each row carries a cover slot');
  assert.equal(covers[0].children[0].dataset.src, '/api/deletion-thumb?id=aaa');
  assert.equal(covers[3].children[0].dataset.src, '/api/deletion-thumb?id=ddd');
  assert.equal(covers[1].classList.contains('empty'), false,
    'a restored row keeps the cover it was deleted with');

  /* --- permanent delete needs a confirmation ----------------------------- */

  const erasers = rows.map(row => row.querySelector('.trash-purge-one'));
  assert.equal(erasers.length, 4, 'every row offers a permanent delete');
  assert.equal(erasers[3].disabled, true,
    'a record from another mods folder cannot be erased from here');

  $('trashSelectAll').click();
  $('trashPurge').click();
  assert.equal(purges.length, 0, 'a refused confirmation deletes nothing');
  assert.match($('trashPurge').textContent, /彻底删除选中（1）/);
  $('trashSelectAll').click();

  /* --- select all -------------------------------------------------------- */

  $('trashSelectAll').click();
  assert.match($('trashCount').textContent, /已选 1/);
  assert.equal($('trashRestore').disabled, false);
  assert.match($('trashRestore').textContent, /恢复选中（1）/);
  assert.equal($('trashSelectAll').textContent, '取消全选');

  $('trashSelectAll').click();
  assert.equal($('trashRestore').disabled, true, 'select all is a toggle');

  /* --- restore ----------------------------------------------------------- */

  boxes[0].checked = true;
  boxes[0].onchange();
  $('trashRestore').click();

  tick().then(() => {
    assert.equal(calls.length, 1, 'one request for the whole selection');
    assert.deepEqual(calls[0].ids, ['aaa']);
    assert.match($('status').textContent, /已恢复 1 个 Mod/);
    // The restored record must reach the Mod grid's data, or the package looks
    // permanently missing until the index is rebuilt.
    assert.ok(KK.data.catalog.some(record => record.path === 'Aumi/guilty.zipmod'));

    /* --- purge a restored record ----------------------------------------- */

    // A restored record has no disabled file left, so this only drops the row
    // and must never be offered as a way to delete the live archive.
    app.window.confirm = () => true;
    const refreshed = $('trashList').querySelectorAll('.trash-row');
    refreshed[1].querySelector('.trash-purge-one').click();
    tick().then(() => {
      assert.equal(purges.length, 1, 'one request for one row');
      assert.deepEqual(purges[0].ids, ['bbb']);
      assert.match($('status').textContent, /已彻底删除 1 条记录/);

      /* --- cleaning the rows nothing can be done with -------------------- */

      // The stale row is still listed and still untickable; the cleanup button
      // is the only way to drop it, and it must send that row alone.
      const clean = $('trashClean');
      assert.equal(clean.disabled, false, 'the stale row makes the button usable');
      assert.match(clean.textContent, /清理失效记录（1）/);
      app.window.confirm = () => false;
      clean.click();
      assert.equal(purges.length, 1, 'a refused confirmation cleans nothing');
      app.window.confirm = () => true;
      clean.click();
      tick().then(() => {
        assert.equal(purges.length, 2, 'one request with the stale rows');
        assert.deepEqual(purges[1].ids, ['bbb']);
        assert.match($('status').textContent, /已清理 1 条失效记录/);
        console.log('PASS: deleted mods list with a cover, a reason and a red '
          + 'permanent-delete button; a selection restores in one request; '
          + 'rows whose file is gone can be cleared in bulk');
      }).catch(error => { console.error(error); process.exit(1); });
    }).catch(error => { console.error(error); process.exit(1); });
  }).catch(error => { console.error(error); process.exit(1); });
}).catch(error => { console.error(error); process.exit(1); });
