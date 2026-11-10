/* Plugin: missing-mod report.
 *
 * Own view, own filters, own pager. It reads KK.deps (computed once on the
 * server) and never touches the Mod view's filter state, so entering and
 * leaving it cannot disturb where the user was.
 *
 * This is also the only place that needs the card records themselves, so they
 * are fetched on first use instead of being shipped with every page load.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {data, el, setStatus} = KK;
  const SIZE = 40;

  const state = {mode: 'cards', scope: '', filter: 'missing', text: '', page: 0,
    picked: new Set()};
  let list = [];
  let cardsLoaded = false;
  let fetching = false;

  const controls = el('div');
  controls.id = 'dependencyControls';

  const mode = el('select');
  mode.setAttribute('aria-label', '检查对象');
  [['cards', '资源卡缺什么'], ['mods', '哪些 Mod 没被引用']].forEach(([value, label]) => {
    const option = el('option', label);
    option.value = value;
    mode.append(option);
  });

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

  controls.append(mode, scope, filter, query);

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

  /* "Installed 0" on its own reads like a failed download, when the usual cause
   * is that the public pack never shipped those packages. Spell out both sides
   * of the count, then list the ones still missing so they can be searched for
   * by hand. A GUID can also go unanswered because the only build is a Sunshine
   * one or because its archive's manifest could not be read, so those are
   * counted apart -- the coverage line is what makes "not in the pack" a claim
   * the reader can check instead of a guess. */
  function paintReport(job) {
    const installed = (job.updates || []).length;
    const requested = job.requested || job.total || 0;
    const rejected = job.unmatched || [];
    /* The pack ships the same mod for both games, so an unmatched GUID is
     * either "the pack lacks it" or "only the other game's build exists" --
     * and which one is "the other game" flips with the install. */
    const other = job.flavour === 'kks' ? 'kk' : 'kks';
    const otherLabel = other === 'kk' ? 'KK' : 'KKS';
    const wrongGame = rejected.filter(row => row.reason === 'kks'
      || row.reason === 'kk' || row.reason === 'game');
    const absent = rejected.filter(row => !wrongGame.includes(row));
    const parts = [`请求 ${requested.toLocaleString()} 个 GUID`];
    if (job.matched !== undefined && job.matched !== null) {
      parts.push(`公共索引里找到 ${job.matched.toLocaleString()} 个`);
    }
    parts.push(`安装 ${installed.toLocaleString()} 个`);
    if (job.flavour) parts.push(`按 ${job.flavour === 'kks' ? 'KKS' : 'KK'} 索引`);
    if (job.failed) parts.push(`${job.failed.toLocaleString()} 个下载失败`);
    if (absent.length) parts.push(`${absent.length.toLocaleString()} 个公共索引里没有`);
    if (wrongGame.length) {
      parts.push(`${wrongGame.length.toLocaleString()} 个只有 ${otherLabel} 版`);
    }

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
        if (row.reason === 'kks' || row.reason === 'kk' || row.reason === 'game') {
          item.append(el('span', ` · 索引里只有 ${otherLabel} 版`, 'notice'));
        }
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

  /* The same rule as the Mod grid facet: a state nothing is in is not worth a
   * filter, so "cannot tell" is offered only while the snapshot holds one. */
  function unknownMods() {
    const raw = KK.deps.raw;
    if (!raw || !raw.mods) return 0;
    let total = 0;
    for (const path of Object.keys(raw.mods)) {
      if (raw.mods[path].state === 'unknown') total += 1;
    }
    return total;
  }

  const FILTERS = {
    cards: [['missing', '缺失 Mod'], ['complete', '依赖齐全'], ['unknown', '依赖未识别'],
      ['', '全部资源卡']],
    mods() {
      const options = [['unmatched', '未匹配角色卡'], ['matched', '已匹配角色卡']];
      if (unknownMods()) options.push(['unknown', '无法判断']);
      options.push(['', '全部角色 Mod']);
      return options;
    }
  };

  function syncFilters() {
    const registered = FILTERS[state.mode];
    const options = typeof registered === 'function' ? registered() : registered;
    filter.replaceChildren(...options.map(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      return option;
    }));
    if (!options.some(([value]) => value === state.filter)) state.filter = options[0][0];
    filter.value = state.filter;
    scope.hidden = state.mode !== 'cards';
  }

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
    const {summary: mods, coverage} = deps;
    const cardsMode = state.mode === 'cards';
    // A tick on a card that has since been fixed points at nothing; drop it so
    // the count in the button always matches what will really be downloaded.
    for (const path of [...state.picked]) {
      const entry = deps.cards[path];
      if (!entry || !(entry.missing || []).length) state.picked.delete(path);
    }
    const pickable = cardsMode ? list.filter(resource => {
      const verdict = KK.deps.card(resource.path);
      return verdict && verdict.state === 'missing';
    }).length : 0;
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
    actions.replaceChildren(...(cardsMode ? [pickNote, pickAllButton, clearButton] : []),
      downloadButton);
    return [
      el('strong', `角色相关 Mod ${mods.characterMods.toLocaleString()}`),
      el('span', `已匹配 ${mods.matched.toLocaleString()} · ` +
        `未匹配 ${mods.unmatched.toLocaleString()} · ` +
        `无法判断 ${mods.unknown.toLocaleString()}`),
      el('span', `角色卡 ${coverage.total.toLocaleString()} · ` +
        `依赖已识别 ${coverage.parsed.toLocaleString()} · ` +
        `未识别 ${coverage.unknown.toLocaleString()} · ` +
        `缺失 ${coverage.missing.toLocaleString()} · ` +
        `齐全 ${coverage.complete.toLocaleString()}`, 'notice'),
      el('span', '缺失结果按包 GUID 与本体物品清单匹配；依赖未识别的卡片不计为缺失或齐全。',
        'notice')
    ];
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

  function modRow(record) {
    const verdict = KK.deps.mod(record.path);
    const card = el('article', undefined, 'library-card');
    const cover = (record.images || [])[0];
    if (cover) card.append(KK.media.thumb(cover.src, cover.name));
    else card.append(el('div', '暂无预览图', 'empty'));
    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', record.name || record.file, 'name'),
      el('div', record.path, 'path'),
      el('div', `${record.slots.join(' / ') || record.dataDirs.join(' / ') || '未分类'} · ` +
        `${(record.bytes / 1048576).toFixed(1)} MB`, 'count'));

    const stateLabel = {unmatched: '未被任何角色卡引用', matched: null,
      unknown: '无法判断引用状态', other: '非角色内容'}[verdict ? verdict.state : 'other'];
    if (stateLabel) {
      meta.append(el('div', stateLabel,
        verdict && verdict.state === 'unmatched' ? 'detection-label missing' : 'detection-label'));
    }
    if (verdict && (verdict.cards.length || verdict.outfits.length || verdict.scenes.length)) {
      meta.append(el('div', `角色卡 ${verdict.cards.length} · 服装卡 ${verdict.outfits.length} · ` +
        `场景 ${verdict.scenes.length}`, 'count'));
    }

    const open = el('button', '在 Mod 中查看', 'relation-action');
    open.type = 'button';
    open.onclick = () => KK.openView('mods', {query: record.path});
    meta.append(open);

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
    const cardsMode = state.mode === 'cards';

    list = cardsMode
      ? data.library.filter(resource => {
        if (state.scope && resource.kind !== state.scope) return false;
        const verdict = KK.deps.card(resource.path);
        const verdictState = verdict ? verdict.state : 'unknown';
        if (state.filter && verdictState !== state.filter) return false;
        if (!text) return true;
        return [resource.path, ...resource.mods, ...(resource.references || [])
          .map(ref => ref.guid)].join('\n').toLowerCase().includes(text);
      })
      : data.catalog.filter(record => {
        const verdict = KK.deps.mod(record.path);
        if (!verdict) return false;
        if (state.filter && verdict.state !== state.filter) return false;
        if (!text) return true;
        return [record.path, record.name, record.guid].join('\n')
          .toLowerCase().includes(text);
      });

    list.sort((a, b) => a.path.localeCompare(b.path));
    const pages = Math.max(1, Math.ceil(list.length / SIZE));
    state.page = Math.max(0, Math.min(state.page, pages - 1));
    summary.replaceChildren(...numbers());
    grid.replaceChildren();
    for (const entry of list.slice(state.page * SIZE, (state.page + 1) * SIZE)) {
      grid.append(cardsMode ? cardRow(entry) : modRow(entry));
    }
    setStatus(`${list.length.toLocaleString()} 条结果 · ` +
      (cardsMode ? '资源卡缺失检查' : '角色 Mod 引用检查'));
    KK.refreshPager();
  }

  function reset(rerender) {
    state.page = 0;
    if (rerender !== false) render();
  }

  mode.onchange = () => {
    state.mode = mode.value;
    syncFilters();
    reset();
  };
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
    render(params) {
      if (params && params.mode) { state.mode = params.mode; mode.value = params.mode; syncFilters(); }
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

  /* Extension points on the resident views.
   *
   * The Mod grid asks a different question from the dependency view above: not
   * "is this a character mod nobody wears" but "does anything reference this
   * package at all". A package is only safe to drop when no character card, no
   * outfit card and no scene names it, so the facet counts all three. */
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

  KK.onModCard((record, card, meta) => {
    if (!KK.facetValue('usage')) return;
    const verdict = KK.deps.mod(record.path);
    if (!verdict) return;
    const state = cardState(record);
    const refs = references(verdict);
    const label = {
      unmatched: '未被任何卡片引用',
      matched: `被 ${refs.total} 张卡片引用`,
      unknown: record.guid ? '资源卡索引不足，无法判断' : '包内未声明 GUID，无法判断'
    }[state];
    if (label) {
      meta.append(el('div', label, 'detection-label ' +
        (state === 'matched' ? 'installed' : 'duplicate')));
    }
    const breakdown = [['角色卡', refs.cards], ['服装卡', refs.outfits], ['场景', refs.scenes]]
      .filter(([, count]) => count).map(([kind, count]) => `${kind} ${count}`);
    if (breakdown.length) meta.append(el('div', breakdown.join(' · '), 'notice'));
    if (verdict.duplicateGuid) {
      meta.append(el('div', '相同 GUID 对应多个包，无法区分具体版本', 'notice'));
    }
  });
})();
