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
  'view-trash.js', 'plugin-deps.js'];

const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

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
const rowFor = path => $('dependencyList').children.find(row => {
  const shown = row.querySelector('.path');
  return shown && shown.textContent === path;
});

KK.openView('mods');

/* --- The banner --------------------------------------------------------- */

const banner = cardFor(mine.path).querySelector('.conflict-flag');
assert.ok(banner, 'a collision is visible without touching any filter');
assert.equal(banner.textContent,
  'GUID 冲突：另有 1 个包使用同一 GUID，游戏只会加载其中一个');
assert.match(banner.title, new RegExp(escape(twin.file)),
  'the tooltip names the package it collides with');
assert.ok(cardFor(twin.path).querySelector('.conflict-flag'),
  'both sides of the collision are marked, not just one');
assert.ok(!cardFor(catalog[2].path).querySelector('.conflict-flag'),
  'a package without a collision gets no banner');

/* --- Finding them ------------------------------------------------------- */

const facet = $('modPluginFacets').querySelector('[data-facet="conflict"]');
assert.ok(facet, 'the toolbar offers a collision filter once one exists');
assert.equal(facet.children.length, 2, 'all packages, or only the colliding ones');
assert.match(facet.children[1].textContent, /只看冲突（2 个包）/);

facet.value = 'only';
facet.onchange();
assert.equal($('grid').children.length, 2,
  'the filter narrows the grid to the colliding pair');
assert.match($('status').textContent, /2 个匹配/);

facet.value = '';
facet.onchange();
assert.equal($('grid').children.length, 36, 'clearing it brings the page back');

/* --- The dependency view ------------------------------------------------ */

KK.openView('deps');
assert.match($('dependencySummary').textContent, /GUID 冲突 1 组（2 个包）/,
  'the header says how many collisions the catalog holds');

const mode = $('dependencyControls').children[0];
mode.value = 'mods';
mode.onchange();
const filter = $('dependencyControls').children[2];
filter.value = '';
filter.onchange();

const row = rowFor(mine.path);
assert.ok(row, 'the colliding package is listed');
assert.ok(row.querySelector('.conflict-flag'),
  'the dependency list carries the same warning, not only the Mod grid');

console.log('PASS: duplicate GUIDs are marked on both views without a filter, '
  + 'name the package they collide with, can be filtered to, and are counted '
  + 'in the dependency header');
