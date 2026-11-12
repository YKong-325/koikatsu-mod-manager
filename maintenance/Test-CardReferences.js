/* Following up a reference count.
 *
 * "被 4 张服装卡引用" answers whether a package is used; the next question is
 * by whom, and answering it used to mean switching to the dependency view and
 * searching for a path. Each line is now a button that opens the cards behind
 * it. The paths already exist in the dependency snapshot, so only the
 * thumbnails have to come from the server -- and they are asked for by package
 * path and kind, not by sending a thousand paths back.
 */
const assert = require('node:assert/strict');
const {createApp, descendants} = require('./DomShim');
const {catalog, library, deps} = require('./Fixture');

const SCRIPTS = ['mod-search.js', 'app-core.js', 'media-thumbs.js', 'view-mods.js',
  'view-trash.js', 'card-refs.js', 'plugin-deps.js'];

async function main() {
  const app = createApp({catalog, library, deps});
  const KK = app.load(SCRIPTS);
  const $ = id => app.byId.get(id);

  KK.openView('mods');

  const dialog = app.byId.get('cardRefsDialog');
  assert.ok(dialog, 'the reference list is one dialog, not one per card');
  const title = dialog.children[0].children[0];
  const body = dialog.querySelector('.card-refs-body');

  const cardFor = path => $('grid').children.find(card => card.dataset.path === path);
  const countButton = (path, text) => descendants(cardFor(path))
    .find(node => node.tagName === 'BUTTON' && node.textContent === text);

  const roleButton = countButton('pack000.zipmod', '被 1 张角色卡引用');
  assert.ok(roleButton, 'a count is a button, so it can be followed up');
  assert.equal(roleButton.title, '查看这 1 张角色卡',
    'and says what pressing it does');

  let asked = '';
  let answer = {total: 1, cards: [
    {path: 'chara/a.png', file: 'a.png', kind: '角色卡', thumb: 'library_thumbs/a.jpg'}
  ]};
  KK.api.read = async endpoint => { asked = endpoint; return answer; };

  await roleButton.onclick();

  assert.equal(asked, '/api/library/cards?mod=pack000.zipmod&kind=cards',
    'the server is asked for one package and one kind of card');
  assert.equal(dialog.open, true, 'the list opens over the grid');
  assert.match(title.textContent, /被 1 张角色卡引用/);
  assert.match(title.textContent, /Pack 0/, 'the title names the package as well');

  const rows = body.querySelector('.card-refs-list').children;
  assert.equal(rows.length, 1);
  assert.match(rows[0].textContent, /a\.png/, 'the card file name is shown');
  assert.match(rows[0].textContent, /chara\/a\.png/, 'with its path');
  const thumb = rows[0].children[0];
  assert.equal(thumb.dataset.src, 'library_thumbs/a.jpg',
    'the thumbnail is what makes two similarly named cards tellable apart');
  assert.equal(rows[0].className, 'library-card',
    'the row keeps the preview hooks, so the picture can be opened full size');

  /* The two other counts ask their own question: a scene and an outfit are not
   * interchangeable when deciding whether a package can go. */
  const sceneButton = countButton('pack003.zipmod', '被 1 张场景卡引用');
  await sceneButton.onclick();
  assert.match(asked, /kind=scenes$/, 'scene cards are asked for as scenes');

  const outfitButton = countButton('pack004.zipmod', '被 1 张服装卡引用');
  answer = {total: 1, cards: [
    {path: 'coordinate/d.png', file: 'd.png', kind: '服装卡', thumb: null}
  ]};
  await outfitButton.onclick();
  assert.match(asked, /kind=outfits$/, 'outfit cards are asked for as outfits');
  assert.match(body.textContent, /暂无缩略图/,
    'a card without a thumbnail still gets a row');

  /* The dependency snapshot can name a card the library no longer holds -- it
   * was deleted after the snapshot was taken. The count is still true, so the
   * list says how many it could not draw instead of quietly showing fewer. */
  answer = {total: 4, cards: [
    {path: 'chara/a.png', file: 'a.png', kind: '角色卡', thumb: 'library_thumbs/a.jpg'},
    {path: 'chara/b.png', file: 'b.png', kind: '角色卡', thumb: null}
  ]};
  await roleButton.onclick();
  assert.match(body.textContent, /另有 2 张卡片不在资源卡目录里/,
    'cards missing from the library are counted, not dropped silently');

  /* A server that cannot answer must not leave "正在读取卡片…" on screen. */
  KK.api.read = async () => { throw new Error('连接中断'); };
  await roleButton.onclick();
  assert.match(body.textContent, /卡片读取失败：连接中断/);

  dialog.children[0].children[1].onclick();
  assert.equal(dialog.open, false, 'the close button closes the list');

  console.log('PASS: a reference count opens the cards behind it, per kind, with '
    + 'thumbnails and an honest count for cards the library no longer holds');
}

main().catch(error => {
  console.error(error);
  process.exit(1);
});
