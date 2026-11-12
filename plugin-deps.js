/* Plugin: missing-mod report.
 *
 * Own view, own filters, own pager. It reads KK.deps (computed once on the
 * server) and never touches the Mod view's filter state, so entering and
 * leaving it cannot disturb where the user was.
 *
 * One question only: which cards still miss packages, and how to fetch them.
 * The other question this plugin answers -- which installed packages no card
 * refers to -- belongs to housekeeping rather than to a broken card, so it is
 * a facet on the Mod grid where packages are actually disabled, not a second
 * list here.
 *
 * This is also the only place that needs the card records themselves, so they
 * are fetched on first use instead of being shipped with every page load.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {data, el, setStatus} = KK;
  const SIZE = 40;

  const state = {scope: '', filter: 'missing', text: '', page: 0, picked: new Set()};
  let list = [];
  let cardsLoaded = false;
  let fetching = false;

  const controls = el('div');
  controls.id = 'dependencyControls';

  const scope = el('select');
  scope.setAttribute('aria-label', '资源类型');
  [['', '全部类型'], ['角色卡', '角色卡'], ['场景', '场景'], ['服装卡', '服装卡']]
    .forEach(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      scope.append(option);
    });

  const filter = el('select');
  filter.setAttribute('aria-label', '检查状态');

  const query = el('input');
  query.type = 'search';
  query.placeholder = '文件名 / 路径 / GUID / Mod 名称';
  query.setAttribute('aria-label', '搜索检查结果');

  controls.append(scope, filter, query);

  const element = el('section');
  element.id = 'dependencyPanel';

  const summary = el('div', undefined, 'detection-summary');
  summary.id = 'dependencySummary';
  /* The outcome of a download needs somewhere to live that render() will not
   * overwrite: the summary and the status line are both rebuilt on every pass,
   * so a "nothing was found" run looked exactly like a button that did nothing.
   * Most of the GUIDs a card asks for are simply not in the public pack, and
   * that has to be said out loud. */
  const report = el('div', undefined, 'detection-report');
  report.id = 'dependencyReport';
  report.hidden = true;
  const grid = el('div', undefined, 'library-grid');
  grid.id = 'dependencyList';
  element.append(summary, report, grid);

  /* Missing packages can be fetched from the public integration index: the
   * server resolves each GUID to an archive by reading manifests only, then
   * downloads the few that matched. One job at a time, cancellable.
   *
   * A full sweep can be thousands of GUIDs and half an hour of manifest reads,
   * which is rarely what someone wants after spotting one broken card. Ticking
   * cards narrows the job to just their GUIDs; ticking nothing keeps the old
   * "everything that is missing" behaviour, which is still the right default. */
  const downloadButton = el('button', '下载缺失 Mod', 'detection-download');
  downloadButton.id = 'downloadMissing';
  downloadButton.type = 'button';
  downloadButton.onclick = () => fetchMissing();

  /* This row is the page's only action, and it counts the cards you ticked
   * further down the grid -- so it docks to the top of the window instead of
   * scrolling away with the summary it used to sit in. */
  const actions = el('div', undefined, 'detection-actions');
  const pickNote = el('span', '', 'notice');
  pickNote.id = 'dependencyPickNote';

  const pickAllButton = el('button', '全选当前结果', 'detection-plain');
  pickAllButton.id = 'dependencyPickAll';
  pickAllButton.type = 'button';
  pickAllButton.onclick = () => {
    for (const resource of list) {
      const verdict = KK.deps.card(resource.path);
      if (verdict && verdict.state === 'missing') state.picked.add(resource.path);
    }
    render();
  };

  const clearButton = el('button', '清除选择', 'detection-plain');
  clearButton.id = 'dependencyClearPick';
  clearButton.type = 'button';
  clearButton.onclick = () => {
    state.picked.clear();
    render();
  };

  function missingGuids() {
    const raw = KK.deps.raw;
    const seen = new Set();
    if (!raw || !raw.cards) return seen;
    // Only the ticked cards contribute once there is a selection; a stale tick
    // on a card that has since been fixed simply disappears from the set.
    const paths = state.picked.size
      ? [...state.picked].filter(path => raw.cards[path])
      : Object.keys(raw.cards);
    for (const path of paths) {
      for (const guid of raw.cards[path].missing || []) seen.add(guid);
    }
    return seen;
  }

  async function ensureCards() {
    if (cardsLoaded) return;
    cardsLoaded = true;
    if (data.library.length || location.protocol !== 'http:') return;
    try {
      const records = await KK.api.read('/api/library');
      data.library = records;
      render();
    } catch (error) {
      setStatus(error.message === KK.api.OFFLINE
        ? KK.api.OFFLINE
        : '资源卡数据读取失败：' + error.message);
    }
  }

  /* Two numbers, in the order the reader cares about: what the index answered,
   * what landed in mods. The GUID count that was asked for, which game's index
   * was used and which build a manifest declares are all method, not outcome --
   * saying them turned a one-line answer into a paragraph nobody finished.
   * A failed download is the one reason to look further, so it keeps a suffix;
   * the GUIDs nothing answered for are counted by the fold right below, which
   * is where the reader looks for them anyway. */
  function paintReport(job) {
    const installed = (job.updates || []).length;
    const rejected = job.unmatched || [];
    const matched = job.matched === undefined || job.matched === null
      ? (job.total || 0) : job.matched;
    const parts = [`匹配 ${matched.toLocaleString()} 个`,
      `下载 ${installed.toLocaleString()} 个`];
    if (job.failed) parts.push(`${job.failed.toLocaleString()} 个下载失败`);

    const children = [el('strong', '上次下载'), el('span', parts.join(' · '))];
    if (job.error) {
      children.push(el('span', job.error, 'notice'));
      // The failure mode that reads as a bug but is not: a browser reaches the
      // public index through its own proxy, while this program goes out with
      // the system settings. Saying that here saves a round of "it works in my
      // browser" reports.
      if (/公共整合站|整合包目录/.test(job.error)) {
        children.push(el('span',
          '浏览器能打开这个网址、这里却报错，通常是程序没走浏览器的代理：'
          + '请开启系统代理（浏览器插件里的代理对它无效）或换一个网络再试。', 'notice'));
      }
    }
    for (const problem of job.problems || []) {
      children.push(el('span', problem, 'notice'));
    }
    /* A pack folder that failed to list looks exactly like "the pack does not
     * ship it", which is the one wrong conclusion this page must not invite. */
    if ((job.unreadPacks || []).length) {
      children.push(el('span',
        `有 ${job.unreadPacks.length} 个整合包目录这次没读到（${job.unreadPacks.join('、')}），`
        + '所以上面“公共索引里没有”的结论可能不准，换个时间再试一次。', 'notice'));
    }
    if (rejected.length) {
      const details = el('details');
      details.append(el('summary', `查看没找到的 ${rejected.length.toLocaleString()} 个`));
      const list = el('ul', undefined, 'detection-missing');
      for (const row of rejected.slice(0, 200)) {
        const item = el('li');
        item.append(el('code', row.guid));
        const authors = (row.authors || []).join('、');
        if (authors) item.append(el('span', ' · ' + authors));
        if (row.cards) item.append(el('span', ` · ${row.cards} 张卡`, 'notice'));
        const site = (row.websites || [])[0];
        if (site) {
          const link = el('a', '来源页面');
          link.href = site;
          link.target = '_blank';
          link.rel = 'noreferrer';
          item.append(' ', link);
        }
        list.append(item);
      }
      details.append(list);
      if (rejected.length > 200) {
        details.append(el('span', `…还有 ${(rejected.length - 200).toLocaleString()} 个未列出`,
          'notice'));
      }
      const copy = el('button', '复制全部 GUID', 'detection-plain');
      copy.id = 'dependencyCopyMissing';
      copy.type = 'button';
      copy.onclick = async () => {
        const text = rejected.map(row => row.guid).join('\n');
        try {
          if (typeof navigator === 'undefined' || !navigator.clipboard) {
            throw new Error('这个浏览器不支持剪贴板');
          }
          await navigator.clipboard.writeText(text);
          setStatus(`已复制 ${rejected.length.toLocaleString()} 个 GUID`);
        } catch (error) {
          setStatus('复制失败：' + error.message);
        }
      };
      details.append(copy);
      children.push(details);
    }
    report.replaceChildren(...children);
    report.hidden = false;
  }

  async function fetchMissing() {
    if (fetching) {
      try {
        await KK.api.mutate('/api/missing-mods/job/cancel', {});
        setStatus('正在停止下载…');
      } catch (error) {
        setStatus('取消失败：' + error.message);
      }
      return;
    }
    const guids = [...missingGuids()];
    if (!guids.length) {
      setStatus('没有缺失的 Mod');
      return;
    }
    const scopeNote = state.picked.size
      ? `只处理你勾选的 ${state.picked.size} 张资源卡所缺的 GUID。`
      : '当前没有勾选任何卡片，所以会处理所有资源卡缺的 GUID。';
    const question = `在公共整合站里查找并下载这 ${guids.length} 个缺失的 Mod？\n\n`
      + scopeNote + '\n'
      + '先读取公共索引（约 7500 个包），再逐个只读包内 manifest 来匹配 GUID。'
      + '第一次要读半小时以上，读完当场就能下，没有等待期；读到的目录会存下来，'
      + '七天内再点都直接跳过这一步。名字对得上的包排在前面，'
      + '找到即可。只下载真正匹配到的包，装进 mods 目录并自动重建索引，'
      + '过程中可以随时点“停止”。';
    if (!window.confirm(question)) return;
    fetching = true;
    report.hidden = true;
    report.replaceChildren();
    paintDownload({stage: '准备', message: '正在提交任务…'});
    let cursor = 0;
    try {
      const started = await KK.api.mutate('/api/missing-mods/job', {guids});
      setStatus(`共 ${started.total} 个待查找的 GUID`);
      for (;;) {
        const job = await KK.api.read('/api/missing-mods/job?cursor=' + cursor);
        cursor = job.cursor || cursor;
        paintDownload(job);
        if (!job.running) {
          paintReport(job);
          break;
        }
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
      await KK.deps.reload();
      // The cards just serviced are usually complete now, so their ticks would
      // point at nothing; start the next round from a clean slate.
      state.picked.clear();
    } catch (error) {
      setStatus('下载失败：' + error.message);
      report.replaceChildren(el('strong', '上次下载'),
        el('span', '下载失败：' + error.message, 'notice'));
      report.hidden = false;
    } finally {
      fetching = false;
      downloadButton.textContent = '下载缺失 Mod';
      downloadButton.disabled = false;
      render();
    }
  }

  function paintDownload(job) {
    const stage = job.stage || '准备';
    const progress = job.total ? ` ${job.done}/${job.total}` : '';
    downloadButton.textContent = fetching ? `停止（${stage}${progress}）` : '下载缺失 Mod';
    downloadButton.disabled = !fetching;
    if (job.message) setStatus(job.message);
  }

  const FILTERS = [['missing', '缺失 Mod'], ['complete', '依赖齐全'], ['unknown', '依赖未识别'],
    ['', '全部资源卡']];

  function syncFilters() {
    filter.replaceChildren(...FILTERS.map(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      return option;
    }));
    if (!FILTERS.some(([value]) => value === state.filter)) state.filter = FILTERS[0][0];
    filter.value = state.filter;
  }

  /* Two packages can carry the same GUID. Sideloader then loads only one of
   * them, silently; which one it keeps is its own rule (highest version, then
   * longest path), so the manager cannot promise that the copy the user is
   * looking at is the copy in effect. What it can do is say the collision
   * exists before the user wonders why a mod does nothing.
   *
   * The catalog is the authority here rather than deps.json: deps carries only
   * a boolean per package, and a banner is more useful when it can say how many
   * packs share the GUID. Walking 7 000 records once beats rescanning them for
   * every card on every page.
   */
  let guidGroups = null;
  let guidGroupsCount = -1;
  function byGuid() {
    if (guidGroups && guidGroupsCount === data.catalog.length) return guidGroups;
    guidGroups = new Map();
    for (const record of data.catalog) {
      const guid = (record.guid || '').trim().toLowerCase();
      if (!guid) continue;
      const rows = guidGroups.get(guid);
      if (rows) rows.push(record);
      else guidGroups.set(guid, [record]);
    }
    // Deleting a package is the one thing that can end a collision, and it
    // shortens the catalog, so the length is enough to notice it.
    guidGroupsCount = data.catalog.length;
    return guidGroups;
  }

  function sharesGuid(record) {
    const guid = (record.guid || '').trim().toLowerCase();
    if (!guid) return [];
    return (byGuid().get(guid) || []).filter(other => other.path !== record.path);
  }

  function guidClashes() {
    let groups = 0, packs = 0;
    for (const rows of byGuid().values()) {
      if (rows.length < 2) continue;
      groups += 1;
      packs += rows.length;
    }
    return {groups, packs};
  }

  function conflictBanner(record) {
    const peers = sharesGuid(record);
    const others = peers.length === 1 ? '另有 1 个包使用同一 GUID'
      : peers.length ? `另有 ${peers.length} 个包使用同一 GUID`
        : '与其他包使用了同一 GUID';
    const banner = el('div', `GUID 冲突：${others}，游戏只会加载其中一个`,
      'conflict-flag');
    const names = peers.map(other => other.file || other.path).filter(Boolean);
    if (names.length) {
      banner.title = `同一 GUID 的包：${names.join('、')}。` +
        '游戏只会加载其中一个，其余被跳过；留下哪个由包内版本号决定，' +
        '版本相同时按路径长度。停用多余的那个即可消除冲突。';
    }
    return banner;
  }

  /* A collision deserves a filter of its own: the banner explains, the dropdown
   * finds. It only appears when the catalog actually holds one -- on a clean
   * install it would be a control with a single, useless answer. */
  KK.registerModFacet({
    id: 'conflict',
    label: 'GUID 冲突',
    options() {
      const {groups, packs} = guidClashes();
      return groups ? [['', '全部包'], ['only', `只看冲突（${packs} 个包）`]] : [];
    },
    match(record, value) {
      if (value !== 'only') return true;
      const verdict = KK.deps.mod(record.path);
      return Boolean((verdict && verdict.duplicateGuid) || sharesGuid(record).length);
    }
  });

  function numbers() {
    const deps = KK.deps.raw;
    if (!deps) {
      // Nothing to count, so nothing to offer: keep the docked bar meaningful
      // (an empty sticky strip would just draw a border over the page).
      actions.replaceChildren(downloadButton);
      downloadButton.disabled = true;
      return [
        el('strong', '依赖数据不可用'),
        el('span', '本次页面没有加载 deps.js，请通过管理器打开页面。', 'notice')
      ];
    }
    const {coverage} = deps;
    const clash = guidClashes();
    // A tick on a card that has since been fixed points at nothing; drop it so
    // the count in the button always matches what will really be downloaded.
    for (const path of [...state.picked]) {
      const entry = deps.cards[path];
      if (!entry || !(entry.missing || []).length) state.picked.delete(path);
    }
    const pickable = list.filter(resource => {
      const verdict = KK.deps.card(resource.path);
      return verdict && verdict.state === 'missing';
    }).length;
    if (!fetching) {
      const count = missingGuids().size;
      downloadButton.textContent = !count ? '下载缺失 Mod'
        : state.picked.size
          ? `下载选中 ${state.picked.size} 张卡的 ${count} 个 Mod`
          : `下载缺失 Mod（${count}）`;
      downloadButton.disabled = !count;
    }
    const total = missingGuids().size;
    pickNote.textContent = state.picked.size
      ? `已勾选 ${state.picked.size} 张卡 · 共 ${total} 个缺失 Mod`
      : `未勾选时下载全部缺失 Mod；当前结果里 ${pickable.toLocaleString()} 张卡缺失`;
    pickAllButton.textContent = `全选当前结果（${pickable.toLocaleString()}）`;
    pickAllButton.disabled = !pickable;
    clearButton.disabled = !state.picked.size;
    actions.replaceChildren(pickNote, pickAllButton, clearButton, downloadButton);
    const lines = [
      el('strong', `角色卡 ${coverage.total.toLocaleString()}`),
      el('span', `依赖已识别 ${coverage.parsed.toLocaleString()} · ` +
        `未识别 ${coverage.unknown.toLocaleString()} · ` +
        `缺失 ${coverage.missing.toLocaleString()} · ` +
        `齐全 ${coverage.complete.toLocaleString()}`, 'notice')
    ];
    if (clash.groups) {
      // A count on its own is not actionable: it opens the groups so the user
      // can see which packages are fighting over the GUID. It reads as a button
      // rather than a footnote -- an underlined sentence was easy to miss.
      const link = el('button', undefined, 'clash-count');
      link.type = 'button';
      link.title = '查看哪些包共用一个 GUID';
      link.append(el('span', `GUID 冲突 ${clash.groups} 组（${clash.packs} 个包）`),
        el('span', '查看 ›', 'clash-go'));
      link.onclick = showClashes;
      lines.push(link);
    }
    return lines;
  }

  /* Only a card that is actually missing something is worth ticking: the tick
   * means "download for this card", so complete and unknown cards have nothing
   * to contribute and just add noise to the selection. */
  function pickBox(resource, card) {
    const box = el('label', undefined, 'pick');
    box.title = `勾选后只下载「${resource.file}」缺的 Mod`;
    const input = el('input');
    input.type = 'checkbox';
    input.checked = state.picked.has(resource.path);
    input.setAttribute('aria-label', `下载 ${resource.file} 缺失的 Mod`);
    input.onchange = () => {
      if (input.checked) state.picked.add(resource.path);
      else state.picked.delete(resource.path);
      if (input.checked) card.classList.add('selected');
      else card.classList.remove('selected');
      if (KK.deps.raw) summary.replaceChildren(...numbers());
    };
    box.append(input);
    if (input.checked) card.classList.add('selected');
    return box;
  }

  function cardRow(resource) {
    const verdict = KK.deps.card(resource.path);
    const card = el('article', undefined, 'library-card');
    if (resource.thumb) card.append(KK.media.thumb(resource.thumb, resource.file));
    else card.append(el('div', '暂无缩略图', 'empty'));
    if (verdict && verdict.state === 'missing') card.append(pickBox(resource, card));

    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', resource.file, 'name'), el('div', resource.path, 'path'),
      el('div', `${resource.kind} · ${(resource.bytes / 1048576).toFixed(1)} MB`, 'count'));

    const label = verdict && verdict.state === 'missing'
      ? [`缺失 ${verdict.missing.length} 个所需 Mod GUID`, 'detection-label missing']
      : verdict && verdict.state === 'complete'
        ? [`依赖齐全 · 已安装 ${resource.mods.length} 个包`, 'detection-label installed']
        : ['依赖未识别，无法判断', 'detection-label'];
    meta.append(el('div', label[0], label[1]));

    if (verdict && verdict.missing.length) {
      const box = el('details', undefined, 'library-details');
      box.append(el('summary', `缺失 GUID（${verdict.missing.length}）`));
      const items = el('ul');
      verdict.missing.forEach(guid => items.append(el('li', guid)));
      box.append(items);
      box.open = true;
      meta.append(box);
    }
    if (resource.mods.length) {
      meta.append(el('div', `被 ${resource.mods.length} 个已安装包提供`, 'count'));
    }

    card.append(meta);
    return card;
  }

  function render() {
    if (!KK.deps.raw) {
      summary.replaceChildren(...numbers());
      grid.replaceChildren();
      setStatus('依赖数据不可用');
      KK.refreshPager();
      return;
    }
    const text = query.value.trim().toLowerCase();

    list = data.library.filter(resource => {
      if (state.scope && resource.kind !== state.scope) return false;
      const verdict = KK.deps.card(resource.path);
      const verdictState = verdict ? verdict.state : 'unknown';
      if (state.filter && verdictState !== state.filter) return false;
      if (!text) return true;
      return [resource.path, ...resource.mods, ...(resource.references || [])
        .map(ref => ref.guid)].join('\n').toLowerCase().includes(text);
    });

    list.sort((a, b) => a.path.localeCompare(b.path));
    const pages = Math.max(1, Math.ceil(list.length / SIZE));
    state.page = Math.max(0, Math.min(state.page, pages - 1));
    summary.replaceChildren(...numbers());
    grid.replaceChildren();
    for (const entry of list.slice(state.page * SIZE, (state.page + 1) * SIZE)) {
      grid.append(cardRow(entry));
    }
    setStatus(`${list.length.toLocaleString()} 张卡 · 资源卡缺失检查`);
    KK.refreshPager();
  }

  function reset(rerender) {
    state.page = 0;
    if (rerender !== false) render();
  }

  scope.onchange = () => { state.scope = scope.value; reset(); };
  filter.onchange = () => { state.filter = filter.value; reset(); };
  query.oninput = () => reset();

  syncFilters();
  KK.registerView({
    id: 'deps',
    label: '依赖检查',
    order: 30,
    controls,
    dock: actions,
    element,
    render() {
      render();
      ensureCards();
    },
    pageInfo() {
      return {current: state.page, pages: Math.max(1, Math.ceil(list.length / SIZE))};
    },
    go(delta) {
      state.page = Math.max(0, state.page + delta);
      render();
    }
  });

  /* The Mod grid answers a question the dependency view no longer asks: not
   * "which card is missing what" but "does anything reference this package at
   * all". That is housekeeping, and the grid is where a package is actually
   * disabled, so the question lives there and only there. A package is safe to
   * drop only when no character card, no outfit card and no scene names it, so
   * the facet counts all three. */
  const KINDS = ['角色卡', '服装卡', '场景'];

  function references(verdict) {
    const cards = (verdict.cards || []).length;
    const outfits = (verdict.outfits || []).length;
    const scenes = (verdict.scenes || []).length;
    return {cards, outfits, scenes, total: cards + outfits + scenes};
  }

  function libraryHasCards() {
    const raw = KK.deps.raw;
    if (!raw) return false;
    if (raw.coverageByKind) {
      return KINDS.some(kind => {
        const value = raw.coverageByKind[kind];
        return Boolean(value && value.parsed);
      });
    }
    return Boolean(raw.coverage && raw.coverage.parsed);
  }

  function cardState(record) {
    const verdict = KK.deps.mod(record.path);
    if (!verdict) return 'unknown';
    if (references(verdict).total) return 'matched';
    // No GUID, or nothing parsed in the library, means no reference is merely
    // an absence of evidence rather than evidence of absence.
    return record.guid && libraryHasCards() ? 'unmatched' : 'unknown';
  }

  /* "Cannot tell" is a real verdict -- a package without a GUID, or one the
   * index has not covered yet -- but on a healthy catalog nothing lands in it,
   * and an option that can only ever return nothing is noise in the dropdown.
   * It therefore appears only while something is actually in that state. */
  function unknownReferences() {
    let total = 0;
    for (const record of data.catalog) {
      if (cardState(record) === 'unknown') total += 1;
    }
    return total;
  }

  KK.registerModFacet({
    id: 'usage',
    label: '卡片引用',
    options() {
      const choices = [['', '全部引用状态'], ['unmatched', '未被任何卡片引用'],
        ['matched', '已被卡片引用']];
      if (unknownReferences()) choices.push(['unknown', '无法判断']);
      return choices;
    },
    match(record, value) { return cardState(record) === value; }
  });

  /* "被 4 张服装卡引用" is an answer with a follow-up question attached: which
   * four? Clicking the line opens them with their thumbnails, so a package can
   * be judged from the grid instead of switching views and searching for the
   * card by hand. The dependency data already names the paths; the server turns
   * a path list into the handful of fields this list draws. */
  const REF_KINDS = {'角色卡': 'cards', '服装卡': 'outfits', '场景卡': 'scenes'};

  const refDialog = el('dialog');
  refDialog.className = 'card-refs';
  refDialog.setAttribute('aria-label', '引用这个 Mod 的卡片');
  const refHead = el('header');
  const refTitle = el('h2');
  const refClose = el('button', '×');
  refClose.type = 'button'; refClose.title = '关闭';
  refClose.setAttribute('aria-label', '关闭');
  refHead.append(refTitle, refClose);
  const refBody = el('div', undefined, 'card-refs-body');
  refDialog.append(refHead, refBody);
  document.body.append(refDialog);

  function closeReferences() {
    if (typeof refDialog.close === 'function') refDialog.close();
    else refDialog.open = false;
  }

  /* One dialog, two payloads: which cards wear this package, and which packages
     share a GUID. Both answer a single follow-up question and get closed again,
     so they share the frame instead of duplicating it. */
  function openDialog(title, label) {
    refTitle.textContent = title;
    refDialog.setAttribute('aria-label', label);
    refBody.replaceChildren();
    if (typeof refDialog.showModal === 'function') refDialog.showModal();
    else refDialog.open = true;
  }
  refClose.onclick = closeReferences;
  refDialog.addEventListener('click', event => {
    if (event.target !== refDialog) return;
    const box = refDialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right ||
        event.clientY < box.top || event.clientY > box.bottom) closeReferences();
  });

  function refRow(row) {
    const card = el('article', undefined, 'library-card');
    if (row.thumb) card.append(KK.media.thumb(row.thumb, row.file));
    else card.append(el('div', '暂无缩略图', 'empty'));
    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', row.file || row.path, 'name'),
      el('div', row.path, 'path'));
    card.append(meta);
    return card;
  }

  function paintReferences(answer) {
    const rows = answer.cards || [];
    refBody.replaceChildren();
    if (!rows.length) {
      refBody.append(el('p', '这些卡片已经不在资源卡目录里了。', 'notice'));
      return;
    }
    const list = el('div', undefined, 'card-refs-list');
    for (const row of rows) list.append(refRow(row));
    refBody.append(list);
    const total = answer.total || rows.length;
    if (rows.length < total) {
      refBody.append(el('p', `另有 ${(total - rows.length).toLocaleString()} 张卡片不在资源卡目录里。`,
        'notice'));
    }
  }

  let refRequest = 0;

  function showReferences(record, kind, count) {
    const ticket = ++refRequest;
    openDialog(`被 ${count} 张${kind}引用 · ${record.name || record.path}`,
      '引用这个 Mod 的卡片');
    refBody.append(el('p', '正在读取卡片…', 'notice'));
    const endpoint = `/api/library/cards?mod=${encodeURIComponent(record.path)}`
      + `&kind=${REF_KINDS[kind]}`;
    /* Two counts pressed in a row must not let the first answer paint over the
     * second list; only the latest request is allowed to fill the dialog. */
    return KK.api.read(endpoint)
      .then(answer => { if (ticket === refRequest) paintReferences(answer); })
      .catch(error => {
        if (ticket !== refRequest) return;
        refBody.replaceChildren(el('p', '卡片读取失败：' + error.message, 'notice'));
      });
  }

  /* The summary counts colliding groups; this is the answer to the obvious
     follow-up. Biggest pile first, then by path inside a group, so the same
     clash is in the same place every time the dialog opens. */
  function clashGroups() {
    const groups = [];
    for (const [guid, rows] of byGuid()) {
      if (rows.length < 2) continue;
      groups.push([guid, rows.slice().sort((a, b) => a.path.localeCompare(b.path))]);
    }
    return groups.sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  }

  /* Same card shape as the Mod grid, scaled down: a cover is what tells two
     similarly named packs apart, and a collision is exactly the case where the
     names are similar. */
  function guidPack(record) {
    const card = el('article', undefined, 'library-card');
    const thumb = (record.images || [])[0];
    if (thumb) card.append(KK.media.thumb(thumb.src, record.name || record.file));
    else card.append(el('div', '暂无预览', 'empty'));
    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', record.name || record.file, 'name'),
      el('div', record.path, 'path'),
      el('div', `${(record.bytes / 1048576).toFixed(1)} MB`, 'count'));
    card.append(meta);
    return card;
  }

  function showClashes() {
    const groups = clashGroups();
    const packs = groups.reduce((sum, [, rows]) => sum + rows.length, 0);
    openDialog(`GUID 冲突 · ${groups.length} 组共 ${packs} 个包`, '共用同一个 GUID 的包');
    if (!groups.length) {
      refBody.append(el('p', '现在没有冲突。', 'notice'));
      return;
    }
    refBody.append(el('p', '同一组的包共用一个 GUID，游戏只会加载其中一个。'
      + '留下哪个由包内的版本号决定，版本相同时按路径长度；停用多余的那个就能消除冲突。',
      'notice'));
    const box = el('div', undefined, 'guid-groups');
    for (const [guid, rows] of groups) {
      const group = el('section', undefined, 'guid-group');
      group.append(el('h3', guid, 'guid-key'));
      for (const row of rows) group.append(guidPack(row));
      box.append(group);
    }
    refBody.append(box);
  }

  KK.onModCard((record, card, meta) => {
    const verdict = KK.deps.mod(record.path);
    /* A GUID collision is a fact about the package, not about any filter, and
     * it goes above the package name so it cannot be read as a footnote to it.
     * This used to hide behind the "card reference" filter, which is why four
     * colliding packages were invisible on the default view. */
    if (sharesGuid(record).length || (verdict && verdict.duplicateGuid)) {
      meta.prepend(conflictBanner(record));
    }
    if (!verdict) return;
    const state = cardState(record);
    /* Three answers instead of one total: a package worn by one character and
     * a prop placed in one scene are both "referenced", yet only the first is
     * obviously worth keeping. Each kind gets its own line, so the card can be
     * read without opening the dependency view -- and it is shown on the
     * default view, not only while the facet is asking about references. */
    const refs = references(verdict);
    const counts = [['角色卡', refs.cards], ['服装卡', refs.outfits],
      ['场景卡', refs.scenes]].filter(([, count]) => count);
    if (counts.length) {
      const box = el('div', undefined, 'reference-counts installed');
      for (const [kind, count] of counts) {
        const link = el('button', `被 ${count} 张${kind}引用`, 'ref-count');
        link.type = 'button';
        link.title = `查看这 ${count} 张${kind}`;
        link.onclick = () => showReferences(record, kind, count);
        box.append(link);
      }
      meta.append(box);
    } else if (state === 'unmatched') {
      meta.append(el('div', '未被任何卡片引用', 'detection-label duplicate'));
    } else if (state === 'unknown' && KK.facetValue('usage') === 'unknown') {
      meta.append(el('div', record.guid ? '资源卡索引不足，无法判断' : '包内未声明 GUID，无法判断',
        'detection-label'));
    }
  });
})();
