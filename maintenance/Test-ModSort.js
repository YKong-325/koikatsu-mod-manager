/* Sorting on the Mod grid.
 *
 * The sort select is the control people reach for when the grid looks
 * shuffled, so the fixture makes the three orders disagree: path order is not
 * name order, name order is not size order, and both differ from the order
 * packages were added. A comparator that silently fell back to the default
 * would still produce a plausible grid, which is why each key is checked
 * against an explicit expected list.
 */
const assert = require('node:assert/strict');
const {createApp} = require('./DomShim');
const {library, deps} = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'view-trash.js', 'view-archive.js', 'card-refs.js', 'plugin-deps.js',
  'plugin-conflicts.js'];

function record({path, name, bytes, ctimeNs, mtimeNs}) {
  return {
    path,
    file: path.split('/').pop(),
    name,
    guid: 'guid.' + path,
    author: 'tester',
    bytes,
    slots: ['服装'],
    dataDirs: ['chara'],
    items: [{Slot: '服装', Name: 'Item', ID: 1, ListCategory: 1}],
    images: [], thumbBundles: [], missingThumbBundles: [], errors: [],
    ctimeNs, mtimeNs
  };
}

const catalog = [
  record({path: 'b/alpha.zipmod', name: 'Mod 10', bytes: 300, ctimeNs: 3000}),
  record({path: 'c/beta.zipmod', name: 'Mod 2', bytes: 100, ctimeNs: 1000}),
  record({path: 'a/gamma.zipmod', name: 'Mod 1', bytes: 200, ctimeNs: 2000}),
  record({path: 'a/delta.zipmod', name: 'Mod 1', bytes: 200, ctimeNs: 2000}),
  record({path: 'd/zeta.zipmod', name: 'Mod 3', bytes: 400, ctimeNs: 4000}),
  record({path: 'e/eta.zipmod', name: 'Mod 4', bytes: 500, ctimeNs: 3000})
];

const app = createApp({catalog, library, deps});
app.load(SCRIPTS);
const $ = id => app.byId.get(id);
app.window.KK.openView('mods');

/* Cards are read back by path: the visible order is the only thing a user
 * sees, so the assertions compare what the grid puts first, not a sort key. */
const order = () => $('grid').children.map(card =>
  card.querySelector('.path').textContent);

function choose(value) {
  $('sort').value = value;
  $('sort').onchange();
}

const sort = $('sort');
assert.equal(sort.children.length, 8, 'every key the catalog can answer is offered');
assert.deepEqual(sort.children.map(option => option.value),
  ['path', 'name', 'name-desc', 'added', 'added-asc', 'size', 'size-asc', 'items']);

assert.deepEqual(order(), [
  'a/delta.zipmod', 'a/gamma.zipmod', 'b/alpha.zipmod',
  'c/beta.zipmod', 'd/zeta.zipmod', 'e/eta.zipmod'
], 'the default is path order, unchanged from before sorting existed');

/* Names carry numbers, and a plain string comparison would put Mod 10 between
 * Mod 1 and Mod 2. The whole point of the key is that it does not. */
choose('name');
assert.deepEqual(order(), [
  'a/delta.zipmod', 'a/gamma.zipmod', 'c/beta.zipmod',
  'd/zeta.zipmod', 'e/eta.zipmod', 'b/alpha.zipmod'
], 'names sort as text with numbers read as numbers');

choose('name-desc');
assert.deepEqual(order(), [
  'b/alpha.zipmod', 'e/eta.zipmod', 'd/zeta.zipmod',
  'c/beta.zipmod', 'a/delta.zipmod', 'a/gamma.zipmod'
], 'reversing the name key reverses the list, ties still by path');

/* Two packages added in the same second must not swap places between visits,
 * which is what a bare time comparison would let them do. */
choose('added');
assert.deepEqual(order(), [
  'd/zeta.zipmod', 'b/alpha.zipmod', 'e/eta.zipmod',
  'a/delta.zipmod', 'a/gamma.zipmod', 'c/beta.zipmod'
], 'newest first, ties broken by path');

choose('added-asc');
assert.deepEqual(order(), [
  'c/beta.zipmod', 'a/delta.zipmod', 'a/gamma.zipmod',
  'b/alpha.zipmod', 'e/eta.zipmod', 'd/zeta.zipmod'
], 'oldest first');

choose('size');
assert.deepEqual(order(), [
  'e/eta.zipmod', 'd/zeta.zipmod', 'b/alpha.zipmod',
  'a/delta.zipmod', 'a/gamma.zipmod', 'c/beta.zipmod'
], 'largest first, equal sizes fall back to path');

choose('size-asc');
assert.deepEqual(order(), [
  'c/beta.zipmod', 'a/delta.zipmod', 'a/gamma.zipmod',
  'b/alpha.zipmod', 'd/zeta.zipmod', 'e/eta.zipmod'
], 'smallest first');

/* A catalog built before the creation time was recorded has only mtime to go
 * on. Falling back keeps "newest first" meaningful instead of sorting every
 * package as 1970 and quietly doing nothing. */
const legacy = createApp({catalog: [
  record({path: 'old.zipmod', name: 'Old', bytes: 1, mtimeNs: 1000}),
  record({path: 'new.zipmod', name: 'New', bytes: 1, mtimeNs: 5000})
], library: [], deps});
legacy.load(SCRIPTS);
const legacy$ = id => legacy.byId.get(id);
legacy.window.KK.openView('mods');
legacy$('sort').value = 'added';
legacy$('sort').onchange();
assert.deepEqual(legacy$('grid').children.map(card =>
  card.querySelector('.path').textContent), ['new.zipmod', 'old.zipmod'],
  'without a creation time the modification time decides');

console.log('PASS: the Mod grid sorts by path, name (both ways), creation time '
  + '(both ways) and size (both ways), numbers inside names compare as numbers, '
  + 'ties fall back to the path, and old catalogs fall back to mtime');
