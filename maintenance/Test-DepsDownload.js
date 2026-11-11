/* The missing-mod download, seen from the page.
 *
 * The public integration pack ships only a fraction of the GUIDs a card can ask
 * for, so "found nothing" is the normal outcome, not an error. The page used to
 * report that through the status line -- which every render() immediately
 * overwrote -- so a finished round looked exactly like a button that did
 * nothing. The outcome now lives in its own block; these checks keep it there.
 */
const assert = require('node:assert/strict');
const {createApp} = require('./DomShim');
const {catalog, library, deps} = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'view-trash.js', 'plugin-deps.js'];

async function main() {
  const app = createApp({catalog, library, deps, confirmAnswer: () => true});
  const KK = app.load(SCRIPTS);
  const $ = id => app.byId.get(id);

  /* Stub the two endpoints the download drives. Everything else in the page
   * stays live, so the render() that follows the job runs for real. */
  let job = {
    running: true, stage: '匹配', message: '正在读取公共索引…', requested: 1,
    total: 1, done: 0, failed: 0, matched: 0, cursor: 0, updates: [], error: '',
    problems: [], unmatched: []
  };
  KK.api.mutate = async () => ({ok: true, total: 1});
  KK.api.read = async () => ({...job, cursor: 0});

  KK.openView('deps');
  const download = $('downloadMissing');
  assert.match(download.textContent, /下载缺失 Mod（1）/, 'one GUID is missing');
  assert.equal($('dependencyReport').hidden, true, 'nothing is claimed before a run');

  /* A round where the only GUID is not in the public pack. */
  job = {...job, running: false, stage: '完成', total: 0, matched: 0,
    message: '公共索引里没有找到这些 Mod',
    unmatched: [{guid: 'absent', authors: ['someone'], websites: ['https://example.invalid/x'],
      cards: 1}]};
  await download.onclick();

  const report = $('dependencyReport');
  assert.equal(report.hidden, false, 'the outcome survives the re-render that follows');
  assert.match(report.textContent, /请求 1 个 GUID/);
  assert.match(report.textContent, /公共索引里找到 0 个/);
  assert.match(report.textContent, /安装 0 个/);
  assert.match(report.textContent, /1 个公共索引里没有/);
  assert.match(report.textContent, /absent/, 'the unanswered GUID is listed');
  assert.match(report.textContent, /someone/, 'with the author to search for');
  const summary = report.querySelector('summary');
  assert.ok(summary, 'the list is folded away so it does not push the grid down');
  assert.match(summary.textContent, /查看没找到的 1 个/);
  const link = report.querySelector('a');
  assert.equal(link.href, 'https://example.invalid/x',
    'the page the card recorded is one click away');

  /* A round that does install something reports both numbers. */
  job = {running: false, stage: '完成', message: '', requested: 3, total: 2,
    matched: 2, done: 2, failed: 1, cursor: 1, error: '', problems: ['guid.x：连接中断'],
    updates: [{guid: 'a', file: 'a.zipmod', path: 'a.zipmod', bytes: 1},
      {guid: 'b', file: 'b.zipmod', path: 'b.zipmod', bytes: 1}],
    unmatched: [{guid: 'c', authors: [], websites: [], cards: 2}]};
  await download.onclick();
  assert.match($('dependencyReport').textContent, /请求 3 个 GUID/);
  assert.match($('dependencyReport').textContent, /安装 2 个/);
  assert.match($('dependencyReport').textContent, /1 个下载失败/);
  assert.match($('dependencyReport').textContent, /guid\.x：连接中断/,
    'the per-package failure reason is shown, not just counted');

  /* A round where one GUID is only shipped as a Sunshine build and one is not
   * in the index at all. The Sunshine build is still downloaded -- the game,
   * not this program, decides whether it loads -- so the answer has to say it
   * was installed and why it might not show up, instead of implying a failure. */
  job = {running: false, stage: '完成', message: '', requested: 2, total: 2,
    matched: 2, done: 2, failed: 0, cursor: 1, error: '', problems: [],
    scanned: 7469, unreadable: 79, updates: [
      {guid: 'h0stile', file: 'h0stile.zipmod', path: 'h0stile.zipmod', bytes: 1}],
    fallbacks: [{guid: 'h0stile', name: 'H0STILE', version: '1.0',
      game: 'Koikatsu Sunshine', url: 'https://example.invalid/h0stile.zipmod',
      reason: 'kks'}],
    unmatched: [{guid: 'gone', authors: [], websites: [], cards: 1, reason: 'absent'}]};
  await download.onclick();
  const mixed = $('dependencyReport').textContent;
  assert.match(mixed, /1 个公共索引里没有/, 'only the truly absent one is called absent');
  assert.match(mixed, /其中 1 个只有 KKS 版/, 'the other game\'s build is counted apart');
  assert.match(mixed, /已经照样下载/, 'it is downloaded, not withheld');
  assert.match(mixed, /游戏加载时会按这个标注决定/, 'the game is named as the one who decides');
  assert.doesNotMatch(mixed, /索引里只有 KKS 版/,
    'the row is not listed as missing any more, because it was downloaded');
  /* How many archives were read belongs to the log, not the page: the answer
   * line stays one line long. */
  assert.doesNotMatch(mixed, /核对了|读不出来/,
    'the scan coverage is no longer spelled out under the answer');

  /* A pack folder that failed to list must not leave "not in the index"
   * standing as a fact: that is the one conclusion this page can get wrong. */
  job = {...job, unreadPacks: ['Sideloader Modpack - Exclusive KKS']};
  await download.onclick();
  assert.match($('dependencyReport').textContent, /没读到/,
    'an unread pack folder is called out');
  assert.match($('dependencyReport').textContent, /结论可能不准/,
    'and the answer above it is marked as provisional');

  /* Starting the next round clears the previous answer instead of stacking. */
  job = {running: true, stage: '索引', message: '正在读取公共索引…', requested: 1,
    total: 1, done: 0, failed: 0, matched: 0, cursor: 0, updates: [], error: '',
    problems: [], unmatched: []};
  const pending = download.onclick();
  assert.equal($('dependencyReport').hidden, true, 'a new run clears the old report');
  job = {...job, running: false, stage: '完成'};
  await pending;

  console.log('PASS: a download round reports what it asked for, what the index '
    + 'held and what it installed, lists the GUIDs it could not answer, and keeps '
    + 'that answer on screen after the grid is rebuilt');
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
