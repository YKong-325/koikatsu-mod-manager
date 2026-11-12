/* Archiving: the button beside delete, and the page that brings packages back.
 *
 * Deleting hides a package in place; archiving moves it out of the mods tree so
 * it can be backed up like any other file. Two things therefore have to hold:
 * the Mod grid offers both actions side by side, and the archive page only ever
 * moves files - a selection goes back to its original path in one request, and
 * nothing on that page can erase an archive.
 */
const assert = require('node:assert/strict');
const {createApp, descendants} = require('./DomShim');
const {catalog, deps} = require('./Fixture');

const items = [
  {id: 'a1', time: '2026-11-12T10:02:11', source: 'Aumi/guilty.zipmod',
    archived: 'Aumi/guilty.zipmod', file: 'guilty.zipmod', bytes: 8356769,
    name: 'guilty mods', author: 'Aumi', guid: 'guilty.mods',
    restored: false, otherRoot: false, conflict: false, available: true},
  {id: 'a2', time: '2026-11-12T09:41:02', source: 'ATP/jacket.zipmod',
    archived: 'ATP/jacket.zipmod', file: 'jacket.zipmod', bytes: 10715552,
    name: 'Amiya', author: 'ATP', guid: 'Amiya_Jacket',
    restored: true, otherRoot: false, conflict: false, available: false},
  {id: 'a3', time: '2026-11-12T09:20:00', source: 'Augh/tag.zipmod',
    archived: 'Augh/tag.zipmod', file: 'tag.zipmod', bytes: 468531,
    name: 'Ear Tag', author: 'Augh', guid: 'tag',
    restored: false, otherRoot: false, conflict: true, available: true},
  {id: 'a4', time: '2026-11-12T08:00:00', source: 'Old/x.zipmod',
    archived: 'Old/x.zipmod', file: 'x.zipmod', bytes: 1024, name: 'x', author: '',
    guid: 'x', restored: false, otherRoot: true, conflict: false, available: true}
];

const calls = [];
const pool = catalog.slice();
const routes = {
  '/api/session': () => ({token: 'test-token'}),
  '/api/deps': () => deps,
  '/api/archives': () => ({items, pending: 1, modsRoot: 'D:\\Koikatu\\mods',
    archiveRoot: 'D:\\Koikatu\\mods_archive'}),
  '/api/archive': body => {
    calls.push({endpoint: '/api/archive', body});
    const removed = [];
    for (const path of body.paths) {
      const index = pool.findIndex(record => record.path === path);
      if (index >= 0) removed.push(pool.splice(index, 1)[0]);
    }
    return {ok: true, id: 'a9', ids: ['a9'], count: removed.length,
      archiveRoot: 'D:\\Koikatu\\mods_archive', failures: [], warning: null};
  },
  '/api/unarchive': body => {
    calls.push({endpoint: '/api/unarchive', body});
    const wanted = new Set(body.ids);
    return {ok: true, records: items.filter(item => wanted.has(item.id))
      .map(item => ({path: item.source, file: item.file, bytes: item.bytes})),
      failures: [], count: wanted.size};
  }
};

const app = createApp({protocol: 'http:', catalog: pool, deps, routes,
  confirmAnswer: () => true});
const KK = app.load(['mod-search.js', 'app-core.js', 'media-thumbs.js',
  'view-mods.js', 'view-trash.js', 'view-archive.js', 'card-refs.js', 'plugin-deps.js']);
const $ = id => app.byId.get(id);
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

/* --- the two actions sit side by side on a card -------------------------- */

KK.openView('mods');
const card = $('grid').children[0];
const archive = card.querySelector('.archive');
const disable = card.querySelector('.delete');
assert.ok(archive, 'a card offers archiving next to disabling');
assert.ok(disable, 'and still offers disabling');
assert.match(archive.title, /归档/);
assert.match(disable.title, /停用/);
// Both buttons are icon-only: the glyph is a CSS mask over the button itself,
// so the text node stays empty and the name lives in aria-label.
assert.equal(archive.textContent, '', 'archiving shows an icon, not a glyph');
assert.equal(disable.textContent, '', 'and so does disabling');
assert.match(archive.getAttribute('aria-label'), /^归档此 Mod：/);
assert.match(disable.getAttribute('aria-label'), /^停用此 Mod：/);
// Renaming in place is destructive-looking; moving to a backup folder is not.
assert.equal(archive.classList.contains('delete'), false,
  'archiving must not wear the destructive button style');
assert.equal($('archiveSelected').textContent, '归档选中');
assert.equal($('archiveSelected').disabled, true, 'nothing selected yet');

/* --- registering --------------------------------------------------------- */

const tabs = $('tabs').children.filter(node => node.tagName === 'BUTTON');
assert.deepEqual(tabs.map(tab => tab.textContent), ['Mod', '已删除', '归档', '依赖检查']);
assert.ok($('archiveControls'), 'the view ships its own action bar');
assert.equal($('archiveControls').hidden, true, 'the bar stays hidden until opened');
assert.ok($('dock').children.includes($('archiveControls')),
  'the bar is docked, so it stays on screen while the list scrolls');
assert.ok(!descendants($('controls')).includes($('archiveControls')),
  'and it is not one of the filter rows inside <header>');

/* --- single archive from a card ------------------------------------------ */

const firstPath = card.dataset.path;
archive.click();

tick().then(() => {
  assert.equal(calls.length, 1, 'one request for one card');
  assert.equal(calls[0].endpoint, '/api/archive');
  assert.deepEqual(calls[0].body.paths, [firstPath],
    'the card sends its own package path');
  assert.match($('status').textContent, /已归档 1 个 Mod 到 D:\\Koikatu\\mods_archive/);
  assert.ok(!KK.data.catalog.some(record => record.path === firstPath),
    'the archived package leaves the grid');

  /* --- listing ----------------------------------------------------------- */

  KK.openView('archive');
  tick().then(() => {
    assert.equal($('dock').hidden, false, 'the sticky strip shows for this view');
    const rows = $('archiveList').querySelectorAll('.trash-row');
    assert.equal(rows.length, 4, 'every logged archive is listed, regardless of state');

    const boxes = rows.map(row => row.querySelector('input'));
    assert.equal(boxes[0].disabled, false, 'a pending archive is selectable');
    assert.equal(boxes[1].disabled, true, 'an already restored one is not');
    assert.equal(boxes[2].disabled, true, 'a same-name conflict is not');
    assert.equal(boxes[3].disabled, true, 'an archive logged against another root is not');

    // The page must say where the files went: that is the whole point of the
    // feature, and the folder is configurable.
    const hint = $('archiveList').querySelector('.trash-help');
    assert.ok(hint, 'the archive page explains itself above the list');
    assert.match(hint.textContent, /D:\\Koikatu\\mods_archive/);
    assert.match(hint.textContent, /取回/);
    assert.ok(!/archive-log\.jsonl/.test(hint.textContent),
      'the log filename is an implementation detail, not a user-facing fact');
    assert.match($('archiveCount').textContent, /可取回 1 个/);

    /* No destructive control: archiving is a backup, and the page that erases
       files is 已删除, not this one. */
    assert.equal($('archiveList').querySelectorAll('.trash-purge-one').length, 0);
    assert.equal($('archiveList').querySelectorAll('.bulk-danger').length, 0);

    /* --- covers ---------------------------------------------------------- */

    const covers = rows.map(row => row.querySelector('.trash-thumb'));
    assert.equal(covers[0].children[0].dataset.src, '/api/archive-thumb?id=a1');

    /* --- restore a selection in one request ------------------------------ */

    $('archiveSelectAll').click();
    assert.match($('archiveCount').textContent, /已选 1/);
    assert.equal($('archiveSelectAll').textContent, '取消全选');
    assert.match($('archiveRestore').textContent, /取回选中（1）/);
    $('archiveRestore').click();

    tick().then(() => {
      assert.equal(calls.length, 2, 'one request for the whole selection');
      assert.equal(calls[1].endpoint, '/api/unarchive');
      assert.deepEqual(calls[1].body.ids, ['a1']);
      assert.match($('status').textContent, /已取回 1 个 Mod/);
      assert.ok(KK.data.catalog.some(record => record.path === 'Aumi/guilty.zipmod'),
        'the restored record reaches the Mod grid data');
      console.log('PASS: a card archives beside its delete button, the archive page '
        + 'lists what the log knows, moves a selection back in one request, and '
        + 'offers no way to erase a file');
    }).catch(error => { console.error(error); process.exit(1); });
  }).catch(error => { console.error(error); process.exit(1); });
}).catch(error => { console.error(error); process.exit(1); });
