/* Card browser: role cards, scenes and coordinate cards with their own index.
 *
 * Missing-mod verdicts are read from KK.deps (server-side analysis) instead of
 * being recomputed here, so a card shows exactly one answer.
 */
(() => {
  'use strict';

  const KK = window.KK;
  const {data, el, api, setStatus, preserveViewport} = KK;
  const library = data.library;
  const SIZE = 48;

  let page = 0, busy = false;
  let selected = new Set();
  let undoMoves = [];
  try { undoMoves = JSON.parse(localStorage.getItem('kk-library-undo') || '[]'); } catch (_) {}

  const controls = el('div');
  controls.id = 'libraryControls';

  const query = el('input');
  query.id = 'libraryQ'; query.type = 'search';
  query.placeholder = '文件名 / 路径 / 标签 / Mod GUID';
  query.setAttribute('aria-label', '搜索资源');

  const kind = el('select');
  kind.id = 'libraryKind';
  kind.setAttribute('aria-label', '资源类型');
  ['', '角色卡', '场景', '服装卡'].forEach(value => {
    const option = el('option', value || '全部类型');
    option.value = value;
    kind.append(option);
  });

  const state = el('select');
  state.id = 'libraryState';
  state.setAttribute('aria-label', '资源状态');
  [['', '全部状态'], ['missing', '记录中缺失 Mod'], ['duplicate', '重复文件'],
    ['mods', '已关联 mod'], ['unknown', '依赖未识别']].forEach(([value, label]) => {
    const option = el('option', label);
    option.value = value;
    state.append(option);
  });

  const tagFilter = el('input');
  tagFilter.type = 'search';
  tagFilter.placeholder = '筛选标签';
  tagFilter.setAttribute('aria-label', '筛选标签');

  const destination = el('input');
  destination.id = 'libraryDestination';
  destination.placeholder = '目标目录，如 chara/female/收藏';
  destination.setAttribute('aria-label', 'UserData 下的目标目录');

  const move = el('button', '批量移动');
  move.id = 'libraryMove';
  move.type = 'button';
  move.disabled = true;

  const trash = el('button', '移入回收目录');
  trash.type = 'button';

  const undo = el('button', '撤销移动');
  undo.type = 'button';

  const pickPage = el('button', '选择本页');
  pickPage.type = 'button';

  const clear = el('button', '清空选择');
  clear.type = 'button';

  controls.append(query, kind, state, tagFilter, destination, move, trash, undo,
    pickPage, clear);

  const element = el('section');
  element.id = 'libraryPanel';
  element.className = 'library-grid';

  let lastList = [];

  function statusLine(extra) {
    const line = `${library.length.toLocaleString()} 个资源 · ` +
      `${lastList.length.toLocaleString()} 个匹配 · ${selected.size} 个选中`;
    setStatus(extra ? line + ' · ' + extra : line);
  }

  function updateButtons() {
    move.disabled = trash.disabled = !selected.size || busy;
    undo.disabled = busy || !undoMoves.length;
  }

  function remember() {
    localStorage.setItem('kk-library-undo', JSON.stringify(undoMoves));
    updateButtons();
  }

  function details(title, entries, parent, open) {
    const box = el('details', undefined, 'library-details');
    box.append(el('summary', title));
    function load() {
      if (box.dataset.loaded) return;
      box.dataset.loaded = '1';
      const list = el('ul');
      entries.forEach(entry => {
        const item = el('li');
        if (typeof entry === 'string') item.textContent = entry;
        else {
          const button = el('button', entry.text);
          button.type = 'button';
          button.onclick = entry.click;
          item.append(button);
        }
        list.append(item);
      });
      box.append(list);
    }
    box.addEventListener('toggle', () => { if (box.open) load(); });
    if (open) { box.open = true; load(); }
    parent.append(box);
  }

  function showMod(path) {
    KK.openView('mods', {query: path});
  }

  function dependencyLabel(resource) {
    const result = KK.deps.card(resource.path);
    if (!result) return null;
    if (result.state === 'missing') {
      return [`缺失 ${result.missing.length} 个所需 Mod GUID`, 'detection-label missing'];
    }
    if (result.state === 'complete') {
      return [`已识别依赖齐全 · 已安装 ${resource.mods.length} 个 Mod 包`,
        'detection-label installed'];
    }
    return null;
  }

  function buildCard(resource) {
    const card = el('article', undefined, 'library-card');
    if (resource.thumb) card.append(KK.media.thumb(resource.thumb, resource.file));
    else card.append(el('div', '暂无缩略图', 'empty'));

    const meta = el('div', undefined, 'libmeta');
    meta.append(el('div', resource.file, 'name'), el('div', resource.path, 'path'),
      el('div', `${resource.kind} · ${(resource.bytes / 1048576).toFixed(1)} MB`, 'count'));
    if (resource.error) meta.append(el('div', resource.error, 'error'));

    const verdict = dependencyLabel(resource);
    if (verdict) meta.append(el('div', verdict[0], verdict[1]));
    const dependencyState = KK.deps.card(resource.path);
    meta.append(el('div', !dependencyState || dependencyState.state === 'unknown'
      ? '未识别到依赖记录'
      : '已读取 Sideloader 记录；未覆盖的插件或旧格式需游戏内确认', 'notice'));

    if (dependencyState && dependencyState.missing.length) {
      details(`缺失 Mod GUID（${dependencyState.missing.length}）`, dependencyState.missing, meta,
        state.value === 'missing');
    }
    if (resource.mods.length) {
      details(`已安装 Mod（${resource.mods.length}）`,
        resource.mods.map(path => ({text: path, click: () => showMod(path)})), meta);
    }
    if (resource.baseGameTokens && resource.baseGameTokens.length) {
      details(`游戏本体提供（${resource.baseGameTokens.length}）`, resource.baseGameTokens, meta);
    }
    if (resource.references && resource.references.length) {
      details(`依赖条目（${resource.references.length}）`,
        resource.references.map(ref => `${ref.guid}${ref.slot == null ? '' : ' · ID ' + ref.slot}` +
          ` · ${ref.property}` + (ref.providedByBase ? ' · 游戏本体已提供' : '')), meta);
    }
    if (resource.duplicate) {
      details(`完全相同的文件（${resource.duplicates.length}）`,
        resource.duplicates.map(path => ({text: path, click: () => showResource(path)})), meta);
    }

    const tags = el('div', undefined, 'libtags');
    (resource.tags || []).forEach(tag => tags.append(el('span', tag)));
    meta.append(tags);
    meta.append(tagEditor(resource));

    const label = el('label');
    const check = el('input');
    check.type = 'checkbox';
    check.checked = selected.has(resource.path);
    check.onchange = () => {
      if (check.checked) selected.add(resource.path);
      else selected.delete(resource.path);
      statusLine();
      updateButtons();
    };
    label.append(check, document.createTextNode('选择'));
    meta.append(label);

    card.append(meta);
    KK.applyLibraryHooks(resource, card, meta);
    return card;
  }

  function tagEditor(resource) {
    const box = el('details');
    box.append(el('summary', '编辑标签'));
    const input = el('input');
    input.value = (resource.tags || []).join(', ');
    input.placeholder = '标签，以逗号分隔';
    input.setAttribute('aria-label', '标签：' + resource.file);
    const save = el('button', '保存标签');
    save.type = 'button';
    save.onclick = async () => {
      save.disabled = true;
      try {
        const result = await api.mutate('/api/library-tags', {
          path: resource.path,
          tags: input.value.split(/[,，]/).map(tag => tag.trim()).filter(Boolean)
        });
        resource.tags = result.tags;
        render();
      } catch (error) {
        alert(error.message);
      } finally {
        save.disabled = false;
      }
    };
    box.append(input, save);
    return box;
  }

  function showResource(path) {
    query.value = path;
    kind.value = '';
    state.value = '';
    tagFilter.value = '';
    page = 0;
    render();
    window.scrollTo(0, 0);
  }

  function render() {
    const text = query.value.trim().toLowerCase();
    const kindValue = kind.value, stateValue = state.value;
    const tag = tagFilter.value.trim().toLowerCase();

    lastList = library.filter(resource => {
      const searchable = [resource.path, ...resource.mods, ...(resource.tags || []),
        ...(resource.folderTags || []),
        ...(resource.references || []).map(ref => ref.guid)].join('\n').toLowerCase();
      if (text && !searchable.includes(text)) return false;
      if (kindValue && resource.kind !== kindValue) return false;
      if (tag && ![...(resource.tags || []), ...(resource.folderTags || [])]
        .some(value => value.toLowerCase().includes(tag))) return false;
      if (!stateValue) return true;
      if (stateValue === 'missing') {
        const verdict = KK.deps.card(resource.path);
        return Boolean(verdict) && verdict.state === 'missing';
      }
      if (stateValue === 'duplicate') return resource.duplicate;
      if (stateValue === 'mods') return resource.mods.length > 0;
      if (stateValue === 'unknown') {
        const verdict = KK.deps.card(resource.path);
        return !verdict || verdict.state === 'unknown';
      }
      return true;
    });

    const pages = Math.max(1, Math.ceil(lastList.length / SIZE));
    page = Math.max(0, Math.min(page, pages - 1));
    statusLine();
    KK.refreshPager();

    element.replaceChildren();
    for (const resource of lastList.slice(page * SIZE, (page + 1) * SIZE)) {
      element.append(buildCard(resource));
    }
    updateButtons();
  }

  async function moveSelected(toTrash) {
    if (busy || !selected.size) return;
    if (selected.size > 500) { alert('每批最多选择 500 个资源'); return; }
    const target = toTrash ? ''
      : destination.value.trim().replaceAll('\\', '/').replace(/\/+$/, '');
    if (!toTrash && !target) { destination.focus(); return; }
    if (!confirm(`移动 ${selected.size} 个文件到 ` +
      `${toTrash ? 'UserData_trash' : 'UserData/' + target}？`)) return;
    busy = true;
    updateButtons();
    try {
      const result = await api.mutate('/api/library-move',
        {paths: [...selected], destination: target});
      undoMoves.push(result.id);
      remember();
      selected.clear();
      await api.reloadLibrary();
      await KK.deps.reload();
      preserveViewport(() => render());
      if (result.warning) alert(result.warning);
    } catch (error) {
      alert('移动失败：' + error.message);
    } finally {
      busy = false;
      updateButtons();
    }
  }

  async function undoMove() {
    if (busy || !undoMoves.length) return;
    busy = true;
    updateButtons();
    try {
      const result = await api.mutate('/api/library-restore',
        {id: undoMoves[undoMoves.length - 1]});
      undoMoves.pop();
      remember();
      await api.reloadLibrary();
      await KK.deps.reload();
      preserveViewport(() => render());
      if (result.warning) alert(result.warning);
    } catch (error) {
      alert('恢复失败：' + error.message);
    } finally {
      busy = false;
      updateButtons();
    }
  }

  query.oninput = () => { page = 0; render(); };
  [kind, state, tagFilter].forEach(input => {
    input.oninput = () => { page = 0; render(); };
    input.onchange = () => { page = 0; render(); };
  });
  move.onclick = () => moveSelected(false);
  trash.onclick = () => moveSelected(true);
  undo.onclick = undoMove;
  pickPage.onclick = () => {
    lastList.slice(page * SIZE, (page + 1) * SIZE)
      .forEach(resource => selected.add(resource.path));
    render();
  };
  clear.onclick = () => { selected.clear(); render(); };

  const view = KK.registerView({
    id: 'library',
    label: '角色卡 / 场景 / 服装卡',
    order: 20,
    controls,
    element,
    render(params) {
      if (params && params.path) {
        query.value = params.path;
        kind.value = params.kind || '';
        state.value = '';
        tagFilter.value = '';
        page = 0;
      }
      render();
    },
    pageInfo() {
      return {current: page, pages: Math.max(1, Math.ceil(lastList.length / SIZE))};
    },
    go(delta) {
      page = Math.max(0, page + delta);
      render();
    }
  });

  window.KK_LIBRARY_VIEW = view;
})();
