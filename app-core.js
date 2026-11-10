/* Page kernel: data, view registry, shared chrome and write access.
 *
 * Nothing in here knows what a mod, a card or a dependency is. Features live in
 * view-*.js and plugin-*.js and reach the page through what this file exposes:
 *
 *   KK.registerView({id, label, controls, element, render, go, pageInfo})
 *   KK.registerModFacet({id, label, options, match})   -> extra filter on Mod grid
 *   KK.onModCard(fn)                                  -> card slots for badges
 *   KK.media                                          -> thumbnail loader
 *
 * A plugin must never wrap another module's rendering: if it needs pixels on a
 * card it registers a hook, and if it needs its own screen it registers a view.
 */
(() => {
  'use strict';

  const data = {
    catalog: window.MOD_CATALOG || [],
    // Card records are not shipped with the page any more: the dependency view
    // fetches them from /api/library when it is opened. The global only exists
    // so the headless tests can seed cards without a server.
    library: window.KK_LIBRARY || [],
    deps: window.KK_DEPS || null
  };

  const $ = id => document.getElementById(id);
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  const views = [];
  const modHooks = [];
  const facets = [];
  const facetSelects = new Map();
  let facetHost = null;   // where the facet selects live, so they can be rebuilt
  const tabButtons = new Map();
  const pagerBars = ['', 'Bottom'].map(suffix => ({
    bar: $('prev' + suffix).closest('.pager'),
    previous: $('prev' + suffix), label: $('page' + suffix), next: $('next' + suffix)
  }));
  let active = null;

  /* ---------------------------------------------------------------- write API */

  // A local page outlives the process that served it. Every call therefore has
  // to survive the socket being gone, and the raw "Failed to fetch" that fetch
  // throws is useless to whoever is staring at the page: say what happened and
  // what to do instead.
  const OFFLINE = '本地管理器已退出，这个页面已经失效：请关闭此标签页，重新运行 KoikatsuModManager.exe';

  async function request(endpoint, options) {
    try {
      return await fetch(endpoint, options);
    } catch (error) {
      offline();
      throw new Error(OFFLINE);
    }
  }

  let offlineShown = false;
  function offline() {
    if (offlineShown || location.protocol !== 'http:' || !document.body) return;
    offlineShown = true;
    const bar = el('div', OFFLINE, 'offline-banner');
    bar.id = 'offlineBanner';
    document.body.append(bar);
  }

  const api = {
    token: '',
    OFFLINE,
    async mutate(endpoint, body) {
      await sessionReady;
      if (!api.token) throw new Error('请双击 Open-ModManager.bat 启动本地管理器');
      const response = await request(endpoint, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Catalog-Token': api.token},
        body: JSON.stringify(body || {})
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '操作失败');
      return result;
    },
    async read(endpoint) {
      await sessionReady;
      const response = await request(endpoint, {headers: {'X-Catalog-Token': api.token}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '读取失败');
      return result;
    }
  };

  const sessionReady = location.protocol === 'http:'
    ? request('/api/session')
      .then(response => {
        if (!response.ok) throw new Error('请通过本地管理器打开页面');
        return response.json();
      })
      .then(value => { api.token = value.token; })
      .catch(error => { api.token = ''; console.warn(error.message); })
    : Promise.resolve();

  /* ------------------------------------------------------------- dependencies */

  const deps = {
    get raw() { return data.deps; },
    card(path) { return (data.deps && data.deps.cards && data.deps.cards[path]) || null; },
    mod(path) { return (data.deps && data.deps.mods && data.deps.mods[path]) || null; },
    coverage(kinds) {
      const total = {total: 0, parsed: 0, unknown: 0, missing: 0, complete: 0};
      if (!data.deps) return total;
      for (const kind of kinds) {
        const values = data.deps.coverageByKind[kind];
        if (!values) continue;
        for (const key of Object.keys(total)) total[key] += values[key];
      }
      return total;
    },
    async reload() {
      if (location.protocol !== 'http:') return data.deps;
      // A server that is still indexing answers with a redirect to /settings,
      // so response.json() can fail on HTML. Keep the snapshot already loaded
      // from deps.js rather than throwing into the caller's boot path.
      try {
        const response = await fetch('/api/deps');
        if (response.ok) data.deps = await response.json();
      } catch (error) {
        console.warn('dependency snapshot refresh failed', error);
      }
      // The facet options are derived from this snapshot, so they have to be
      // recomputed before the caller re-renders the grid.
      refreshModFacets();
      return data.deps;
    }
  };

  /* ----------------------------------------------------------------- views */

  function registerView(view) {
    if (!view || !view.id || views.some(item => item.id === view.id)) return null;
    const tab = el('button', view.label || view.id, 'tab');
    tab.type = 'button';
    tab.id = view.id + 'Tab';
    tab.dataset.viewTab = view.id;
    tab.onclick = () => openView(view.id);
    $('tabs').append(tab);
    tabButtons.set(view.id, tab);
    if (view.controls) {
      view.controls.hidden = true;
      $('controls').append(view.controls);
    }
    /* A dock is the one row of a view that has to stay reachable while the grid
     * scrolls (select-all, render, delete). It lives outside <header> because a
     * sticky child cannot escape a parent it is taller than. */
    if (view.dock) {
      view.dock.hidden = true;
      $('dock').append(view.dock);
    }
    if (view.element) {
      view.element.hidden = true;
      $('viewRoot').append(view.element);
    }
    views.push(view);
    return view;
  }

  function openView(id, params) {
    const view = views.find(item => item.id === id);
    if (!view) return null;
    if (active === view && !params) { refreshPager(); return view; }
    if (active && active !== view && active.unmount) active.unmount();
    active = view;
    for (const item of views) {
      if (item.element) item.element.hidden = item !== view;
      if (item.controls) item.controls.hidden = item !== view;
      if (item.dock) item.dock.hidden = item !== view;
      tabButtons.get(item.id).classList.toggle('active', item === view);
    }
    // An empty sticky strip would still draw its border over the grid.
    $('dock').hidden = !view.dock;
    setStatus('');
    view.render(params);
    refreshPager();
    if (!params || params.focus !== false) window.scrollTo(0, 0);
    history.replaceState(null, '', '#' + id);
    return view;
  }

  function refreshPager() {
    const info = active && active.pageInfo ? active.pageInfo() : null;
    for (const item of pagerBars) {
      if (!item.bar) continue;
      item.bar.hidden = !info;
      if (!info) continue;
      item.previous.disabled = info.current <= 0;
      item.next.disabled = info.current >= info.pages - 1;
      item.label.textContent = `${info.current + 1} / ${info.pages}`;
    }
  }

  for (const item of pagerBars) {
    if (!item.bar) continue;
    item.previous.onclick = () => { if (active && active.go) { active.go(-1); refreshPager(); } };
    item.next.onclick = () => { if (active && active.go) { active.go(1); refreshPager(); } };
  }

  /* ------------------------------------------------------- slots and facets */

  function onModCard(hook) { modHooks.push(hook); }
  function applyHooks(hooks, ...args) {
    for (const hook of hooks) {
      try { hook(...args); } catch (error) { console.warn('card hook failed', error); }
    }
  }

  function registerModFacet(facet) { facets.push(facet); }

  function renderModFacets(container, onChange) {
    // Facets arrive as plugins load, so this is re-runnable: it rebuilds the
    // selects from whatever has registered so far. A facet may also compute its
    // options on demand -- an option that can only ever return nothing (a state
    // no record is in) is noise in the dropdown -- and whatever the user had
    // chosen survives the rebuild as long as that choice still exists.
    const chosen = new Map([...facetSelects].map(([id, select]) => [id, select.value]));
    facetHost = {container, onChange};
    container.replaceChildren();
    facetSelects.clear();
    for (const facet of facets) {
      const select = el('select');
      select.dataset.facet = facet.id;
      select.setAttribute('aria-label', facet.label);
      const options = typeof facet.options === 'function' ? facet.options() : facet.options;
      for (const [value, label] of options) {
        const option = el('option', label);
        option.value = value;
        select.append(option);
      }
      const keep = chosen.get(facet.id);
      select.value = options.some(([value]) => value === keep)
        ? keep : (options.length ? options[0][0] : '');
      select.onchange = onChange;
      facetSelects.set(facet.id, select);
      container.append(select);
    }
  }

  /* Only a changed answer set can change what a facet offers: a reloaded
   * dependency index, or a package whose verdict is no longer discoverable.
   * This is not part of every render -- rebuilding a select under the pointer
   * would swallow the click that opened it. */
  function refreshModFacets() {
    if (!facetHost) return;
    const before = [...facetSelects].map(([id, select]) => [id, select.value]);
    renderModFacets(facetHost.container, facetHost.onChange);
    const lost = before.some(([id, value]) => {
      const select = facetSelects.get(id);
      return select && select.value !== value;
    });
    // A selection that no longer exists has been reset to "all"; the grid still
    // shows the old subset, so it has to be refiltered once.
    if (lost) facetHost.onChange();
  }

  function facetValue(id) {
    const select = facetSelects.get(id);
    return select ? select.value : '';
  }

  /* ------------------------------------------------------------- utilities */

  let statusText = '';
  function setStatus(text) {
    statusText = text;
    $('status').textContent = text;
  }

  function preserveViewport(update) {
    const x = window.scrollX, y = window.scrollY, body = document.body;
    const minHeight = body.style.minHeight;
    // Keep the document tall while the old cards are replaced.
    body.style.minHeight = document.documentElement.scrollHeight + 'px';
    try { update(); } finally {
      window.scrollTo({left: x, top: y, behavior: 'instant'});
      requestAnimationFrame(() => {
        body.style.minHeight = minHeight;
        window.scrollTo({left: x, top: y, behavior: 'instant'});
      });
    }
  }

  function link(href, text, cls) {
    const anchor = el('a', text, cls);
    anchor.href = href;
    return anchor;
  }

  window.KK = {
    data, deps, api, sessionReady,
    registerView, openView, refreshPager,
    registerModFacet, renderModFacets, refreshModFacets, facetValue, facets,
    onModCard,
    applyModHooks: (record, card, meta) => applyHooks(modHooks, record, card, meta),
    setStatus, getStatus: () => statusText,
    preserveViewport, el, $, link,
    search: window.KK_MOD_SEARCH ? window.KK_MOD_SEARCH.create() : null,
    currentView: () => active && active.id
  };

  const settings = link('/settings', '目录设置', 'directory-settings');
  const tabs = document.querySelector('.tabs');
  if (tabs) tabs.append(settings);
})();
