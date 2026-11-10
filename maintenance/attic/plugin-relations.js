/* Plugin: two-way view of cards and the mods they reference.
 *
 * Registered as a view; it appends card buttons through the hook slots instead
 * of wrapping another module's setView.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {data, el, setStatus} = KK;
  const PAGE = 40;

  const controls = el('div');
  controls.id = 'relationControls';

  const mode = el('select');
  mode.setAttribute('aria-label', '关联方向');
  [['cards', '按资源卡查看 Mod'], ['mods', '按 Mod 查看引用资源']].forEach(([value, label]) => {
    const option = el('option', label);
    option.value = value;
    mode.append(option);
  });

  const kind = el('select');
  kind.setAttribute('aria-label', '关联资源类型');
  ['角色卡', '场景', '服装卡'].forEach(label => {
    const option = el('option', label);
    option.value = label;
    kind.append(option);
  });

  const query = el('input');
  query.type = 'search';
  query.placeholder = '角色卡 / 场景 / 服装卡 / Mod 名称 / GUID';
  query.setAttribute('aria-label', '搜索资源卡与 Mod');

  const state = el('select');
  state.setAttribute('aria-label', '关联状态');
  [['', '全部关联状态'], ['missing', '有缺失 Mod'], ['installed', '有已安装 Mod'],
    ['unknown', '依赖未识别']].forEach(([value, label]) => {
    const option = el('option', label);
    option.value = value;
    state.append(option);
  });

  controls.append(mode, kind, query, state);

  const element = el('section');
  element.id = 'relationsPanel';

  const sidebar = el('aside', undefined, 'relation-list');
  sidebar.setAttribute('aria-label', '资源卡与 Mod 列表');
  const list = el('div');
  const pager = el('div', undefined, 'relation-pager');
  const previous = el('button', '←');
  const next = el('button', '→');
  const pageLabel = el('span');
  previous.setAttribute('aria-label', '关联列表上一页');
  next.setAttribute('aria-label', '关联列表下一页');
  pager.append(previous, pageLabel, next);
  sidebar.append(list, pager);

  const detail = el('section', undefined, 'relation-detail');
  detail.setAttribute('aria-label', '资源卡与 Mod 对应明细');
  element.append(sidebar, detail);

  let pageNumber = 0, selectedPath = '', selectedGuid = '';
  let pendingPath = '', pendingKey = '';
  let cards = [], byGuid = new Map(), references = new Map(), candidates = [];
  let reverseIndex = null, reverseVersion = -1;

  // mod path -> how many cards of each kind reference it. Built once per
  // library version; scanning the whole library per card would be quadratic.
  function reverse() {
    if (reverseIndex && reverseVersion === data.version) return reverseIndex;
    reverseIndex = new Map();
    reverseVersion = data.version;
    for (const resource of data.library) {
      for (const path of resource.mods || []) {
        let entry = reverseIndex.get(path);
        if (!entry) {
          entry = {'角色卡': 0, '场景': 0, '服装卡': 0};
          reverseIndex.set(path, entry);
        }
        if (entry[resource.kind] !== undefined) entry[resource.kind]++;
      }
    }
    return reverseIndex;
  }

  function rebuild() {
    byGuid = new Map();
    references = new Map();
    for (const mod of data.catalog) {
      if (!mod.guid) continue;
      const key = mod.guid.toLowerCase();
      if (!byGuid.has(key)) byGuid.set(key, []);
      byGuid.get(key).push(mod);
    }
    cards = data.library.filter(resource => resource.kind === kind.value)
      .sort((a, b) => a.path.localeCompare(b.path));
    for (const card of cards) {
      const guids = new Set((card.references || []).map(ref => ref.guid));
      for (const guid of guids) {
        const key = guid.toLowerCase();
        if (!references.has(key)) references.set(key, {key, guid, cards: []});
        references.get(key).cards.push(card);
      }
    }
  }

  function packages(guid) { return byGuid.get(guid.toLowerCase()) || []; }

  function jumpToPackage(path) { KK.openView('mods', {query: path}); }

  function heading(title, subtitle, image) {
    const head = el('div', undefined, 'relation-heading');
    if (image) {
      const node = el('img');
      node.src = image;
      node.alt = title;
      head.append(node);
    }
    const text = el('div');
    text.append(el('h2', title), el('div', subtitle, 'path'));
    head.append(text);
    detail.append(head);
    return text;
  }

  function cardState(card) {
    if (!card.references || !card.references.length) return '依赖未识别';
    return `已安装 ${card.mods.length} 个包 · 缺失 ${card.missingTokens.length} 个 GUID`;
  }

  function resourceButton(card) {
    const button = el('button', undefined, 'relation-resource');
    button.type = 'button';
    button.setAttribute('aria-label', '查看对应 Mod：' + card.path);
    if (card.thumb) {
      const image = el('img');
      image.src = card.thumb;
      image.alt = '';
      image.loading = 'lazy';
      button.append(image);
    }
    const text = el('span');
    text.append(el('strong', card.file), el('span', card.path, 'path'),
      el('span', cardState(card), card.missing ? 'missing' : 'count'));
    button.append(text);
    button.onclick = () => openCard(card.path);
    return button;
  }

  function usage(ref, mods) {
    if (ref.slot == null) return ref.property;
    const names = mods.flatMap(mod => mod.items || []).filter(item =>
      String(item.ID) === String(ref.slot) && ref.category !== null &&
      ref.category !== undefined && String(item.ListCategory) === String(ref.category));
    const name = [...new Set(names.map(item => item.Name))].join(' / ');
    return `${name ? name + ' · ' : ''}${ref.property} · ID ${ref.slot}`;
  }

  function showCard(card) {
    const text = heading(card.file, card.path, card.thumb);
    text.append(el('div', cardState(card), card.missing ? 'missing' : 'count'));
    const open = el('button', '查看资源卡');
    open.type = 'button';
    open.onclick = () => KK.openView('library', {path: card.path, kind: card.kind});
    text.append(open);
    if (!card.references || !card.references.length) {
      detail.append(el('p', '尚未识别此文件的 Sideloader 依赖记录，无法列出对应 Mod。', 'notice'));
      return;
    }
    const grouped = new Map();
    card.references.forEach(ref => {
      const key = ref.guid.toLowerCase();
      if (!grouped.has(key)) grouped.set(key, {guid: ref.guid, refs: []});
      grouped.get(key).refs.push(ref);
    });
    const wrap = el('div', undefined, 'relation-table-wrap');
    const table = el('table');
    const head = el('thead'), header = el('tr');
    ['Mod 名称 / GUID', '安装状态', '对应压缩包', '使用条目'].forEach(title => {
      header.append(el('th', title));
    });
    head.append(header);
    table.append(head);
    const body = el('tbody');
    [...grouped.values()].sort((a, b) =>
      Number(Boolean(packages(a.guid).length)) - Number(Boolean(packages(b.guid).length)) ||
      a.guid.localeCompare(b.guid)).forEach(group => {
      const mods = packages(group.guid), row = el('tr');
      const nameCell = el('td');
      nameCell.append(el('strong', mods.map(mod => mod.name || mod.file).join(' / ') || group.guid),
        el('div', group.guid, 'path'));
      const related = el('button', '查看引用卡片');
      related.type = 'button';
      related.onclick = () => openMod(group.guid, kind.value);
      nameCell.append(related);
      const baseCount = group.refs.filter(ref => ref.providedByBase).length;
      const baseOnly = !mods.length && baseCount === group.refs.length;
      const status = mods.length ? '已安装' : baseOnly ? '游戏本体已提供'
        : baseCount ? '部分缺失' : '缺失';
      row.append(nameCell, el('td', status, mods.length || baseOnly ? 'installed' : 'missing'));
      const packageCell = el('td');
      if (!mods.length) {
        packageCell.append(el('span', baseOnly
          ? '由本体物品清单提供，无需对应 zipmod' : '未找到相同 GUID 的已安装包',
        baseOnly ? 'count' : 'missing'));
      }
      mods.forEach(mod => {
        const button = el('button', mod.path);
        button.type = 'button';
        button.onclick = () => jumpToPackage(mod.path);
        packageCell.append(button);
      });
      const itemCell = el('td'), entries = el('ul');
      [...new Set(group.refs.map(ref => usage(ref, mods) +
        (ref.providedByBase ? ' · 游戏本体已提供' : '')))]
        .forEach(item => entries.append(el('li', item)));
      itemCell.append(entries);
      row.append(packageCell, itemCell);
      body.append(row);
    });
    table.append(body);
    wrap.append(table);
    detail.append(wrap);
    detail.append(el('p',
      '对应关系来自已识别的 Sideloader 记录；旧格式及其他插件依赖尚未全部覆盖。', 'notice'));
  }

  function baseProvided(entry) {
    const refs = entry.cards.flatMap(card => (card.references || [])
      .filter(ref => ref.guid.toLowerCase() === entry.guid.toLowerCase()));
    return refs.length > 0 && refs.every(ref => ref.providedByBase);
  }

  function showMod(entry) {
    const mods = packages(entry.guid);
    const supplied = baseProvided(entry);
    const text = heading(mods.map(mod => mod.name || mod.file).join(' / ') || entry.guid,
      entry.noGuid ? '包内未声明 GUID' : entry.guid);
    text.append(el('div', `${mods.length ? '已安装' : supplied ? '游戏本体已提供' : '缺失'} · ` +
      `${entry.cards.length} 个${kind.value}引用`, mods.length || supplied ? 'installed' : 'missing'));
    mods.forEach(mod => {
      const button = el('button', mod.path);
      button.type = 'button';
      button.onclick = () => jumpToPackage(mod.path);
      detail.append(button);
    });
    if (!entry.cards.length) {
      detail.append(el('p', '没有在已识别的依赖记录中找到引用。', 'notice'));
    }
    const grid = el('div', undefined, 'relation-resources');
    entry.cards.forEach(card => grid.append(resourceButton(card)));
    detail.append(grid);
  }

  function renderRelations() {
    const text = query.value.trim().toLowerCase(), cardsMode = mode.value === 'cards';
    if (cardsMode) {
      candidates = cards.filter(card => (!text || [card.path, ...card.mods,
        ...(card.references || []).flatMap(ref => [ref.guid,
          ...packages(ref.guid).map(mod => mod.name)])].join('\n').toLowerCase().includes(text)) &&
        (!state.value || (state.value === 'missing' && card.missing) ||
          (state.value === 'installed' && (card.mods.length ||
            (card.baseGameTokens && card.baseGameTokens.length))) ||
          (state.value === 'unknown' && !(card.references || []).length)));
    } else {
      candidates = [...references.values()].filter(entry =>
        (!text || [entry.guid, ...packages(entry.guid).map(mod => mod.path + '\n' + mod.name),
          ...entry.cards.map(card => card.path)].join('\n').toLowerCase().includes(text)) &&
        (!state.value || (state.value === 'missing' && !packages(entry.guid).length &&
          !baseProvided(entry)) ||
          (state.value === 'installed' && (packages(entry.guid).length || baseProvided(entry)))));
      candidates.sort((a, b) => b.cards.length - a.cards.length || a.guid.localeCompare(b.guid));
    }
    const pages = Math.max(1, Math.ceil(candidates.length / PAGE));
    if (pendingPath || pendingKey) {
      const index = pendingPath
        ? candidates.findIndex(entry => entry.path === pendingPath)
        : candidates.findIndex(entry => entry.key === pendingKey);
      if (index >= 0) pageNumber = Math.floor(index / PAGE);
      pendingPath = pendingKey = '';
    }
    pageNumber = Math.max(0, Math.min(pageNumber, pages - 1));
    const key = cardsMode ? selectedPath : selectedGuid;
    let current = candidates.find(entry => (cardsMode ? entry.path : entry.key) === key);
    if (!current) current = candidates[pageNumber * PAGE];
    if (current) {
      if (cardsMode) selectedPath = current.path;
      else selectedGuid = current.key;
    }
    list.replaceChildren();
    detail.replaceChildren();
    candidates.slice(pageNumber * PAGE, (pageNumber + 1) * PAGE).forEach(entry => {
      let button;
      if (cardsMode) button = resourceButton(entry);
      else {
        const mods = packages(entry.guid);
        button = el('button', undefined, 'relation-resource');
        button.type = 'button';
        const label = el('span');
        label.append(el('strong', mods.map(mod => mod.name || mod.file).join(' / ') || entry.guid),
          el('span', entry.guid, 'path'),
          el('span', `${mods.length ? '已安装' : baseProvided(entry) ? '游戏本体已提供' : '缺失'} · ` +
            `${entry.cards.length} 个${kind.value}`,
          mods.length || baseProvided(entry) ? 'count' : 'missing'));
        button.append(label);
        button.onclick = () => { selectedGuid = entry.key; renderRelations(); };
      }
      const selected = current === entry;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
      if (cardsMode) button.onclick = () => { selectedPath = entry.path; renderRelations(); };
      list.append(button);
    });
    previous.disabled = pageNumber === 0;
    next.disabled = pageNumber === pages - 1;
    pageLabel.textContent = `${pageNumber + 1} / ${pages}`;
    setStatus(`${cards.length} 个${kind.value} · ${candidates.length} 个匹配` +
      `${cardsMode ? '资源' : 'Mod GUID'}`);
    if (current) {
      if (cardsMode) showCard(current);
      else showMod(current);
    } else detail.append(el('p', '没有匹配的对应关系', 'notice'));
  }

  function openCard(path) {
    const card = data.library.find(resource => resource.path === path);
    if (!card) return;
    KK.openView('relations', {card: path});
  }

  function openMod(pathOrGuid, resourceKind = '角色卡') {
    const mod = data.catalog.find(record => record.path === pathOrGuid);
    const guid = (mod && mod.guid) || pathOrGuid;
    KK.openView('relations', {mod: guid, kind: resourceKind, noGuid: Boolean(mod && !mod.guid)});
  }

  window.KK_RELATIONS = {openCard, openMod};

  [mode, kind, state, query].forEach(input => {
    input.oninput = input.onchange = () => {
      pageNumber = 0;
      selectedPath = '';
      selectedGuid = '';
      renderRelations();
    };
  });
  previous.onclick = () => { pageNumber--; selectedPath = selectedGuid = ''; renderRelations(); };
  next.onclick = () => { pageNumber++; selectedPath = selectedGuid = ''; renderRelations(); };

  KK.registerView({
    id: 'relations',
    label: '资源卡 ↔ Mod',
    order: 25,
    controls,
    element,
    render(params) {
      if (params && params.card) {
        const card = data.library.find(resource => resource.path === params.card);
        if (card) {
          kind.value = card.kind;
          mode.value = 'cards';
          selectedPath = pendingPath = card.path;
        }
      }
      if (params && params.mod) {
        mode.value = 'mods';
        if (params.kind) kind.value = params.kind;
        selectedGuid = pendingKey = String(params.mod).toLowerCase();
      }
      if (params) {
        query.value = '';
        state.value = '';
        pageNumber = 0;
      }
      rebuild();
      if (params && params.mod && !references.has(selectedGuid)) {
        references.set(selectedGuid, {
          key: selectedGuid, guid: params.mod, cards: [], noGuid: Boolean(params.noGuid)
        });
      }
      renderRelations();
    },
    pageInfo() { return null; }
  });

  /* Card decorations, registered as slots instead of piggy-backing on render. */
  KK.onModCard((record, card, meta) => {
    const counts = reverse().get(record.path);
    for (const label of ['角色卡', '场景', '服装卡']) {
      const count = (counts && counts[label]) || 0;
      const button = el('button', `查看关联${label}（${count}）`, 'relation-action');
      button.type = 'button';
      button.onclick = () => openMod(record.path, label);
      meta.append(button);
    }
  });

  KK.onLibraryCard((resource, card, meta) => {
    if (!['角色卡', '场景', '服装卡'].includes(resource.kind)) return;
    const button = el('button', '查看对应 Mod', 'relation-action');
    button.type = 'button';
    button.onclick = () => openCard(resource.path);
    meta.append(button);
  });
})();
