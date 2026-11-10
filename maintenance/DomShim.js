/* Minimal DOM for running the page modules under node.
 *
 * Just enough of Element/Document to boot app-core with its views and plugins,
 * so a broken registration or a missing hook fails here rather than in the
 * browser. Not a browser: no layout, no CSS, no event capture.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const byId = new Map();

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentElement = null;
    this._class = '';
    this._text = '';
    this._attrs = {};
    this._events = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this._value = undefined;
    this.disabled = false;
    this.checked = false;
    this.open = false;
  }
  get className() { return this._class; }
  set className(value) { this._class = String(value || ''); }
  // Browsers fall back to the first option's value when a <select> has no
  // explicit selection. An option without a value attribute reports its text.
  // Getting this wrong hid a filter bug that emptied the whole Mod grid, so the
  // shim mirrors the real rule instead of returning ''.
  get value() {
    if (this.tagName === 'OPTION') {
      return this._value === undefined ? this.textContent : this._value;
    }
    if (this.tagName === 'SELECT' && this._value === undefined) {
      const first = this.children.find(child => child instanceof Element &&
        child.tagName === 'OPTION');
      return first ? first.value : '';
    }
    return this._value === undefined ? '' : this._value;
  }
  set value(value) { this._value = String(value); }
  get id() { return this._id || ''; }
  set id(value) { this._id = value; byId.set(value, this); }
  get classList() {
    const list = () => this._class.split(/\s+/).filter(Boolean);
    const write = items => { this._class = [...new Set(items)].join(' '); };
    return {
      add: (...names) => write([...list(), ...names]),
      remove: (...names) => write(list().filter(item => !names.includes(item))),
      contains: name => list().includes(name),
      toggle: (name, force) => {
        const has = list().includes(name);
        const next = force === undefined ? !has : Boolean(force);
        if (next && !has) write([...list(), name]);
        if (!next && has) write(list().filter(item => item !== name));
        return next;
      }
    };
  }
  get textContent() {
    return this._text + this.children
      .map(child => (typeof child === 'string' ? child : child.textContent)).join('');
  }
  set textContent(value) { this.children = []; this._text = String(value); }
  append(...nodes) {
    for (const node of nodes) {
      if (typeof node === 'string') { this.children.push(node); continue; }
      node.parentElement = this;
      this.children.push(node);
    }
  }
  prepend(...nodes) {
    const prepared = nodes.map(node => {
      if (typeof node === 'string') return node;
      node.parentElement = this;
      return node;
    });
    this.children.unshift(...prepared);
  }
  replaceChildren(...nodes) {
    this.children = [];
    this._text = '';
    this.append(...nodes);
  }
  remove() {
    const parent = this.parentElement;
    if (!parent) return;
    parent.children = parent.children.filter(child => child !== this);
    this.parentElement = null;
  }
  setAttribute(name, value) {
    this._attrs[name] = String(value);
    if (name === 'class') this._class = String(value);
  }
  getAttribute(name) { return name in this._attrs ? this._attrs[name] : null; }
  removeAttribute(name) { delete this._attrs[name]; }
  addEventListener(type, handler) {
    (this._events[type] = this._events[type] || []).push(handler);
  }
  dispatch(type, event = {}) {
    for (const handler of this._events[type] || []) {
      handler({target: this, preventDefault() {}, ...event});
    }
  }
  click() { if (this.onclick) this.onclick({target: this, preventDefault() {}}); }
  closest(selector) {
    let node = this;
    while (node) {
      if (matches(node, selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
  querySelector(selector) {
    return descendants(this).find(node => matches(node, selector)) || null;
  }
  querySelectorAll(selector) {
    return descendants(this).filter(node => matches(node, selector));
  }
  getBoundingClientRect() { return {left: 0, top: 0, right: 0, bottom: 0}; }
}

function descendants(node, out = []) {
  for (const child of node.children) {
    if (child instanceof Element) { out.push(child); descendants(child, out); }
  }
  return out;
}

function matchesSimple(node, selector) {
  if (!(node instanceof Element)) return false;
  const data = selector.match(/^([a-z]*)\[data-([a-z-]+)="([^"]+)"\]$/i);
  if (data) {
    const [, tag, name, value] = data;
    if (tag && node.tagName !== tag.toUpperCase()) return false;
    const key = name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    return node.dataset[key] === value;
  }
  if (selector.startsWith('.')) return node.classList.contains(selector.slice(1));
  if (selector.startsWith('#')) return node.id === selector.slice(1);
  return node.tagName === selector.toUpperCase();
}

/* Descendant selectors ("ancestor child") are common in the tests, so keep a
 * real matcher instead of silently returning nothing for them. Comma lists are
 * supported because the page uses them for the "not a control" guard. */
function matchesOne(node, selector) {
  if (!(node instanceof Element)) return false;
  const parts = String(selector).trim().split(/\s+/);
  if (!matchesSimple(node, parts[parts.length - 1])) return false;
  let ancestor = node.parentElement;
  for (let index = parts.length - 2; index >= 0; index--) {
    while (ancestor && !matchesSimple(ancestor, parts[index])) {
      ancestor = ancestor.parentElement;
    }
    if (!ancestor) return false;
    ancestor = ancestor.parentElement;
  }
  return true;
}

function matches(node, selector) {
  if (!(node instanceof Element)) return false;
  return String(selector).split(',').some(part => part.trim() &&
    matchesOne(node, part.trim()));
}

function make(tag, className) {
  const node = new Element(tag);
  if (className) node.className = className;
  return node;
}

function buildDocument() {
  const body = make('body');
  const header = make('header');
  const tabs = make('nav', 'tabs');
  tabs.id = 'tabs';
  const controls = make('div');
  controls.id = 'controls';
  const status = make('div', 'status');
  status.id = 'status';
  const dock = make('div');
  dock.id = 'dock';
  const viewRoot = make('div');
  viewRoot.id = 'viewRoot';
  // Mirrors ModManager.html: the result line lives inside the search area.
  header.append(tabs, controls, status);
  body.append(header, dock, viewRoot);
  for (const [suffix, shape] of [['', 'pager'], ['Bottom', 'pager pager-bottom']]) {
    const bar = make('div', shape);
    for (const name of ['prev', 'next']) {
      const button = make('button');
      button.id = name + suffix;
      bar.append(button);
    }
    const label = make('span');
    label.id = 'page' + suffix;
    bar.append(label);
    body.append(bar);
  }
  const document = {
    body,
    documentElement: {scrollHeight: 4000},
    getElementById: id => byId.get(id) || null,
    querySelector: selector => descendants(body).find(node => matches(node, selector)) || null,
    querySelectorAll: selector => descendants(body).filter(node => matches(node, selector)),
    createElement: tag => new Element(tag),
    createTextNode: text => String(text),
    addEventListener() {}
  };
  return {document, body};
}

function response(status, payload) {
  return {
    ok: status < 400,
    status,
    json: async () => payload,
    blob: async () => payload,
    text: async () => JSON.stringify(payload)
  };
}

/* Boot the page scripts against `routes` (path -> handler(body) -> payload).
 * `protocol: 'file:'` exercises the read-only path with no server.
 * `confirmAnswer` lets a test accept or refuse a destructive confirmation.
 * `offline` makes every fetch fail the way a browser does once the local
 * manager process has exited (TypeError: Failed to fetch).
 * `viewportHeight` gives the page a window height so edge scrolling during a
 * sweep can be exercised; at 0 the page sees no viewport and never scrolls. */
function createApp({protocol = 'file:', catalog = [], library = [], deps = {},
                    routes = {}, confirmAnswer = () => false, offline = false,
                    viewportHeight = 0} = {}) {
  byId.clear();
  const {document} = buildDocument();
  const storage = {getItem: () => null, setItem() {}, removeItem() {}};

  const fetch = async (url, options = {}) => {
    if (offline) throw new TypeError('Failed to fetch');
    const target = String(url).split('?')[0];
    const handler = routes[target];
    if (!handler) return response(404, {error: 'not found'});
    const body = options.body ? JSON.parse(options.body) : undefined;
    return response(200, await handler(body, options));
  };

  const window = {
    MOD_CATALOG: catalog, KK_LIBRARY: library, KK_DEPS: deps,
    scrollX: 0, scrollY: 0,
    innerWidth: 1400, innerHeight: viewportHeight,
    /* A real scroll, clamped to the document, so a sweep can be tested against
     * a page that actually moves under the pointer. scrollTo({top}) is what the
     * viewport guard uses; scrollTo(x, y) is what the edge scroller uses. */
    scrollTo(x, y) {
      if (x && typeof x === 'object') {
        window.scrollX = x.left || 0;
        window.scrollY = x.top || 0;
        return;
      }
      window.scrollX = x || 0;
      if (y !== undefined) {
        const limit = Math.max(0, document.documentElement.scrollHeight - viewportHeight);
        window.scrollY = Math.min(limit, Math.max(0, y));
      }
    },
    confirm: message => confirmAnswer(message),
    fetch
  };
  const context = vm.createContext({
    window,
    document,
    location: {protocol, hash: ''},
    history: {replaceState() {}},
    localStorage: storage,
    sessionStorage: storage,
    requestAnimationFrame: callback => { callback(); return 1; },
    console,
    setTimeout,
    clearTimeout,
    alert() {},
    confirm: window.confirm,
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    Blob: class {},
    fetch
  });
  window.window = window;
  context.self = window;

  const root = path.resolve(__dirname, '..');
  function load(names) {
    for (const name of names) {
      vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context,
        {filename: name});
    }
    return window.KK;
  }

  return {context, window, document, byId, load, fetch};
}

module.exports = {createApp, Element, matches, descendants};
