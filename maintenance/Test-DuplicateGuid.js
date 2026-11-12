/* Duplicate GUIDs, seen from the page.
 *
 * Two packages can carry the same GUID. Sideloader then loads only one of them
 * and skips the rest without saying so in the UI, so a pack that looks
 * installed can be the one that never loads. The manager cannot decide which
 * one wins, but it must say the collision exists -- and that hint used to sit
 * behind the "卡片引用" filter, which meant the default view showed nothing.
 */
const assert = require('node:assert/strict');
const {createApp} = require('./DomShim');
const Fixture = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'view-trash.js', 'card-refs.js', 'plugin-deps.js', 'plugin-conflicts.js'];

const catalog = Fixture.catalog.map(record => ({...record}));
const mine = catalog[0];
const twin = catalog[1];
assert.ok(mine.guid && twin.guid, 'the fixture packs carry GUIDs');
// A different spelling of the same GUID is still the same GUID.
twin.guid = mine.guid.toUpperCase();
twin.file = 'pack001-conflict.zipmod';

// deps.json carries one verdict per package; keep the snapshot consistent.
const mods = {...Fixture.deps.mods};
for (const record of [mine, twin]) {
  mods[record.path] = {...mods[record.path], duplicateGuid: true};
}

const app = createApp({catalog, library: Fixture.library,
  deps: {...Fixture.deps, mods}});
const KK = app.load(SCRIPTS);
const $ = id => app.byId.get(id);
const cardFor = path => $('grid').children.find(card => card.dataset.path === path);

KK.openView('mods');

/* --- The banner --------------------------------------------------------- */

const banner = cardFor(mine.path).querySelector('.conflict-flag');
assert.ok(banner, 'a collision is visible without touching any filter');
assert.equal(banner.textContent,
  'GUID 冲突：另有 1 个包使用同一 GUID，游戏只会加载其中一个 · 查看 ›',
  'the banner says what is wrong and that it opens something');
assert.equal(banner.title, '查看这 2 个包',
  'the banner is a door to the packs that share the GUID, not a dead tooltip');
assert.ok(cardFor(twin.path).querySelector('.conflict-flag'),
  'both sides of the collision are marked, not just one');
assert.ok(!cardFor(catalog[2].path).querySelector('.conflict-flag'),
  'a package without a collision gets no banner');

/* --- Finding them ------------------------------------------------------- */

// A dropdown that says "all packages / only the colliding ones" was a second
// way to ask what the banner already answered, so it is gone: the count in the
// toolbar opens the packs themselves.
assert.equal($('modPluginFacets').querySelector('[data-facet="conflict"]'), null,
  'the collision dropdown is retired');
assert.equal(KK.facets.length, 1, 'only the card-reference facet is left');

const door = $('modPluginTools').querySelector('#showGuidClashes');
assert.ok(door, 'the toolbar carries the way into the colliding packages');
assert.match(door.textContent, /GUID 冲突 1 组（2 个包）/,
  'the door counts the collisions the catalog holds');

door.onclick();
const clashDialog = app.byId.get('guidClashDialog');
assert.equal(clashDialog.open, true, 'the count opens the colliding packages');
const groups = clashDialog.querySelectorAll('.guid-group');
assert.equal(groups.length, 1, 'one block per colliding GUID');
assert.equal(groups[0].querySelector('.guid-key').textContent, mine.guid.toLowerCase(),
  'the block names the GUID the packages share');
const packs = groups[0].querySelectorAll('.library-card');
assert.equal(packs.length, 2, 'both sides of the collision are listed, not just one');
assert.deepEqual(
  Array.from(packs).map(node => node.querySelector('.path').textContent).sort(),
  [mine.path, twin.path].sort(),
  'the two packages listed are the two that collide');
// The cover is the only thing that separates two near-identical names, so the
// rows are cards, not lines of text.
assert.ok(packs[0].children.length > 1, 'each package is shown with its preview, not just text');

/* The dialog repeats the reference counts the Mod grid prints: zero means the
   GUID is unused, a number means one copy has to stay. Both copies of a GUID
   carry the same number, because cards name the GUID rather than the file. */
const packFor = path => Array.from(packs)
  .find(node => node.querySelector('.path').textContent === path);
const usageFor = path => packFor(path).querySelector('.guid-usage');
const countLink = usageFor(mine.path).querySelector('.ref-count');
assert.equal(countLink.textContent, '被 1 张角色卡引用',
  'a colliding package says how many cards wear it');
assert.equal(countLink.title, '查看这 1 张角色卡',
  'and the count is a door to those cards, as it is on the Mod grid');
assert.equal(usageFor(twin.path).textContent, '未被任何卡片引用',
  'the unused copy says so, which is what makes it droppable');
assert.ok(usageFor(twin.path).classList.contains('unused'),
  'the unused copy is not dressed up in the colour of a reason to keep it');

/* Pressing the count has to reach the same list the grid opens, from inside a
   dialog that is already modal. */
let asked = '';
KK.api.read = async endpoint => {
  asked = endpoint;
  return {total: 1, cards: [{path: 'chara/a.png', file: 'a.png', kind: '角色卡',
    thumb: 'library_thumbs/a.jpg'}]};
};
countLink.onclick();
assert.equal(asked, '/api/library/cards?mod=pack000.zipmod&kind=cards',
  'the count asks for this package and this kind of card');
const refs = app.byId.get('cardRefsDialog');
assert.equal(refs.open, true, 'the cards open over the collision list');
assert.match(refs.querySelector('h2').textContent, /被 1 张角色卡引用/,
  'the list is titled the same way as when it is opened from the grid');
refs.children[0].children[1].onclick();
assert.equal(refs.open, false, 'closing it leaves the collision list in place');

/* The banner is the door on the card itself: clicking the one on a colliding
   card opens that GUID's group instead of the whole list. */
cardFor(mine.path).querySelector('.conflict-flag').onclick();
assert.equal(clashDialog.open, true);
assert.match(clashDialog.querySelector('h2').textContent, /2 个包共用这个 GUID/,
  'the card opens its own group, not the whole list');

/* --- The dependency view ------------------------------------------------ */

KK.openView('deps');
const summary = $('dependencySummary');
assert.equal(summary.querySelector('.conflict-open'), null,
  'the collision count is no longer part of the dependency header');

console.log('PASS: duplicate GUIDs are marked on the Mod grid, the card banner and '
  + 'the toolbar door both open the colliding packages as cards carrying the '
  + 'same reference counts the grid shows -- each of them opening the cards '
  + 'behind it -- and the dependency view no longer reports on collisions');
