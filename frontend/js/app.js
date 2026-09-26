/* ============================================================
   app.js —— 路由 / 登录门 / 启动
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, store = Agri.store, api = Agri.api;

  var app = Agri.app = {
    current: '',
    _gateTab: 'login',
    _msg: ''
  };

  /* ---------------- 登录 ---------------- */

  app.showGate = function (msg) {
    app._msg = msg || '';
    document.getElementById('gate').hidden = false;
    document.getElementById('app').hidden = true;
    var err = document.getElementById('gateErr');
    if (msg) { err.textContent = msg; err.hidden = false; }
    else err.hidden = true;
  };

  function hideGate() {
    document.getElementById('gate').hidden = true;
    document.getElementById('app').hidden = false;
  }

  function initGate() {
    var tabWrap = document.getElementById('gateTab');
    var extra = document.getElementById('gateExtra');
    var submit = document.getElementById('gateSubmit');
    var err = document.getElementById('gateErr');

    tabWrap.addEventListener('click', function (e) {
      var b = e.target.closest('.seg-btn');
      if (!b) return;
      app._gateTab = b.getAttribute('data-tab');
      [].forEach.call(tabWrap.children, function (x) {
        x.classList.toggle('is-on', x === b);
      });
      extra.hidden = app._gateTab !== 'register';
      submit.textContent = app._gateTab === 'register' ? '注册并进入' : '登录';
      err.hidden = true;
    });

    document.getElementById('gateForm').addEventListener('submit', function (e) {
      e.preventDefault();
      var u = document.getElementById('gateUser').value.trim();
      var p = document.getElementById('gatePass').value;
      if (!u || !p) return;
      submit.disabled = true;
      submit.textContent = '请稍候…';

      var p1 = app._gateTab === 'register'
        ? api.post('/auth/register', {
            username: u, password: p,
            display_name: document.getElementById('gateName').value.trim() || u
          })
        : Promise.resolve();

      p1
        .then(function () {
          return api.post('/auth/login', { username: u, password: p });
        })
        .then(function (tok) {
          store.setToken(tok.access_token);
          return api.get('/auth/me');
        })
        .then(function (me) {
          store.user = me;
          // 首次登录跟随用户偏好；用户手动切过就以本地为准
          if (localStorage.getItem('agri.elder') === null) {
            store.setElder(me.elder_mode !== false);
          }
          hideGate();
          boot();
        })
        .catch(function (e) {
          err.textContent = e.message || '登录失败';
          err.hidden = false;
        })
        .then(function () {
          submit.disabled = false;
          submit.textContent = app._gateTab === 'register' ? '注册并进入' : '登录';
        });
    });
  }

  /* ---------------- 导航 ---------------- */

  function navItems() {
    return Object.keys(Agri.pages).map(function (k) { return Agri.pages[k]; });
  }

  function renderNav() {
    var items = navItems();
    var html = items.map(function (p) {
      return '<button class="nav-item" data-route="' + p.id + '">' +
             ui.esc(p.label) + '</button>';
    }).join('');
    document.getElementById('nav').innerHTML = html;

    document.getElementById('tabbar').innerHTML = items.map(function (p) {
      return '<button class="nav-item" data-route="' + p.id + '">' +
             '<span class="ico">' + (p.icon || '·') + '</span>' +
             '<span>' + ui.esc(p.label) + '</span></button>';
    }).join('');

    document.getElementById('app').addEventListener('click', function (e) {
      var b = e.target.closest('[data-route]');
      if (!b) return;
      go(b.getAttribute('data-route'));
    });
  }

  function markNav(id) {
    [].forEach.call(document.querySelectorAll('[data-route]'), function (b) {
      b.classList.toggle('is-on', b.getAttribute('data-route') === id);
    });
  }

  /* ---------------- 路由 ---------------- */

  function go(id) {
    var page = Agri.pages[id] || Agri.pages.home;
    app.current = page.id;
    location.hash = '#/' + page.id;
    markNav(page.id);
    var view = document.getElementById('view');
    view.innerHTML = ui.loading();
    window.scrollTo(0, 0);
    // 页面 render 可能是异步的；统一 catch，避免一个页面报错把整个应用卡死
    Promise.resolve()
      .then(function () { return page.render(view); })
      .catch(function (e) {
        view.innerHTML = '<div class="card"><h3>页面出错了</h3>' +
          '<p class="muted mt-2">' + ui.esc(e.message || String(e)) + '</p></div>';
      });
  }
  app.go = go;

  function routeFromHash() {
    var m = /^#\/(\w+)/.exec(location.hash || '');
    return m ? m[1] : 'home';
  }

  /* ---------------- 降级提示 ---------------- */

  function loadBanner() {
    return api.get('/health').then(function (h) {
      var cfg = [];    /* 靠 .env 配置就能解决的 */
      var infra = [];  /* 服务本身的问题，配 .env 没用 */
      if (!h.llm_enabled) cfg.push('未配置大模型，回答将使用模板生成（质量下降）');
      if (!h.vision_enabled) cfg.push('未配置视觉模型，暂时无法识别照片');
      if (h.redis !== 'up') infra.push('缓存服务不可用');
      /* ★ 判断「图片存哪儿」必须看 storage（实际后端），不能看 minio。
         minio 只说明端口通不通 —— MinIO 进程活着但 S3 操作全被拒时
         （例如缺 license），后端会静默降级到本地磁盘，而 minio 仍是 'up'，
         只看它的话这条提示永远不会出现。 */
      if (h.storage === 'local') infra.push('对象存储不可用，图片改存本地磁盘');
      else if (h.minio !== 'up') infra.push('图片存储不可用');
      var msgs = cfg.concat(infra);
      var b = document.getElementById('banner');
      if (!msgs.length) { b.hidden = true; return; }
      /* ★ 两类问题会**同时出现**（实测就是：llm/vision 未配置 + 对象存储降级），
         所以不能二选一。早先写成 `cfg.length ? A : B`，结果
         「对象存储不可用」后面跟着「去 .env 配 Key」—— 驴唇不对马嘴。
         现在各给各的指引。 */
      var tails = [];
      if (cfg.length) {
        tails.push('配置类问题在 <code>.env</code> 里填好 API Key 后重启即可启用');
      }
      if (infra.length) {
        tails.push('服务类问题执行 <code>python scripts/infra.py status</code> 查看');
      }
      b.innerHTML = '<span>⚠️</span><span>' + ui.esc(msgs.join('；')) +
        '。' + tails.join('；') + '。</span>';
      b.hidden = false;
    }).catch(function () { /* 健康检查失败不阻塞使用 */ });
  }

  /* ---------------- 顶栏 ---------------- */

  function renderTop() {
    var me = store.user || {};
    document.getElementById('whoName').textContent =
      me.display_name || me.username || '未登录';
    document.getElementById('avatar').textContent =
      (me.display_name || me.username || '农').slice(0, 1);
    syncElderBtn();

    document.getElementById('elderBtn').addEventListener('click', function () {
      store.setElder(!store.elder);
      syncElderBtn();
      go(app.current);   // 重渲染当前页，让术语切换生效
    });

    document.getElementById('who').addEventListener('click', function () {
      ui.modal({
        title: '账号',
        html: '<div class="kv"><span class="k">用户名</span><span class="v">' +
              ui.esc(me.username || '') + '</span></div>' +
              '<div class="kv"><span class="k">称呼</span><span class="v">' +
              ui.esc(me.display_name || '—') + '</span></div>' +
              '<div class="kv"><span class="k">老人模式</span><span class="v">' +
              (store.elder ? '已开启' : '已关闭') + '</span></div>' +
              '<p class="small muted mt-3">老人模式会放大字号，并把「置信度」' +
              '这类术语换成人话。</p>',
        okText: '退出登录', cancelText: '关闭'
      }).then(function (ok) {
        if (!ok) return;
        store.setToken('');
        store.user = null;
        app.showGate('已退出登录');
      });
    });
  }

  function syncElderBtn() {
    document.getElementById('elderBtn').classList.toggle('primary', store.elder);
    document.getElementById('elderIcon').textContent = store.elder ? 'Aa+' : 'Aa';
  }

  /* ---------------- 启动 ---------------- */

  function boot() {
    renderNav();
    renderTop();
    loadBanner();
    go(routeFromHash());
  }

  app.boot = boot;

  function start() {
    initGate();
    window.addEventListener('hashchange', function () {
      if (app.current !== routeFromHash()) go(routeFromHash());
    });

    if (!store.isLoggedIn()) { app.showGate(); return; }

    api.get('/auth/me')
      .then(function (me) {
        store.user = me;
        if (localStorage.getItem('agri.elder') === null) {
          store.setElder(me.elder_mode !== false);
        }
        hideGate();
        boot();
      })
      .catch(function () {
        store.setToken('');
        app.showGate('登录已过期，请重新登录');
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

})(window.Agri);
