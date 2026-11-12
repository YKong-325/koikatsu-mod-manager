/* Disable / restore flow through the Mod view, against a stubbed server.
 *
 * Covers the write path the DOM smoke test cannot: the token handshake, the API
 * contract for single and batch deletes, click and drag selection, the undo
 * stack and the viewport guard.
 */
const assert = require('node:assert/strict');
const {createApp} = require('./DomShim');
const fixture = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'card-refs.js', 'plugin-deps.js'];

const calls = [];
const pool = fixture.catalog.slice();
const removed = [];
let polls = 0;

/* A refused confirmation must stop a batch delete, so the answer is a
 * variable the test flips. */
let confirmAnswer = true;

const app = createApp({
  protocol: 'http:',
  catalog: pool,
  library: fixture.library,
  deps: fixture.deps,
  confirmAnswer: () => confirmAnswer,
  routes: {
    '/api/session': () => ({token: 'test-token'}),
    '/api/deps': () => fixture.deps,
    '/api/delete': body => {
      const paths = body.paths || (body.path ? [body.path] : []);
      const ids = [];
      for (const path of paths) {
        const index = pool.findIndex(record => record.path === path);
        if (index < 0) continue;
        removed.push(pool.splice(index, 1)[0]);
        ids.push('undo-' + calls.length + '-' + ids.length);
      }
      calls.push({endpoint: '/api/delete', body});
      return {id: ids[0], ids, count: ids.length, failures: [],
        warning: 'test warning'};
    },
    '/api/restore': body => {
      calls.push({endpoint: '/api/restore', body});
      // The server returns the records and rewrites index.json; the page adds
      // them back to its own list, which is the behaviour under test.
      const back = removed.splice(0);
      back.forEach(record => pool.push(record));
      return {record: back[0], records: back, count: back.length,
        failures: [], warning: null};
    },
    '/api/render-previews': body => {
      if (body) {
        calls.push({endpoint: '/api/render-previews', body});
        return {total: (body.paths || []).length};
      }
      // Report the job as running once, so the test can change the selection
      // while it is in flight; the second poll finishes it.
      polls += 1;
      if (polls === 1) {
        return {running: true, done: 2, failed: 0, cursor: 2, updates: [], total: 6};
      }
      return {running: false, done: 6, failed: 0, cursor: 6, updates: [], total: 6};
    }
  }
});

const scrolls = [];
const KK = app.load(SCRIPTS);
const $ = id => app.byId.get(id);
app.window.scrollTo = position => scrolls.push(position);

const grid = () => $('grid');
const emit = (type, event) => {
  for (const handler of grid()._events[type] || []) handler(event);
};
const click = (node, extra = {}) => {
  emit('pointerdown', {target: node, clientX: 0, clientY: 0, button: 0, ...extra});
  emit('pointerup', {});
};

/* Cards have no layout here, so give each one a plausible rectangle: three
 * columns of 280x180, 300x200 apart. Sweeping 0,0 -> 620,400 must catch the
 * first six. */
function layout(page = 0) {
  grid().children.forEach((card, index) => {
    const column = index % 3, row = Math.floor(index / 3);
    card.getBoundingClientRect = () => ({
      left: column * 300, top: row * 200,
      right: column * 300 + 280, bottom: row * 200 + 180
    });
  });
}
const sweep = (from, to) => {
  emit('pointerdown', {target: grid(), clientX: from[0], clientY: from[1], button: 0});
  emit('pointermove', {clientX: to[0], clientY: to[1]});
  emit('pointerup', {});
};

async function main() {
  await KK.deps.reload();
  KK.openView('mods');
  assert.equal(grid().children.length, 36);

  /* The preview filter describes what is inside a package. The selection is
   * already shown in full -- highlighted cards, ticked boxes, a running count --
   * so there is deliberately no "picked only" entry to mirror it. */
  const imageSelect = $('image');
  assert.equal(imageSelect.children.some(node => node.value === 'picked'), false,
    'the preview filter does not offer a "picked" entry');

  /* --- one card, through its own button -------------------------------- */
  const card = grid().children[0];
  const target = card.querySelector('.delete');
  assert.ok(target, 'first card has a disable button');
  assert.equal(target.disabled, false, 'disable is enabled when the manager is reachable');

  await target.onclick({target, preventDefault() {}});
  assert.equal(calls[0].endpoint, '/api/delete', 'delete goes through the API');
  assert.deepEqual(calls[0].body.paths, ['pack000.zipmod'], 'the right package is sent');
  assert.equal(KK.data.catalog.length, 40, 'the disabled package leaves the catalog');
  assert.match($('status').textContent, /已停用 1 个 Mod/);
  assert.match($('status').textContent, /test warning/, 'server warnings surface');
  assert.equal($('undo').disabled, false, 'undo becomes available');
  assert.match($('undo').textContent, /撤销删除（1）/);

  await $('undo').onclick();
  assert.equal(calls[1].endpoint, '/api/restore');
  assert.deepEqual(calls[1].body.ids, ['undo-0-0']);
  assert.equal(KK.data.catalog.length, 41, 'the package comes back');
  assert.match($('status').textContent, /已恢复 1 个 Mod/);
  assert.equal($('undo').disabled, true, 'undo stack is empty again');

  /* --- click selection -------------------------------------------------- */
  KK.openView('mods');
  layout();
  click(grid().children[0]);
  assert.ok(grid().children[0].classList.contains('selected'), 'a click selects');
  assert.equal(grid().children[0].getAttribute('aria-selected'), 'true');
  assert.equal(grid().children[0].querySelector('.pick input').checked, true,
    'the card checkbox follows the selection');
  assert.equal(grid().children[0].querySelector('.meta .pick'), null,
    'the selection box lives on the cover, not in the text block');
  assert.match($('bulkCount').textContent, /已选 1 个/);
  assert.match($('deleteSelected').textContent, /删除选中（1）/);

  click(grid().children[1]);
  assert.match($('bulkCount').textContent, /已选 2 个/, 'clicks accumulate');

  click(grid().children[0]);
  assert.match($('bulkCount').textContent, /已选 1 个/, 'clicking again clears that card');
  assert.ok(!grid().children[0].classList.contains('selected'));

  /* Clearing is a selection-only operation: the grid behind it is untouched,
   * because no filter depends on the selection any more. */
  $('clearSelection').onclick();
  assert.match($('bulkCount').textContent, /未选择/);
  assert.equal(grid().children.length, 36, 'the grid survives clearing the selection');

  click(grid().children[0]);
  assert.match($('bulkCount').textContent, /已选 1 个/);

  /* A control inside the card never toggles the card itself. */
  const box = grid().children[2].querySelector('.pick input');
  click(box);
  assert.equal(KK.data.catalog.filter(record => record.path === 'pack002.zipmod').length, 1);
  assert.ok(!grid().children[2].classList.contains('selected'),
    'clicking the checkbox is not a card click');

  /* Ticking the box selects, the way a batch UI has to work for someone who
   * never discovers the drag shortcut. */
  box.checked = true; box.onchange();
  assert.ok(grid().children[2].classList.contains('selected'), 'the box selects a card');
  assert.match($('bulkCount').textContent, /已选 2 个/);
  box.checked = false; box.onchange();
  assert.ok(!grid().children[2].classList.contains('selected'), 'unticking clears it');
  assert.match($('bulkCount').textContent, /已选 1 个/);

  /* --- whole page, then clear ------------------------------------------ */
  $('pickPage').onclick();
  assert.equal($('bulkCount').textContent, '已选 36 个');
  $('clearSelection').onclick();
  assert.equal($('bulkCount').textContent.startsWith('未选择'), true);

  /* --- dragging the text block stays a text selection -------------------- */
  const meta = grid().children[0].querySelector('.meta');
  emit('pointerdown', {target: meta, clientX: 10, clientY: 10, button: 0});
  emit('pointermove', {clientX: 260, clientY: 160});
  emit('pointerup', {});
  assert.equal($('bulkCount').textContent.startsWith('未选择'), true,
    'a drag that starts on the text selects nothing');
  assert.equal(grid().children.filter(node => node.classList.contains('selected')).length, 0);
  assert.equal((app.document.body.children || []).filter(
    node => node.classList && node.classList.contains('marquee')).length, 0,
    'no sweep rectangle is drawn over the text');

  /* A plain click on that same text still toggles the card. */
  emit('pointerdown', {target: meta, clientX: 10, clientY: 10, button: 0});
  emit('pointerup', {});
  assert.ok(grid().children[0].classList.contains('selected'), 'a click on the text selects');
  $('clearSelection').onclick();

  /* --- drag selection --------------------------------------------------- */
  layout();
  sweep([0, 0], [620, 400]);
  assert.equal($('bulkCount').textContent, '已选 6 个', 'the sweep catches two rows of three');
  assert.equal(grid().children.filter(node => node.classList.contains('selected')).length, 6);
  assert.ok(!grid().children.some(node => node.classList.contains('marquee-hit')),
    'the sweep rectangle is cleaned up');

  /* --- batch delete, refused then accepted ------------------------------ */
  confirmAnswer = false;
  await $('deleteSelected').onclick();
  assert.equal(calls.length, 2, 'a refused confirmation sends nothing');
  assert.match($('status').textContent, /已取消/);
  assert.equal(KK.data.catalog.length, 41);

  confirmAnswer = true;
  await $('deleteSelected').onclick();
  assert.equal(calls[2].endpoint, '/api/delete');
  assert.equal(calls[2].body.paths.length, 6, 'one request carries the whole selection');
  assert.equal(KK.data.catalog.length, 35, 'all six leave the catalog at once');
  assert.equal($('bulkCount').textContent.startsWith('未选择'), true,
    'the selection is consumed by the delete');
  assert.match($('undo').textContent, /撤销删除（6）/);

  await $('undo').onclick();
  assert.equal(calls[3].endpoint, '/api/restore');
  assert.equal(calls[3].body.ids.length, 6, 'the whole batch comes back together');
  assert.equal(KK.data.catalog.length, 41);
  assert.match($('status').textContent, /已恢复 6 个 Mod/);

  /* --- rendering consumes the selection ---------------------------------
   * The picked set only says what to render. Keeping it after the job would
   * leave the next bulk action aimed at packages already handled. */
  KK.openView('mods');
  layout();
  sweep([0, 0], [620, 400]);
  assert.equal($('bulkCount').textContent, '已选 6 个');
  confirmAnswer = true;
  const job = $('renderPreviews').onclick();
  await new Promise(resolve => setTimeout(resolve, 150));
  // Picking during a run belongs to the next batch, not to this one.
  click(grid().children[8]);
  assert.equal($('bulkCount').textContent, '已选 7 个');
  await job;
  const render = calls[calls.length - 1];
  assert.equal(render.endpoint, '/api/render-previews', 'render goes through the API');
  assert.equal(render.body.paths.length, 6, 'only the selected packages are rendered');
  assert.equal($('bulkCount').textContent, '已选 1 个',
    'the six rendered packages drop out, the one picked mid-run stays');
  assert.equal(grid().children.filter(node => node.classList.contains('selected')).length, 1,
    'no rendered card keeps its check mark');
  assert.match($('status').textContent, /已取消选中 6 个/);
  $('clearSelection').onclick();

  assert.ok(scrolls.length >= 4, 'the viewport is restored after each rebuild');
  console.log('PASS: single and batch deletes each take one request, click and drag '
    + 'selection stay in sync with the cards, a refused confirmation sends nothing, '
    + 'and a render consumes the selection it was started from');
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
