'use strict';
/* ============================================================
   check-frontend-runtime.cjs —— 把前端 classic script 真的跑一遍
   ------------------------------------------------------------
   背景：这个项目的前端是零构建的 classic script，没有浏览器可用
   （agent-browser 不支持 Windows）。静态检查能验「id 拼对了没」，
   但验不了「这段代码执行到那里会不会炸」。

   本脚本做的事：
     1. 按 index.html 里真实的 <script> 顺序，把 8 个文件依次
        放进一个 vm 上下文执行 —— 抓模块顶层的报错。
     2. DOM 用真实 index.html 构建（见 dom-stub.cjs），
        fetch 用后端真实响应做桩（见 fixtures/，由 capture-fixtures.py 抓）。
     3. 未登录态 → 断言登录门正确显示。
     4. 模拟完整登录（填表 → submit → /auth/login → /auth/me → 进主界面）。
     5. 逐个调用 6 个页面的 render()，抓运行时异常。
        这一步是重点：真实浏览器里 app.go() 会把异常 catch 成
        「页面出错了」卡片，用户只看到白卡片，看不出哪一行炸的。
     6. 交互：切登录 tab、点导航、切老人模式、点账号弹窗退出。

   ★ 故意让它「会失败」的两个设计：
     - fetch 打到没有 fixture 的路径 → 记一条 UNMOCKED，算失败。
       新加的接口调用点必须显式补 fixture，不能悄悄放过。
     - 出现 unhandledRejection → 算失败。chat.js 的 loadMsgs 是
       fire-and-forget，它抛错时浏览器控制台一闪而过，页面看着正常。

   用法：
     node frontend/tools/check-frontend-runtime.cjs [-v]
     退出码 0 = 全通过，1 = 有失败
   ============================================================ */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { createDocument } = require('./dom-stub.cjs');

const ROOT = path.resolve(__dirname, '..');
const HTML_PATH = path.join(ROOT, 'index.html');
const FIXTURES = path.join(__dirname, 'fixtures');
const VERBOSE = process.argv.includes('-v');

let pass = 0, fail = 0;
const failures = [];

/** ★ 必须挂上：chat.js 的 loadMsgs 是 fire-and-forget，
 *  它抛错时浏览器控制台一闪而过、页面看着正常 —— 正是要抓的那种。
 *  记成失败而不是忽略。 */
const REJECTIONS = [];
process.on('unhandledRejection', (e) => {
  REJECTIONS.push((e && e.message) ? e.message + '\n' + String(e.stack).split('\n')[1]
                                   : String(e));
});
process.on('uncaughtException', (e) => {
  REJECTIONS.push('[uncaught] ' + (e && e.message ? e.message : String(e)));
});

function check(name, ok, detail) {
  if (ok) {
    pass++;
    if (VERBOSE) console.log('  \x1b[32m✓\x1b[0m ' + name);
  } else {
    fail++;
    failures.push({ name, detail });
    console.log('  \x1b[31m✗\x1b[0m ' + name + (detail ? '\n      ' + String(detail).split('\n').join('\n      ') : ''));
  }
}

/** 分节：顺便把上一节的通过数打出来。
 *  （不这么做的话，全绿时屏幕上一片空白，看不出到底跑了多少项） */
let _sec = null;
function section(title) {
  if (_sec) {
    const dp = pass - _sec.p, df = fail - _sec.f;
    console.log('     ' + (df ? '\x1b[31m' : '\x1b[32m') + dp + ' 项通过' +
      (df ? '，' + df + ' 项失败' : '') + '\x1b[0m');
  }
  console.log('\n\x1b[1m' + title + '\x1b[0m');
  _sec = { p: pass, f: fail };
}

/* ---------------- fixture ---------------- */

function loadFixtures() {
  const out = {};
  for (const f of fs.readdirSync(FIXTURES)) {
    if (!f.endsWith('.json')) continue;
    out[f.replace(/\.json$/, '')] = JSON.parse(fs.readFileSync(path.join(FIXTURES, f), 'utf8'));
  }
  return out;
}

const ROUTES = {
  'GET /health': 'health',
  'GET /auth/me': 'auth_me',
  'POST /auth/login': 'auth_login',
  'GET /farm/overview': 'farm_overview',
  'GET /farm/farms': 'farm_farms',
  'GET /farm/crops': 'farm_crops',
  'GET /farm/growth-records': 'farm_growth_records',
  'GET /chat/sessions': 'chat_sessions',
  'GET /knowledge/stats': 'knowledge_stats',
  'GET /knowledge/diseases': 'knowledge_diseases',
  'GET /pesticide/usage': 'pesticide_usage'
};
const ROUTE_RE = [
  [/^GET \/chat\/sessions\/[^/]+$/, 'chat_session_detail'],
  [/^GET \/knowledge\/diseases\/[^/]+$/, 'knowledge_disease_detail']
];

/* ---------------- 环境 ---------------- */

function makeLocalStorage(seed) {
  const m = new Map(Object.entries(seed || {}));
  return {
    getItem: k => (m.has(String(k)) ? m.get(String(k)) : null),
    setItem: (k, v) => { m.set(String(k), String(v)); },
    removeItem: k => { m.delete(String(k)); },
    clear: () => m.clear(),
    key: i => Array.from(m.keys())[i] ?? null,
    get length() { return m.size; },
    _dump: () => Object.fromEntries(m)
  };
}

function createEnv(opts) {
  const fixtures = opts.fixtures;
  const html = opts.html;
  const doc = createDocument(html);
  const unmocked = [];
  const fetchLog = [];
  const timers = [];
  const consoleLog = [];

  // ★ 事件总线挂在 window 上（app.js 用的是 window.addEventListener）。
  //   第一版我把它挂在 document 上，结果 hashchange 永远派发不到 ——
  //   路由监听器形同虚设，而测试照样「全绿」。
  const winListeners = new Map();
  function winDispatch(type, evt) {
    const e = evt || { type };
    for (const fn of (winListeners.get(type) || []).slice()) fn.call(sandbox, e);
  }

  const loc = {
    protocol: opts.protocol || 'http:',
    host: '127.0.0.1:8001',
    hostname: '127.0.0.1',
    port: opts.port !== undefined ? opts.port : '8001',
    origin: 'http://127.0.0.1:8001',
    search: opts.search || '',
    pathname: '/',
    _hash: '',
    get hash() { return loc._hash; },
    set hash(v) {
      const nv = String(v);
      if (nv === loc._hash) return;
      loc._hash = nv;
      winDispatch('hashchange');
    },
    reload() {}, assign() {}, replace() {}
  };

  function makeResponse(status, body) {
    const txt = typeof body === 'string' ? body : JSON.stringify(body);
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: String(status),
      headers: { get: () => 'application/json' },
      text: () => Promise.resolve(txt),
      json: () => Promise.resolve(JSON.parse(txt))
    };
  }

  function fetchStub(url, init) {
    const method = ((init && init.method) || 'GET').toUpperCase();
    const pathOnly = String(url)
      .replace(/^https?:\/\/[^/]+/, '')
      .replace(/^\/api\/v1/, '')
      .split('?')[0];
    const route = method + ' ' + pathOnly;
    fetchLog.push(route);

    let key = ROUTES[route];
    if (!key) {
      for (const [re, k] of ROUTE_RE) if (re.test(route)) { key = k; break; }
    }
    if (!key) {
      unmocked.push(route);
      return Promise.resolve(makeResponse(501, {
        detail: '__UNMOCKED__ ' + route +
          ' —— 补进 tools/capture-fixtures.py 的 simple 列表后重抓'
      }));
    }
    if (!(key in fixtures)) {
      unmocked.push(route + ' (缺 fixture: ' + key + ')');
      return Promise.resolve(makeResponse(501, { detail: 'missing fixture ' + key }));
    }
    return Promise.resolve(makeResponse(200, fixtures[key]));
  }

  class FormDataStub {
    constructor() { this._entries = []; }
    append(k, v) { this._entries.push([k, v]); }
    get(k) { const e = this._entries.find(x => x[0] === k); return e ? e[1] : null; }
    getAll(k) { return this._entries.filter(x => x[0] === k).map(x => x[1]); }
    has(k) { return this._entries.some(x => x[0] === k); }
    entries() { return this._entries.slice(); }
  }

  const sandbox = {
    document: doc,
    location: loc,
    localStorage: makeLocalStorage(opts.storage),
    fetch: fetchStub,
    FormData: FormDataStub,
    URL: Object.assign(
      function URLShim(u, base) { return new (require('url').URL)(u, base); },
      { createObjectURL: () => 'blob:stub/' + (++createEnv._blobSeq) }
    ),
    TextDecoder: require('util').TextDecoder,
    URLSearchParams,
    console: {
      log: (...a) => consoleLog.push(['log', a.join(' ')]),
      warn: (...a) => consoleLog.push(['warn', a.join(' ')]),
      error: (...a) => consoleLog.push(['error', a.join(' ')]),
      info: (...a) => consoleLog.push(['info', a.join(' ')]),
      debug: () => {}
    },
    // ★ 定时器做成惰性：toast 的自动消失 setTimeout 不需要真跑，
    //   真跑会让进程挂着 4.2 秒。这里只记录，不触发。
    setTimeout: (fn, ms) => { timers.push(ms || 0); return timers.length; },
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    navigator: { userAgent: 'node-dom-stub', language: 'zh-CN' },
    innerWidth: opts.innerWidth || 1280,
    innerHeight: 800,
    scrollTo: () => {},
    alert: () => {},
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  };

  // window 级事件（app.js 在这里注册 hashchange）
  sandbox.addEventListener = (t, fn) => {
    if (typeof fn !== 'function') return;
    if (!winListeners.has(t)) winListeners.set(t, []);
    winListeners.get(t).push(fn);
  };
  sandbox.removeEventListener = (t, fn) => {
    const a = winListeners.get(t);
    if (a) { const i = a.indexOf(fn); if (i !== -1) a.splice(i, 1); }
  };
  sandbox.dispatchEvent = (e) => { winDispatch(e.type, e); return true; };

  const ctx = vm.createContext(sandbox);
  vm.runInContext('var window = globalThis; var self = globalThis;', ctx);

  return {
    ctx, sandbox, doc, loc, unmocked, fetchLog, timers, consoleLog,
    localStorage: sandbox.localStorage,
    /** 微任务清空：页面里的 .then 链是嵌套的，多跑几轮 setImmediate 才到底 */
    flush: async (n) => {
      for (let i = 0; i < (n || 14); i++) await new Promise(r => setImmediate(r));
    }
  };
}
createEnv._blobSeq = 0;

/* ---------------- 加载脚本 ---------------- */

/** 从 index.html 里读真实的 <script src>，不硬编码顺序 ——
 *  这样「加了页面文件但忘了加进 index.html」会直接暴露 */
function scriptList(html) {
  const out = [];
  const re = /<script\s+src="([^"]+)"\s*>/g;
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out;
}

function loadScripts(env, scripts) {
  const errors = [];
  for (const rel of scripts) {
    const p = path.join(ROOT, rel);
    if (!fs.existsSync(p)) {
      errors.push({ file: rel, error: new Error('文件不存在：' + p) });
      continue;
    }
    const src = fs.readFileSync(p, 'utf8');
    try {
      vm.runInContext(src, env.ctx, { filename: rel });
    } catch (e) {
      errors.push({ file: rel, error: e });
    }
  }
  return errors;
}

/* ---------------- 主流程 ---------------- */

(async function main() {
  const html = fs.readFileSync(HTML_PATH, 'utf8');
  const fixtures = loadFixtures();
  const scripts = scriptList(html);

  console.log('\n\x1b[1m前端运行时检查\x1b[0m  （DOM 桩 + vm 执行真实脚本）\n');

  /* ---- 1. 脚本清单 ---- */
  section('1. 加载');
  check('index.html 声明了 8 个脚本',
    scripts.length === 8, '实际 ' + scripts.length + ' 个：' + scripts.join(', '));
  const pageScripts = scripts.filter(s => s.includes('/pages/'));
  const fixtureCount = Object.keys(fixtures).length;
  check('fixture 已抓取（' + fixtureCount + ' 份）', fixtureCount >= 12,
    '先跑 tools/capture-fixtures.py');

  /* ---- 2. 匿名态：登录门 ---- */
  section('2. 匿名态（未登录）');
  const anon = createEnv({ html, fixtures, storage: {} });
  const anonErrors = loadScripts(anon, scripts);
  check('8 个脚本顶层执行无异常', anonErrors.length === 0,
    anonErrors.map(e => e.file + ': ' + e.error.message).join('\n'));
  if (anonErrors.length) { report(); return; }

  const Agri = anon.sandbox.Agri || (anon.sandbox.window && anon.sandbox.window.Agri);
  check('window.Agri 命名空间已建立', !!Agri);
  await anon.flush();

  check('未登录时登录门可见（#gate.hidden === false）',
    anon.doc.getElementById('gate').hidden === false);
  check('未登录时主界面隐藏（#app.hidden === true）',
    anon.doc.getElementById('app').hidden === true);
  check('登录门错误区默认隐藏', anon.doc.getElementById('gateErr').hidden === true);

  /* ---- 3. 页面注册 ---- */
  section('3. 页面注册');
  const pageKeys = Object.keys(Agri.pages);
  // 三向核对：磁盘上的 pages/*.js == index.html 里声明的 == 实际注册的。
  // 只比数量是不够的 —— 漏掉一个 script 标签时数量仍然相等，检查会假绿。
  const diskPages = fs.readdirSync(path.join(ROOT, 'js/pages'))
    .filter(f => f.endsWith('.js')).map(f => f.replace(/\.js$/, '')).sort();
  const declaredPages = pageScripts.map(s => path.basename(s, '.js')).sort();
  const registeredPages = pageKeys.slice().sort();
  check('磁盘上的页面文件 == index.html 声明 == 已注册（' + diskPages.length + ' 个）',
    JSON.stringify(diskPages) === JSON.stringify(declaredPages) &&
    JSON.stringify(declaredPages) === JSON.stringify(registeredPages),
    '磁盘 [' + diskPages.join(', ') + ']\n声明 [' + declaredPages.join(', ') +
    ']\n注册 [' + registeredPages.join(', ') + ']');
  for (const k of pageKeys) {
    const p = Agri.pages[k];
    check('页面 ' + k + ' 元数据完整（id 一致 / label / render）',
      p && p.id === k && typeof p.label === 'string' && p.label &&
      typeof p.render === 'function' && !!p.icon,
      JSON.stringify({ id: p && p.id, label: p && p.label, icon: p && p.icon,
        render: typeof (p && p.render) }));
  }

  /* ---- 4. cfg：三种打开方式都要能算出 apiBase ---- */
  section('4. 配置解析（core.js resolveBase）');
  const cases = [
    ['同源（后端托管 8001）', { port: '8001', protocol: 'http:' }, 'http://127.0.0.1:8001/api/v1'],
    ['file:// 双击打开', { port: '', protocol: 'file:' }, 'http://127.0.0.1:8001/api/v1'],
    ['?api= 指定局域网后端', { port: '8080', protocol: 'http:', search: '?api=http://192.168.1.9:8001' },
      'http://192.168.1.9:8001/api/v1'],
    ['?api= 结尾带斜杠', { port: '8080', protocol: 'http:', search: '?api=http://192.168.1.9:8001/' },
      'http://192.168.1.9:8001/api/v1']
  ];
  for (const [label, o, want] of cases) {
    const e = createEnv({ html, fixtures, ...o });
    const errs = loadScripts(e, ['js/core.js']);
    const A = e.sandbox.Agri;
    check('apiBase · ' + label, !errs.length && A && A.cfg.apiBase === want,
      '得到 ' + (A && A.cfg.apiBase) + '，期望 ' + want);
  }

  /* ---- 5. 完整登录流程（走真实的表单 submit） ---- */
  section('5. 登录流程（模拟填表 + submit）');
  const app = Agri.app;
  anon.doc.getElementById('gateUser').value = 'demo';
  anon.doc.getElementById('gatePass').value = 'demo1234';
  anon.doc.getElementById('gateForm').dispatchEvent({
    type: 'submit', target: anon.doc.getElementById('gateForm')
  });
  await anon.flush();
  check('登录后主界面可见（#app.hidden === false）',
    anon.doc.getElementById('app').hidden === false,
    '登录门 err=' + anon.doc.getElementById('gateErr').textContent);
  check('登录后 token 已写入 localStorage',
    !!anon.localStorage.getItem('agri.token'));
  check('登录后顶栏显示用户名', anon.doc.getElementById('whoName').textContent === '演示农户',
    '实际 ' + JSON.stringify(anon.doc.getElementById('whoName').textContent));
  check('导航渲染出 ' + pageKeys.length + ' 项',
    anon.doc.getElementById('nav').querySelectorAll('[data-route]').length === pageKeys.length);
  check('底部 tabbar 渲染出 ' + pageKeys.length + ' 项',
    anon.doc.getElementById('tabbar').querySelectorAll('[data-route]').length === pageKeys.length);
  check('降级提示条正常显示（llm/vision 未配置）',
    anon.doc.getElementById('banner').hidden === false &&
    /未配置大模型/.test(anon.doc.getElementById('banner').textContent));

  /* ★ 存储降级的判据必须落在「逻辑」上，不能落在「当前环境值」上。
     为什么：fixture 里的 storage 会随 MinIO 好没好而变化 ——
     直接断言「一定出现对象存储提示」会在 MinIO 修好之后假红。
     所以这里覆盖三种输入，把两个分支都走一遍。
     （背景见 D25：MinIO 进程活着但 S3 操作全被拒时，后端静默降级到
       本地磁盘，而 minio 字段仍是 'up'。只看 minio 这条提示永不出现。） */
  const healthCases = [
    ['storage=local（已降级）', { storage: 'local' }, true],
    ['storage=minio + 端口通', { storage: 'minio', minio: 'up' }, false],
    ['storage=minio + 端口断', { storage: 'minio', minio: 'down' }, true],
  ];
  for (const [label, patch, wantWarn] of healthCases) {
    const e = createEnv({
      html,
      fixtures: Object.assign({}, fixtures, {
        /* ★ key 用**文件名**（'health'），不是路由（'GET /health'）——
           loadFixtures() 是按文件名建索引的，ROUTES 才是路由→文件名。
           这两套 key 空间很容易搞混：写错 key 时 Object.assign 会静默
           变成「只用 patch 当 fixture」，测试看起来在跑，其实没测到东西。 */
        health: Object.assign({}, fixtures.health, patch),
      }),
      storage: {},
    });
    loadScripts(e, scripts);
    /* 注意：loadBanner 挂在 boot() 里，而 boot() 只在登录成功后调用 ——
       不先走一遍登录，banner 永远是空的，断言会假红。 */
    e.doc.getElementById('gateUser').value = 'demo';
    e.doc.getElementById('gatePass').value = 'demo1234';
    e.doc.getElementById('gateForm').dispatchEvent({
      type: 'submit', target: e.doc.getElementById('gateForm'),
    });
    await e.flush(8);
    const txt = e.doc.getElementById('banner').textContent;
    check('提示条 · ' + label + ' → ' + (wantWarn ? '报出' : '不报') + '存储问题',
      /对象存储|图片存储/.test(txt) === wantWarn, 'banner = ' + JSON.stringify(txt));
  }

  /* ---- 6. 逐页 render ---- */
  section('6. 逐页渲染（抓运行时异常）');
  const view = anon.doc.getElementById('view');
  for (const id of declaredPages) {
    const page = Agri.pages[id];
    if (!page) {
      check('render · ' + id + ' 页面已注册', false, 'Agri.pages.' + id + ' 不存在（脚本没加载成功？）');
      continue;
    }
    view.innerHTML = '';
    let err = null;
    try {
      await page.render(view);
      await anon.flush(6);   // 页面内的 fire-and-forget 子渲染
    } catch (e) {
      err = e;
    }
    check('render · ' + id + ' 无异常', !err,
      err ? (err.message + '\n' + String(err.stack).split('\n').slice(1, 4).join('\n')) : '');
    const txt = view.textContent;
    check('render · ' + id + ' 产出了内容（>60 字）', txt.length > 60,
      '只有 ' + txt.length + ' 字：' + JSON.stringify(txt.slice(0, 80)));
    check('render · ' + id + ' 未落到「页面出错了」',
      txt.indexOf('页面出错了') === -1,
      '异常被 app.go() 的 catch 吞了 —— 真实浏览器里用户只会看到这张卡片');
  }

  /* ---- 7. 走 app.go() 路由（验证 catch 分支没被触发） ---- */
  section('7. 路由 go()（含错误兜底分支）');
  for (const id of declaredPages) {
    if (!Agri.pages[id]) continue;   // 上面已经报过「未注册」
    app.go(id);
    await anon.flush(8);
    check('go("' + id + '") 后 current 正确', app.current === id, '实际 ' + app.current);
    check('go("' + id + '") 未触发错误卡片',
      view.textContent.indexOf('页面出错了') === -1);
  }
  app.go('__nonexistent__');
  await anon.flush(6);
  check('未知路由回落到首页', app.current === 'home', '实际 ' + app.current);

  /* ---- 8. 交互 ---- */
  section('8. 交互');

  // 8.1 切注册 tab（匿名环境里再点一次）
  // 注意：桩不支持后代选择器（"#gateTab [data-tab=..]"），用属性选择器就够。
  // 这里写错的是我的测试，不是产品代码 —— 别去放宽 dom-stub。
  const regBtn = anon.doc.querySelector('[data-tab="register"]');
  check('找得到注册 tab 按钮', !!regBtn);
  if (regBtn) {
    regBtn.dispatchEvent({ type: 'click', target: regBtn });
    check('点「注册」后 #gateExtra 显示', anon.doc.getElementById('gateExtra').hidden === false);
    check('点「注册」后提交按钮文案变为「注册并进入」',
      anon.doc.getElementById('gateSubmit').textContent === '注册并进入',
      '实际 ' + JSON.stringify(anon.doc.getElementById('gateSubmit').textContent));
    const loginBtn = anon.doc.querySelector('[data-tab="login"]');
    loginBtn.dispatchEvent({ type: 'click', target: loginBtn });
    check('切回「登录」后 #gateExtra 隐藏', anon.doc.getElementById('gateExtra').hidden === true);
  }

  // 8.2 点导航切页
  const navBtns = anon.doc.getElementById('nav').querySelectorAll('[data-route]');
  const target = pageKeys.find(k => k !== 'home');
  const navBtn = navBtns.find(b => b.getAttribute('data-route') === target);
  check('导航里找得到 ' + target + ' 按钮', !!navBtn);
  if (navBtn) {
    navBtn.dispatchEvent({ type: 'click', target: navBtn });
    await anon.flush(8);
    check('点导航后切到 ' + target, app.current === target, '实际 ' + app.current);
    check('点导航后高亮同步',
      navBtn.classList.contains('is-on'));
  }

  // 8.3 老人模式切换
  const elderBefore = Agri.store.elder;
  const elderBtn = anon.doc.getElementById('elderBtn');
  elderBtn.dispatchEvent({ type: 'click', target: elderBtn });
  await anon.flush(8);
  check('点「大字」后老人模式取反', Agri.store.elder === !elderBefore,
    elderBefore + ' → ' + Agri.store.elder);
  check('body.elder 与状态一致',
    anon.doc.body.classList.contains('elder') === Agri.store.elder);
  check('老人模式图标同步', anon.doc.getElementById('elderIcon').textContent === (Agri.store.elder ? 'Aa+' : 'Aa'));

  // 8.4 账号弹窗 → 退出登录
  const who = anon.doc.getElementById('who');
  who.dispatchEvent({ type: 'click', target: who });
  await anon.flush(6);
  const mask = anon.doc.querySelector('.modal-mask');
  check('点账号弹出了模态框', !!mask);
  if (mask) {
    check('模态框里渲染了用户名',
      mask.textContent.indexOf('demo') !== -1);
    const okBtn = mask.querySelector('[data-x="1"]');
    check('模态框有确认按钮', !!okBtn);
    okBtn.dispatchEvent({ type: 'click', target: okBtn });
    await anon.flush(8);
    check('确认后退出登录（token 清空）', !anon.localStorage.getItem('agri.token'));
    check('退出后回到登录门', anon.doc.getElementById('gate').hidden === false);
    check('退出后提示「已退出登录」',
      anon.doc.getElementById('gateErr').textContent === '已退出登录',
      '实际 ' + JSON.stringify(anon.doc.getElementById('gateErr').textContent));
    check('模态框已从 DOM 移除', !anon.doc.querySelector('.modal-mask'));
  }

  /* ---- 9. 副作用：未 mock 的请求 / 未处理的 rejection / 控制台报错 ---- */
  section('9. 副作用');
  check('没有打到未 mock 的接口', anon.unmocked.length === 0,
    anon.unmocked.join('\n'));
  check('没有 unhandledRejection / uncaughtException', REJECTIONS.length === 0,
    REJECTIONS.join('\n'));
  const consoleErrors = anon.consoleLog.filter(([lvl]) => lvl === 'error');
  check('控制台无 error 输出', consoleErrors.length === 0,
    consoleErrors.map(x => x[1]).join('\n'));
  if (VERBOSE) {
    console.log('      fetch 调用记录：');
    const counts = {};
    for (const r of anon.fetchLog) counts[r] = (counts[r] || 0) + 1;
    for (const [r, n] of Object.entries(counts).sort()) console.log('        ' + n + '× ' + r);
  }

  report();
})().catch(e => {
  console.error('\n\x1b[31m检查器自身崩了：\x1b[0m', e);
  process.exit(2);
});

function report() {
  if (_sec) {
    const dp = pass - _sec.p, df = fail - _sec.f;
    console.log('     ' + (df ? '\x1b[31m' : '\x1b[32m') + dp + ' 项通过' +
      (df ? '，' + df + ' 项失败' : '') + '\x1b[0m');
    _sec = null;
  }
  const total = pass + fail;
  console.log('\n' + '─'.repeat(64));
  if (fail === 0) {
    console.log(`\x1b[32m全部通过：${pass}/${total}\x1b[0m`);
  } else {
    console.log(`\x1b[31m失败 ${fail} 项\x1b[0m，通过 ${pass}/${total}`);
    console.log('\n失败明细：');
    for (const f of failures) console.log('  · ' + f.name + (f.detail ? '\n      ' + f.detail : ''));
  }
  console.log('─'.repeat(64) + '\n');
  process.exit(fail === 0 ? 0 : 1);
}
