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
  'view-trash.js', 'view-archive.js', 'plugin-deps.js'];

const app = createApp({catalog, library, deps});
const KK = app.load(SCRIPTS);
const $ = id => app.byId.get(id);

assert.ok(KK, 'page kernel must exist');
assert.equal(KK.data.catalog.length, 41);

const tabs = $('tabs').children.filter(node => node.tagName === 'BUTTON');
assert.equal(tabs.length, 4, 'one tab per registered view');
assert.deepEqual(tabs.map(tab => tab.textContent),
  ['Mod', '已删除', '归档', '依赖检查']);
assert.equal(KK.facets.length, 2,
  'the plugin answers two questions on the Mod grid: card references and collisions');

/* --- Mod grid ----------------------------------------------------------- */

KK.openView('mods');
assert.equal($('grid').children.length, 36, 'a mod page holds 36 cards');
assert.match($('status').textContent, /41 个包/);
// The result line answers the filters, so it sits in the header with them
// rather than in the gap between the header and the sticky bar.
assert.equal($('status').parentElement.tagName, 'HEADER',
  'the result line belongs to the search area');
const facetSelect = $('modPluginFacets').querySelector('[data-facet="usage"]');
assert.ok(facetSelect, 'the card-reference facet is rendered into the Mod toolbar');
assert.equal(facetSelect.children.length, 4,
  'pack040 has no GUID, so "cannot tell" applies');
/* No two packs in this fixture share a GUID, so the collision facet has no
 * options to offer and must not put an empty dropdown in the toolbar. */
assert.equal($('modPluginFacets').children.length, 1,
  'a facet with nothing to offer is not rendered at all');

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

/* Three counts, one per kind of card, shown without touching any filter: which
 * kind names a package is what decides whether it is safe to drop. */
const cardText = path => $('grid').children
  .find(card => card.dataset.path === path).textContent;
assert.match(cardText('pack000.zipmod'), /被 1 张角色卡引用/);
assert.match(cardText('pack003.zipmod'), /被 1 张场景卡引用/);
assert.match(cardText('pack004.zipmod'), /被 1 张服装卡引用/);
assert.match(cardText('pack001.zipmod'), /未被任何卡片引用/);
assert.doesNotMatch(cardText('pack003.zipmod'), /张角色卡引用/,
  'a scene reference is not counted as a character one');

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
assert.match($('dependencySummary').textContent, /角色卡 2/,
  'the header opens with the card count');
assert.match($('dependencySummary').textContent, /依赖已识别/,
  'and still carries the card coverage');
assert.match($('dependencySummary').textContent, /本体物品清单/,
  'plus the note saying how a GUID is matched');
assert.equal($('dependencyControls').children.length, 3,
  'type, state and search -- the "which mods are unused" switch is gone');
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

/* --- Options that would only ever be empty ------------------------------- */

// A facet with no answer to give is not drawn, and the collision one appears
// again the moment the catalog holds a second copy of a GUID.
const twin = {...catalog[0], path: 'zz-pack-copy.zipmod'};
const dupApp = createApp({catalog: [...catalog, twin], library, deps});
const DUP = dupApp.load(SCRIPTS);
DUP.openView('mods');
const conflictSelect = () => dupApp.byId.get('modPluginFacets')
  .querySelector('[data-facet="conflict"]');
assert.ok(conflictSelect(), 'a collision puts the filter back in the toolbar');
assert.equal(conflictSelect().children.length, 2, 'all packages, or only the colliding ones');
conflictSelect().value = 'only';
conflictSelect().onchange();
assert.equal(dupApp.byId.get('grid').children.length, 2,
  'and it narrows the grid to the pair');
DUP.data.catalog.splice(DUP.data.catalog.length - 1, 1);
DUP.refreshModFacets();
assert.equal(conflictSelect(), null,
  'removing the last collision takes the dropdown away with it');

console.log('PASS: shell boots every registered view, the collision facet filters '
  + 'the Mod grid, cards are decorated through hooks, every missing-mod verdict '
  + 'comes from the shared dependency data, and the batch bar docks above the '
  + 'scrolling grid');

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
