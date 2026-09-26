'use strict';
/* ============================================================
   dom-stub.cjs —— 给前端 classic script 用的最小 DOM 实现
   ------------------------------------------------------------
   为什么需要它：
     项目前端是「零构建」的 classic script（见 core.js 顶部注释），
     没有任何打包器、也没有测试框架。结果是 6 个页面写完，
     一次都没在真实浏览器里跑过 —— 而这台机器上
     agent-browser 不支持 Windows（见系统提示），没有浏览器可用。

   于是退一步：把 DOM 做到「够跑」，用 vm 把前端代码真的执行一遍。
   它能抓到的东西很具体：
     - 模块顶层代码报错（拼错变量、依赖顺序错）
     - 页面 render() 里的 TypeError（真实浏览器里会被 app.go()
       的 .catch 吞成「页面出错了」卡片，用户只看到一张白卡片）
     - fire-and-forget 的异步渲染（chat.js 的 loadMsgs）抛错后
       变成 unhandledRejection，浏览器控制台里一闪而过
     - getElementById 拼错（返回 null 后 .hidden 报错）

   它抓不到的东西（必须说清楚，别高估它）：
     - CSS 布局问题（老人模式字号覆盖、窄屏堆叠）
     - 真实点击的坐标命中（我这里是逻辑冒泡，不是像素命中测试）
     - 图片渲染、字体、滚动位置

   ★ 两条设计原则：
     1. **用真实的 index.html 建 DOM**，不手搭。这样 index.html 里
        少了某个 id，测试就会失败 —— 与生产同构才有意义。
     2. **遇到不认识的选择器要抛错**，不能返回 null。
        静默返回 null 恰好就是我们要抓的那类 bug
        （getElementById 拼错时不报错，只是拿到 null）。
   ============================================================ */

const VOID = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr'
]);

const ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0',
  '#39': "'", '#34': '"'
};

function decodeEntities(s) {
  if (s.indexOf('&') === -1) return s;
  return s.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (m, body) => {
    if (body[0] === '#') {
      const hex = body[1] === 'x' || body[1] === 'X';
      const n = parseInt(hex ? body.slice(2) : body.slice(1), hex ? 16 : 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : m;
    }
    return Object.prototype.hasOwnProperty.call(ENTITIES, body)
      ? ENTITIES[body] : m;
  });
}

function escapeText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ---------------- 选择器 ---------------- */

/** 只支持前端实际用到的这几类：tag / #id / .class / [attr] / [attr="v"]，
 *  以及用逗号分隔的组合。出现别的（空格后代、>、:伪类）就抛错。 */
function compileCompound(sel) {
  if (/[\s>+~:]/.test(sel)) {
    throw new Error(
      'dom-stub 不支持的选择器语法：' + JSON.stringify(sel) +
      '（只支持 tag/#id/.class/[attr] 的逗号组合；' +
      '需要新语法请先在这里实现，别让它静默返回 null）');
  }
  const parts = [];
  const re = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([^\]]+)\]/g;
  let m, consumed = 0;
  while ((m = re.exec(sel))) {
    if (m.index !== consumed) throw new Error('选择器解析不了：' + JSON.stringify(sel));
    consumed = m.index + m[0].length;
    if (m[1]) parts.push({ kind: 'tag', v: m[1].toUpperCase() });
    else if (m[2]) parts.push({ kind: 'id', v: m[2] });
    else if (m[3]) parts.push({ kind: 'class', v: m[3] });
    else {
      const raw = m[4];
      const eq = raw.indexOf('=');
      if (eq === -1) parts.push({ kind: 'attr', name: raw.trim().toLowerCase(), value: null });
      else {
        let v = raw.slice(eq + 1).trim();
        if ((v[0] === '"' && v.endsWith('"')) || (v[0] === "'" && v.endsWith("'"))) {
          v = v.slice(1, -1);
        }
        parts.push({ kind: 'attr', name: raw.slice(0, eq).trim().toLowerCase(), value: v });
      }
    }
  }
  if (consumed !== sel.length) throw new Error('选择器解析不了：' + JSON.stringify(sel));
  return parts;
}

function compileSelector(sel) {
  return String(sel).split(',').map(s => s.trim()).filter(Boolean).map(compileCompound);
}

function matchCompound(el, parts) {
  for (const p of parts) {
    if (p.kind === 'tag' && el.tagName !== p.v) return false;
    if (p.kind === 'id' && el.getAttribute('id') !== p.v) return false;
    if (p.kind === 'class' && !el.classList.contains(p.v)) return false;
    if (p.kind === 'attr') {
      if (!el.hasAttribute(p.name)) return false;
      if (p.value !== null && el.getAttribute(p.name) !== p.value) return false;
    }
  }
  return true;
}

function matchSelector(el, groups) {
  return groups.some(parts => matchCompound(el, parts));
}

/* ---------------- 节点 ---------------- */

let NODE_SEQ = 0;

class StubNode {
  constructor(doc) {
    this.ownerDocument = doc;
    this.childNodes = [];
    this.parentNode = null;
    this._uid = ++NODE_SEQ;
  }
  get parentElement() {
    return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null;
  }
  get children() {
    return this.childNodes.filter(n => n.nodeType === 1);
  }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  appendChild(n) {
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    this.childNodes.push(n);
    return n;
  }
  insertBefore(n, ref) {
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    if (i === -1) this.childNodes.push(n); else this.childNodes.splice(i, 0, n);
    return n;
  }
  removeChild(n) {
    const i = this.childNodes.indexOf(n);
    if (i !== -1) { this.childNodes.splice(i, 1); n.parentNode = null; }
    return n;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get textContent() {
    return this.childNodes.map(n => n.textContent).join('');
  }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    if (v !== '' && v !== null && v !== undefined) {
      this.appendChild(this.ownerDocument.createTextNode(String(v)));
    }
  }
  // 事件：默认什么都不做（文本节点不参与）
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return true; }
}

class StubText extends StubNode {
  constructor(doc, data) {
    super(doc);
    this.nodeType = 3;
    this.nodeName = '#text';
    this.data = String(data);
  }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
}

class StubElement extends StubNode {
  constructor(doc, tagName) {
    super(doc);
    this.nodeType = 1;
    this.tagName = String(tagName).toUpperCase();
    this.nodeName = this.tagName;
    this._attrs = new Map();
    this._listeners = new Map();
    this.style = new Proxy({}, {
      get: (t, k) => (k in t ? t[k] : ''),
      set: (t, k, v) => { t[k] = v; return true; }
    });
    this.classList = makeClassList(this);
    this._value = null;      // 仅当显式赋过值时用
    this._checked = null;
    this._disabled = null;
  }

  /* ---- 属性 ---- */
  getAttribute(name) {
    const k = String(name).toLowerCase();
    if (k === 'class') return this._attrs.get('class') || '';
    return this._attrs.has(k) ? this._attrs.get(k) : null;
  }
  setAttribute(name, value) {
    const k = String(name).toLowerCase();
    this._attrs.set(k, String(value));
    if (k === 'value') this._value = String(value);
    if (k === 'checked') this._checked = true;
    if (k === 'disabled') this._disabled = true;
    if (k === 'selected') this._selected = true;
  }
  removeAttribute(name) {
    const k = String(name).toLowerCase();
    this._attrs.delete(k);
    if (k === 'value') this._value = null;
    if (k === 'checked') this._checked = null;
    if (k === 'disabled') this._disabled = null;
  }
  hasAttribute(name) { return this._attrs.has(String(name).toLowerCase()); }
  get id() { return this.getAttribute('id') || ''; }
  set id(v) { this.setAttribute('id', v); }
  get className() { return this._attrs.get('class') || ''; }
  set className(v) {
    this._attrs.set('class', String(v));
    this.classList._sync();
  }

  /* ---- 表单值 ---- */
  get type() {
    const t = this.getAttribute('type');
    if (t) return t.toLowerCase();
    if (this.tagName === 'INPUT') return 'text';
    if (this.tagName === 'BUTTON') return 'submit';
    return undefined;
  }
  set type(v) { this.setAttribute('type', v); }

  get name() { return this.getAttribute('name') || ''; }
  set name(v) { this.setAttribute('name', v); }

  get value() {
    if (this.tagName === 'SELECT') {
      const opts = this._descendants('OPTION');
      const sel = opts.find(o => o.hasAttribute('selected'));
      const pick = sel || opts[0];
      return pick ? (pick.getAttribute('value') !== null
        ? pick.getAttribute('value') : pick.textContent) : '';
    }
    if (this.tagName === 'TEXTAREA') return this.textContent;
    if (this.tagName === 'OPTION') {
      const v = this.getAttribute('value');
      return v !== null ? v : this.textContent;
    }
    if (this._value !== null) return this._value;
    return this.getAttribute('value') || '';
  }
  set value(v) {
    if (this.tagName === 'SELECT') {
      // 和浏览器一致：设置 select.value 会改动 option 的 selected
      for (const o of this._descendants('OPTION')) {
        if (o.value === String(v)) o.setAttribute('selected', ''); else o.removeAttribute('selected');
      }
      return;
    }
    if (this.tagName === 'TEXTAREA') { this.textContent = v; return; }
    this._value = v === null || v === undefined ? '' : String(v);
    this._attrs.set('value', this._value);
  }

  get checked() {
    if (this._checked !== null) return this._checked;
    return this.hasAttribute('checked');
  }
  set checked(v) { this._checked = !!v; }
  get disabled() {
    if (this._disabled !== null) return this._disabled;
    return this.hasAttribute('disabled');
  }
  set disabled(v) { this._disabled = !!v; }
  get selected() { return this.hasAttribute('selected'); }
  set selected(v) { if (v) this.setAttribute('selected', ''); else this.removeAttribute('selected'); }

  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(v) { if (v) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }

  get files() { return this._files || []; }
  set files(v) { this._files = v; }

  get dataset() {
    const out = {};
    for (const [k, v] of this._attrs) {
      if (k.startsWith('data-')) {
        out[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
      }
    }
    return out;
  }

  /* ---- 事件 ---- */
  addEventListener(type, fn) {
    if (typeof fn !== 'function') return;
    const k = String(type);
    if (!this._listeners.has(k)) this._listeners.set(k, []);
    const arr = this._listeners.get(k);
    if (!arr.includes(fn)) arr.push(fn);
  }
  removeEventListener(type, fn) {
    const arr = this._listeners.get(String(type));
    if (arr) {
      const i = arr.indexOf(fn);
      if (i !== -1) arr.splice(i, 1);
    }
  }
  dispatchEvent(evt) {
    const e = evt || {};
    if (!e.type) throw new Error('dispatchEvent 需要一个带 type 的事件对象');
    if (!e.target) e.target = this;
    if (!e.preventDefault) e.preventDefault = () => { e.defaultPrevented = true; };
    if (!e.stopPropagation) e.stopPropagation = () => { e._stopped = true; };
    // 从 target 往上冒泡（真实 DOM 的行为；closest 依赖 e.target 是原始目标）
    let node = this;
    while (node) {
      const direct = node._listeners && node._listeners.get(e.type);
      if (direct) for (const fn of direct.slice()) fn.call(node, e);
      const inline = node['on' + e.type];
      if (typeof inline === 'function') inline.call(node, e);
      if (e._stopped) break;
      node = node.parentNode;
    }
    return !e.defaultPrevented;
  }
  click() {
    if (this.tagName === 'INPUT' && this.type === 'file') return;  // 真浏览器里点击是打开选择框
    return this.dispatchEvent({ type: 'click' });
  }
  focus() {}
  blur() {}
  scrollIntoView() {}
  getBoundingClientRect() {
    return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 };
  }

  /* ---- 查询 ---- */
  closest(sel) {
    const g = compileSelector(sel);
    let n = this;
    while (n && n.nodeType === 1) {
      if (matchSelector(n, g)) return n;
      n = n.parentNode;
    }
    return null;
  }
  matches(sel) { return matchSelector(this, compileSelector(sel)); }
  querySelector(sel) {
    const g = compileSelector(sel);
    for (const d of this._descendants()) if (matchSelector(d, g)) return d;
    return null;
  }
  querySelectorAll(sel) {
    const g = compileSelector(sel);
    return this._descendants().filter(d => matchSelector(d, g));
  }
  /** 深度优先、文档序，不含自身 —— 与浏览器一致 */
  _descendants(tagFilter) {
    const out = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType !== 1) continue;
        if (!tagFilter || c.tagName === tagFilter) out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }

  /* ---- innerHTML ---- */
  get innerHTML() { return serializeChildren(this); }
  set innerHTML(html) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    parseInto(String(html), this, this.ownerDocument);
  }
  get outerHTML() { return serializeNode(this); }
  get tagText() { return this.textContent; }
}

function makeClassList(el) {
  const api = {
    _sync() { /* className 是唯一真源，无需缓存 */ },
    _list() { return (el.getAttribute('class') || '').split(/\s+/).filter(Boolean); },
    _set(list) { el._attrs.set('class', list.join(' ')); },
    contains(c) { return api._list().includes(c); },
    add(...cs) {
      const l = api._list();
      for (const c of cs) if (!l.includes(c)) l.push(c);
      api._set(l);
    },
    remove(...cs) {
      api._set(api._list().filter(x => !cs.includes(x)));
    },
    toggle(c, force) {
      const has = api.contains(c);
      const on = force === undefined ? !has : !!force;
      if (on) api.add(c); else api.remove(c);
      return on;
    },
    get length() { return api._list().length; },
    toString() { return api._list().join(' '); }
  };
  return api;
}

/* ---------------- HTML 解析 ---------------- */

function findTagEnd(html, start) {
  let i = start + 1, q = null;
  while (i < html.length) {
    const c = html[i];
    if (q) { if (c === q) q = null; }
    else if (c === '"' || c === "'") q = c;
    else if (c === '>') return i;
    i++;
  }
  return html.length;
}

function parseAttrs(s) {
  const out = [];
  let i = 0;
  while (i < s.length) {
    while (i < s.length && /\s/.test(s[i])) i++;
    if (i >= s.length) break;
    let j = i;
    while (j < s.length && !/[\s=]/.test(s[j])) j++;
    const name = s.slice(i, j).toLowerCase();
    if (!name) { i = j + 1; continue; }
    i = j;
    while (i < s.length && /\s/.test(s[i])) i++;
    let value = '';
    if (s[i] === '=') {
      i++;
      while (i < s.length && /\s/.test(s[i])) i++;
      const q = s[i];
      if (q === '"' || q === "'") {
        const e = s.indexOf(q, i + 1);
        value = s.slice(i + 1, e === -1 ? s.length : e);
        i = e === -1 ? s.length : e + 1;
      } else {
        let k = i;
        while (k < s.length && !/\s/.test(s[k])) k++;
        value = s.slice(i, k);
        i = k;
      }
    }
    if (name) out.push([name, decodeEntities(value)]);
  }
  return out;
}

function parseInto(html, target, doc) {
  const stack = [target];
  const top = () => stack[stack.length - 1];
  let i = 0;
  const addText = (raw) => {
    if (!raw) return;
    const txt = decodeEntities(raw);
    if (!txt) return;
    top().appendChild(doc.createTextNode(txt));
  };

  while (i < html.length) {
    const lt = html.indexOf('<', i);
    if (lt === -1) { addText(html.slice(i)); break; }
    if (lt > i) addText(html.slice(i, lt));

    if (html.startsWith('<!--', lt)) {
      const e = html.indexOf('-->', lt + 4);
      i = e === -1 ? html.length : e + 3;
      continue;
    }
    if (html.startsWith('<!', lt)) {
      const e = html.indexOf('>', lt);
      i = e === -1 ? html.length : e + 1;
      continue;
    }
    if (html.startsWith('</', lt)) {
      const e = html.indexOf('>', lt);
      const name = html.slice(lt + 2, e === -1 ? html.length : e).trim().toLowerCase();
      for (let k = stack.length - 1; k > 0; k--) {
        if (stack[k].tagName.toLowerCase() === name) { stack.length = k; break; }
      }
      i = e === -1 ? html.length : e + 1;
      continue;
    }

    const end = findTagEnd(html, lt);
    let inner = html.slice(lt + 1, end);
    let selfClose = false;
    if (inner.endsWith('/')) { selfClose = true; inner = inner.slice(0, -1); }
    const sp = inner.search(/\s/);
    const tagName = (sp === -1 ? inner : inner.slice(0, sp)).trim();
    const attrSrc = sp === -1 ? '' : inner.slice(sp);
    if (!tagName) { i = end + 1; continue; }

    const el = doc.createElement(tagName);
    for (const [k, v] of parseAttrs(attrSrc)) el.setAttribute(k, v);
    top().appendChild(el);

    const isVoid = VOID.has(tagName.toLowerCase());
    if (!selfClose && !isVoid) stack.push(el);
    i = end + 1;
  }
  return target;
}

/* ---------------- 序列化（innerHTML getter） ---------------- */

function serializeNode(n) {
  if (n.nodeType === 3) return escapeText(n.data);
  const attrs = [];
  for (const [k, v] of n._attrs) attrs.push(v === '' ? k : `${k}="${escapeText(v)}"`);
  const open = '<' + n.tagName.toLowerCase() + (attrs.length ? ' ' + attrs.join(' ') : '');
  if (VOID.has(n.tagName.toLowerCase())) return open + ' />';
  return open + '>' + serializeChildren(n) + '</' + n.tagName.toLowerCase() + '>';
}
function serializeChildren(el) {
  return el.childNodes.map(serializeNode).join('');
}

/* ---------------- document ---------------- */

function createDocument(html) {
  const doc = {
    nodeType: 9,
    readyState: 'complete',
    _listeners: new Map(),
    createElement(tag) { return new StubElement(doc, tag); },
    createTextNode(s) { return new StubText(doc, s); },
    createDocumentFragment() { return new StubElement(doc, '#fragment'); }
  };
  doc.documentElement = new StubElement(doc, 'html');
  doc.head = new StubElement(doc, 'head');
  doc.body = new StubElement(doc, 'body');
  doc.documentElement.appendChild(doc.head);
  doc.documentElement.appendChild(doc.body);
  doc.childNodes = [doc.documentElement];
  doc.documentElement.parentNode = doc;

  // ★ 用真实 index.html 建 DOM：少一个 id 测试就会红，与生产同构
  const bodyMatch = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html);
  const bodyHtml = bodyMatch ? bodyMatch[1] : html;
  const headMatch = /<head[^>]*>([\s\S]*?)<\/head>/i.exec(html);
  if (headMatch) parseInto(headMatch[1], doc.head, doc);
  parseInto(bodyHtml.replace(/<script[\s\S]*?<\/script>/gi, ''), doc.body, doc);

  const all = () => {
    const out = [];
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === 1) { out.push(c); walk(c); }
      }
    };
    walk(doc.documentElement);
    return out;
  };
  doc.getElementById = (id) => all().find(e => e.getAttribute('id') === id) || null;
  doc.querySelector = (sel) => {
    const g = compileSelector(sel);
    return all().find(e => matchSelector(e, g)) || null;
  };
  doc.querySelectorAll = (sel) => {
    const g = compileSelector(sel);
    return all().filter(e => matchSelector(e, g));
  };
  doc.addEventListener = (t, fn) => {
    if (!doc._listeners.has(t)) doc._listeners.set(t, []);
    doc._listeners.get(t).push(fn);
  };
  doc.removeEventListener = () => {};
  doc.dispatchEvent = (e) => {
    for (const fn of (doc._listeners.get(e.type) || []).slice()) fn.call(doc, e);
    return true;
  };
  return doc;
}

module.exports = {
  createDocument, StubElement, StubText, StubNode,
  parseInto, compileSelector, decodeEntities, VOID
};
