/* Edge scrolling while sweeping a selection.
 *
 * The grid is taller than the window, so a sweep that starts at the top cannot
 * reach the last row in one gesture. Holding the pointer in the strip along an
 * edge has to scroll the page, keep the rectangle anchored to the content it
 * started from, and stop as soon as the job is done.
 */
const assert = require('node:assert/strict');
const {createApp} = require('./DomShim');
const fixture = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'card-refs.js', 'plugin-deps.js'];

const VIEWPORT = 900;
const ROW = 200;          // each fake row of cards is 200px tall
const START_Y = 100;      // where the sweep is pressed, in view coordinates

const app = createApp({
  protocol: 'http:',
  catalog: fixture.catalog.slice(),
  library: fixture.library,
  deps: fixture.deps,
  viewportHeight: VIEWPORT,
  routes: {
    '/api/session': () => ({token: 'test-token'}),
    '/api/deps': () => fixture.deps
  }
});

const KK = app.load(SCRIPTS);
const $ = id => app.byId.get(id);
const win = app.window;
const grid = () => $('grid');
const emit = (type, event) => {
  for (const handler of grid()._events[type] || []) handler(event);
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/* Cards are laid out three to a row, 280x180 at 300x200 steps. The top is
 * expressed in view coordinates, so the grid really does move when the page
 * scrolls — which is the whole point of the test. */
function layout() {
  grid().children.forEach((card, index) => {
    const column = index % 3, row = Math.floor(index / 3);
    card.getBoundingClientRect = () => ({
      left: column * 300,
      top: row * ROW - win.scrollY,
      right: column * 300 + 280,
      bottom: row * ROW - win.scrollY + 180
    });
  });
}

const marqueeBox = () => app.document.querySelector('.marquee');

async function waitFor(predicate, timeout = 4000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (predicate()) return true;
    await sleep(20);
  }
  return false;
}

function pickedCount() {
  return grid().children.filter(card => card.classList
    && card.classList.contains('selected')).length;
}

async function main() {
  await KK.deps.reload();
  KK.openView('mods');
  layout();

  assert.equal(win.scrollY, 0, 'the page starts at the top');
  const rowsInView = grid().children.length;
  assert.ok(rowsInView > 9, 'the first page holds more than one screen of cards');

  /* --- holding the pointer at the bottom edge scrolls the page ----------- */
  emit('pointerdown', {target: grid(), clientX: 10, clientY: START_Y, button: 0});
  emit('pointermove', {clientX: 620, clientY: VIEWPORT - 20});
  const scrolled = await waitFor(() => win.scrollY >= ROW + 40);
  assert.ok(scrolled, 'resting against the bottom edge scrolls the grid down');
  assert.ok(marqueeBox(), 'the sweep rectangle is still on screen while it scrolls');

  /* --- the origin stays on the content it was pressed on ----------------- */
  const box = marqueeBox();
  const expectedTop = START_Y - win.scrollY;
  assert.ok(Math.abs(parseFloat(box.style.top) - expectedTop) <= 2,
    `the rectangle moves with the content (top ${box.style.top}, expected ${expectedTop})`);

  /* --- releasing in the middle stops the scroll -------------------------- */
  emit('pointermove', {clientX: 620, clientY: VIEWPORT / 2});
  const rested = win.scrollY;
  await sleep(160);
  assert.ok(Math.abs(win.scrollY - rested) <= 1,
    'a pointer back in the middle no longer scrolls');

  /* --- and it resumes when the pointer returns to the edge --------------- */
  emit('pointermove', {clientX: 620, clientY: VIEWPORT - 10});
  const resumed = await waitFor(() => win.scrollY >= rested + ROW);
  assert.ok(resumed, 'returning to the edge resumes the scroll');

  emit('pointerup', {});
  const after = win.scrollY;
  await sleep(160);
  assert.equal(win.scrollY, after, 'releasing stops the timer');
  assert.equal(marqueeBox(), null, 'the rectangle is cleaned up');

  /* The sweep covered roughly three screens of cards, not just the one row a
   * non-scrolling gesture could have reached. */
  await sleep(0);
  const picked = pickedCount();
  assert.ok(picked >= 9,
    `scrolling during the sweep selects cards below the fold (picked ${picked})`);
  assert.match($('status').textContent, /框选了 \d+ 个 Mod/);

  /* --- an upward sweep scrolls back -------------------------------------- */
  const down = win.scrollY;
  emit('pointerdown', {target: grid(), clientX: 10, clientY: VIEWPORT - 60, button: 0});
  emit('pointermove', {clientX: 10, clientY: 8});
  const back = await waitFor(() => win.scrollY < down - 100);
  assert.ok(back, 'resting against the top edge scrolls back up');
  emit('pointerup', {});

  console.log('PASS: resting the sweep pointer against an edge scrolls the grid '
    + 'both ways, the rectangle keeps its origin on the page, and the timer '
    + 'stops on release or when the pointer leaves the edge');
}

main().catch(error => { console.error(error); process.exit(1); });
