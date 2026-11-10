/* Smoke test for the page shell.
 *
 * The browser modules are plain scripts that talk to a small, documented API
 * (KK.registerView / registerModFacet / onModCard). This boots them against a
 * minimal DOM so a view that stops registering, a plugin that stops decorating
 * cards, or a filter that loses its extension point fails here.
 */
const assert = require('node:assert/strict');
const {createApp, descendants} = require('./DomShim');
const {catalog, library, deps} = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'view-trash.js', 'plugin-deps.js'];

const app = createApp({catalog, library, deps});
const KK = app.load(SCRIPTS);
const $ = id => app.byId.get(id);

assert.ok(KK, 'page kernel must exist');
assert.equal(KK.data.catalog.length, 41);

const tabs = $('tabs').children.filter(node => node.tagName === 'BUTTON');
assert.equal(tabs.length, 3, 'one tab per registered view');
assert.deepEqual(tabs.map(tab => tab.textContent), ['Mod', '已删除', '依赖检查']);
assert.equal(KK.facets.length, 1, 'the dependency plugin registers one Mod facet');

/* --- Mod grid ----------------------------------------------------------- */

KK.openView('mods');
assert.equal($('grid').children.length, 36, 'a mod page holds 36 cards');
assert.match($('status').textContent, /41 个包/);
// The result line answers the filters, so it sits in the header with them
// rather than in the gap between the header and the sticky bar.
assert.equal($('status').parentElement.tagName, 'HEADER',
  'the result line belongs to the search area');
const facetSelect = $('modPluginFacets').querySelector('[data-facet="usage"]');
assert.ok(facetSelect, 'plugin facet is rendered into the Mod toolbar');
assert.equal(facetSelect.children.length, 4,
  'facet options come from the plugin; pack040 has no GUID, so "cannot tell" applies');

/* The bars that act on a long list stick to the top of the window while it
 * scrolls, which only works if they hang off #dock rather than a filter row
 * inside <header>. Every view with such a bar claims the same strip. */
const dock = $('dock');
assert.ok(dock.children.includes($('pickPage').parentElement),
  'the batch bar is docked outside the header');
assert.ok(!descendants($('controls')).includes($('pickPage')),
  'and it is no longer part of the filter rows');
assert.equal(dock.hidden, false, 'the strip is shown for the Mod view');
KK.openView('deps');
assert.equal(dock.hidden, false, 'the dependency view docks its download row too');
assert.ok(dock.children.includes($('downloadMissing').parentElement),
  'the download row rides along the top');
assert.ok(!descendants($('dependencySummary')).includes($('downloadMissing')),
  'and no longer scrolls away inside the summary');
assert.equal($('pickPage').parentElement.hidden, true, 'the Mod bar steps aside');
KK.openView('mods');
assert.equal(dock.hidden, false, 'coming back restores it');
assert.equal($('downloadMissing').parentElement.hidden, true,
  'and the dependency bar steps aside in turn');

const firstCard = $('grid').children[0];
assert.match(firstCard.textContent, /Pack 0/, 'card carries the package name');
assert.ok(firstCard.querySelector('.delete'), 'disable button is present');

/* No cover of its own means no cover: a card that uses the package is not
 * borrowed as its picture, the render job is what fills that gap. */
const plain = $('grid').children.find(card => card.textContent.includes('pack000.zipmod'));
assert.ok(!plain.querySelector('.images'), 'a pack without its own image keeps the placeholder');
assert.match(plain.textContent, /未声明缩略图/, 'the placeholder says why it is empty');

/* A facet value must actually filter the grid, and "referenced" must mean any
 * kind of card: a scene or an outfit is as much a reason to keep a package as a
 * character card is. */
const listedNames = () => $('grid').children.map(card => {
  const name = card.querySelector('.name');
  return name ? name.textContent : '';
});

facetSelect.value = 'unmatched';
facetSelect.onchange();
assert.equal($('grid').children.length, 36, 'unmatched page is still full');
const orphanNames = listedNames();
assert.ok(!orphanNames.includes('Pack 0'), 'a pack used by a character card stays hidden');
assert.ok(!orphanNames.includes('Pack 3'), 'a pack used by a scene stays hidden');
assert.ok(!orphanNames.includes('Pack 4'), 'a pack used by an outfit card stays hidden');

facetSelect.value = 'matched';
facetSelect.onchange();
assert.deepEqual(listedNames().sort(), ['Pack 0', 'Pack 3', 'Pack 4'],
  'character cards, outfit cards and scenes all count as a reference');
facetSelect.value = '';
facetSelect.onchange();
assert.equal($('grid').children.length, 36, 'clearing the facet restores the full list');

/* --- Dependency view ---------------------------------------------------- */

KK.openView('deps');
assert.match($('dependencySummary').textContent, /角色相关 Mod 41/);
assert.match($('dependencySummary').textContent, /依赖已识别/,
  'the header still carries the card coverage');
assert.match($('dependencySummary').textContent, /本体物品清单/,
  'and the note saying how a GUID is matched');
const download = $('downloadMissing');
assert.ok(download, 'the dependency view offers to fetch what is missing');
assert.match(download.textContent, /下载缺失 Mod（1）/,
  'the button counts the distinct missing GUIDs');
assert.equal($('dependencyList').children.length, 1, 'only one card is missing a mod');
assert.match($('dependencyList').children[0].textContent, /absent/);

/* --- Scoping a download to the cards you pick --------------------------- */

const missingCard = $('dependencyList').children[0];
const tick = missingCard.querySelector('.pick input');
assert.ok(tick, 'a card that is missing a mod can be ticked');
assert.equal($('dependencyPickAll').textContent, '全选当前结果（1）',
  'the pick-all button counts the missing cards in the current result');
tick.checked = true;
tick.onchange();
assert.match(download.textContent, /下载选中 1 张卡的 1 个 Mod/,
  'ticking a card scopes the download to that card');
assert.equal(missingCard.classList.contains('selected'), true,
  'a ticked card is highlighted like a selected Mod card');
assert.match($('dependencyPickNote').textContent, /已勾选 1 张卡/);
$('dependencyClearPick').onclick();
assert.match(download.textContent, /下载缺失 Mod（1）/,
  'clearing the selection falls back to every missing mod');
// Clearing re-renders the grid, so the old card node is gone; look it up again.
assert.equal($('dependencyList').children[0].classList.contains('selected'), false);
assert.equal($('dependencyClearPick').disabled, true, 'nothing left to clear');

$('dependencyPickAll').onclick();
assert.match(download.textContent, /下载选中 1 张卡的 1 个 Mod/,
  'pick-all takes the whole current result');
$('dependencyClearPick').onclick();

$('dependencyControls').children[0].value = 'mods';
$('dependencyControls').children[0].onchange();
assert.equal($('dependencyList').children.length, 38, 'unmatched mods are listed');
assert.ok($('dependencyControls').children[2].children
  .some(option => option.value === 'unknown'),
  'the "cannot tell" filter is there while the snapshot holds such a mod');

/* --- Options that would only ever be empty ------------------------------- */

console.log('PASS: shell boots every registered view, plugin facets filter the '
  + 'Mod grid, cards are decorated through hooks, every missing-mod verdict '
  + 'comes from the shared dependency data, and the batch bar docks above the '
  + 'scrolling grid');

// The filter dropdown must not promise a state no record is in: "cannot tell"
// only means something while a package has no GUID or the index has not caught
// up. A catalog where every package resolves gets three options, not four.
const healthy = catalog.map(record => ({...record, guid: record.guid || 'guid.40'}));
const healthyDeps = {...deps, mods: {...deps.mods,
  'pack002.zipmod': {...deps.mods['pack002.zipmod'], state: 'unmatched'},
  'pack040.zipmod': {state: 'unmatched', cards: [], outfits: [], scenes: [],
    duplicateGuid: false}}};
const clean = createApp({catalog: healthy, library, deps: healthyDeps});
const CLEAN = clean.load(SCRIPTS);
CLEAN.openView('mods');
const usageSelect = () => clean.byId.get('modPluginFacets')
  .querySelector('[data-facet="usage"]');
assert.equal(usageSelect().children.length, 3,
  'every package resolves, so "cannot tell" is not on offer');
assert.deepEqual(usageSelect().children.map(option => option.value),
  ['', 'unmatched', 'matched'], 'only the states that exist are listed');

// Rebuilding must keep a choice that still exists; it is only reset when the
// option the user picked has gone away.
usageSelect().value = 'matched';
CLEAN.data.catalog[5].guid = '';
CLEAN.refreshModFacets();
assert.equal(usageSelect().children.length, 4,
  'the option comes back as soon as one package needs it');
assert.equal(usageSelect().value, 'matched', 'and the current choice survives');

// Picking the option and then losing it resets to "all": the grid would
// otherwise keep showing a subset the dropdown no longer offers.
usageSelect().value = 'unknown';
CLEAN.refreshModFacets();
assert.equal(usageSelect().value, 'unknown', 'a choice that still exists is kept');

CLEAN.data.catalog[5].guid = 'guid.5';
CLEAN.refreshModFacets();
assert.equal(usageSelect().children.length, 3, 'it goes away with the last package in it');
assert.equal(usageSelect().value, '', 'a filter with nothing left to show resets to all');

// The dependency view's own status filter follows the same rule, against the
// snapshot it actually filters on.
CLEAN.openView('deps');
const modeSelect = clean.byId.get('dependencyControls').children[0];
modeSelect.value = 'mods';
modeSelect.onchange();
assert.ok(!clean.byId.get('dependencyControls').children[2].children
  .some(option => option.value === 'unknown'),
  'a "cannot tell" filter the snapshot has nothing for is not offered');

/* --- Orphaned tab -------------------------------------------------------- */

// The page outlives the local server. A dead socket must say so in words, and
// it must say it once at the top of the page, not leak "Failed to fetch".
const orphan = createApp({protocol: 'http:', catalog, library, deps, offline: true});
const OK = orphan.load(['mod-search.js', 'app-core.js', 'media-thumbs.js',
  'view-mods.js', 'plugin-deps.js']);
OK.api.read('/api/library').then(
  () => { throw new Error('a dead server must not resolve a read'); },
  error => {
    assert.equal(error.message, OK.api.OFFLINE);
    assert.match(error.message, /KoikatsuModManager\.exe/);
    const banners = orphan.document.body.children.filter(node => node.id === 'offlineBanner');
    assert.equal(banners.length, 1, 'a dead server puts exactly one banner on the page');
    assert.match(banners[0].textContent, /本地管理器已退出/);
    console.log('PASS: an orphaned tab explains that the local manager has exited');
  }
).catch(error => {
  console.error('FAIL: orphaned tab handling —', error.message);
  process.exitCode = 1;
});
