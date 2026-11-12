/* Replay the real catalog through the page modules under node.
 *
 * The shell tests use a 3-record fixture, so they cannot catch a renderer that
 * throws on real data. Usage: node maintenance/Repro-RealCatalog.js <catalogDir>
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createApp} = require('./DomShim.js');

const dir = process.argv[2];
if (!dir) throw new Error('pass the catalog directory');

const app = createApp({protocol: 'file:'});
for (const name of ['data.js', 'deps.js']) {
  const file = path.join(dir, name);
  if (!fs.existsSync(file)) { console.log('missing', name); continue; }
  vm.runInContext(fs.readFileSync(file, 'utf8'), app.context, {filename: name});
}
console.log('catalog:', (app.window.MOD_CATALOG || []).length,
  '| deps.cards:', Object.keys((app.window.KK_DEPS || {}).cards || {}).length);

const KK = app.load(['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'view-trash.js', 'card-refs.js', 'plugin-deps.js', 'preview-ui.js']);

console.log('views:', KK ? KK.facets.length + ' facets' : 'NO KK');
const tabs = app.document.getElementById('tabs');
console.log('tabs:', tabs.children.map(tab => tab.textContent).join(' | '));

function report(viewId, container) {
  try {
    KK.openView(viewId);
    const node = app.document.getElementById(container);
    const cards = node ? node.children.length : -1;
    console.log(`${viewId}: container=${container} children=${cards},
      status="${app.document.getElementById('status').textContent.slice(0, 60)}"`);
  } catch (error) {
    console.log(`${viewId}: THREW ${error && error.stack ? error.stack.split('\n')[0] : error}`);
  }
}

report('mods', 'grid');
report('deps', 'dependencyList');
