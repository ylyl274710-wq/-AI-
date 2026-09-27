/* ============================================================
   core.js —— 命名空间 / 配置 / 状态 / API / UI 工具
   ------------------------------------------------------------
   加载顺序第一，后面所有文件都依赖 window.Agri。

   ★ 为什么用全局命名空间 + classic script，而不是 ES module：
     农户端要能「双击 index.html 直接打开」演示，ES module 在 file://
     下会被 CORS 拦死。classic script 没有这个限制。
     代价是要自己管加载顺序（见 index.html 底部的 script 顺序）。
   ============================================================ */
(function (global) {
  'use strict';

  var Agri = global.Agri = {};

  /* ==========================================================
     1. 配置
     ========================================================== */

  var DEFAULT_PORT = 8001;

  function resolveBase() {
    // 后端端口固定 8001（8000 被另一个项目占着）。
    // file:// 打开时同源不成立，必须用绝对地址。
    var override = new URLSearchParams(location.search).get('api');
    if (override) return override.replace(/\/$/, '') + '/api/v1';

    if (location.protocol === 'file:') {
      return 'http://127.0.0.1:' + DEFAULT_PORT + '/api/v1';
    }
    if (location.port === String(DEFAULT_PORT)) {
      return location.origin + '/api/v1';   // 由后端托管，同源最省事
    }
    // 其他端口（比如用别的静态服务器打开）→ 打后端绝对地址，靠 CORS 放行
    return 'http://127.0.0.1:' + DEFAULT_PORT + '/api/v1';
  }

  Agri.cfg = {
    apiBase: resolveBase(),
    shotTypes: [
      { v: 'leaf_front', t: '叶片正面' },
      { v: 'leaf_back', t: '叶片背面' },
      { v: 'whole_plant', t: '整株' },
      { v: 'lesion_closeup', t: '病斑近照' },
      { v: 'unknown', t: '其他' }
    ],
    maxImages: 4,
    taskTypes: [
      { v: 'patrol', t: '巡田' },
      { v: 'fertilize', t: '施肥' },
      { v: 'irrigate', t: '浇水' },
      { v: 'spray', t: '打药' },
      { v: 'recheck', t: '复查' },
      { v: 'other', t: '其他' }
    ]
  };

  /* ==========================================================
     2. 状态
     ========================================================== */

  var LS_TOKEN = 'agri.token';
  var LS_ELDER = 'agri.elder';

  Agri.store = {
    token: localStorage.getItem(LS_TOKEN) || '',
    user: null,
    // 老人模式默认开：这是面向「不太会用智能手机的农户」的产品，
    // 默认值应该照顾目标用户，而不是照顾开发者自己看着舒服。
    elder: localStorage.getItem(LS_ELDER) !== '0',
    // 当前诊断话题的会话 id（内存级，不持久化——刷新页面开新话题是合理语义）。
    // ★ 连续提问共用同一个 session：列表不再每次问答多一条记录，
    //   后端的多轮记忆（按会话聚合历史）也才能在界面上真正生效。
    diagSessionId: '',
    _subs: [],

    on: function (fn) { this._subs.push(fn); },
    emit: function () { this._subs.forEach(function (f) { try { f(); } catch (e) {} }); },

    setDiagSession: function (sid) { this.diagSessionId = sid || ''; },
    newDiagTopic: function () { this.diagSessionId = ''; },
    setToken: function (t) {
      this.token = t || '';
      if (t) localStorage.setItem(LS_TOKEN, t);
      else localStorage.removeItem(LS_TOKEN);
    },
    setElder: function (on) {
      this.elder = !!on;
      localStorage.setItem(LS_ELDER, on ? '1' : '0');
      applyElder();
      this.emit();
    },
    isLoggedIn: function () { return !!this.token; }
  };

  function applyElder() {
    document.body.classList.toggle('elder', Agri.store.elder);
  }
  Agri.applyElder = applyElder;

  /* ==========================================================
     3. API
     ========================================================== */

  function ApiError(status, body) {
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    this.message = (body && (body.detail || body.message)) || ('请求失败 (' + status + ')');
    // FastAPI 校验错误是数组，展开成一句人话
    if (body && Array.isArray(body.detail)) {
      this.message = body.detail.map(function (d) {
        return (d.loc || []).slice(1).join('.') + ' ' + (d.msg || '');
      }).join('；');
    }
  }
  ApiError.prototype = Object.create(Error.prototype);

  function qs(params) {
    if (!params) return '';
    var p = new URLSearchParams();
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === '') return;
      p.append(k, v);
    });
    var s = p.toString();
    return s ? '?' + s : '';
  }

  Agri.api = {
    ApiError: ApiError,
    qs: qs,

    /** 普通 JSON 请求。204 返回 null。 */
    req: function (method, path, opts) {
      opts = opts || {};
      var headers = {};
      var body;
      if (opts.body !== undefined) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(opts.body);
      }
      if (opts.form) {
        body = new FormData();
        Object.keys(opts.form).forEach(function (k) {
          var v = opts.form[k];
          if (v === undefined || v === null) return;
          if (Array.isArray(v)) v.forEach(function (x) { body.append(k, x); });
          else body.append(k, v);
        });
        (opts.files || []).forEach(function (f) { body.append('files', f); });
      }
      var t = Agri.store.token;
      if (t) headers['Authorization'] = 'Bearer ' + t;

      return fetch(Agri.cfg.apiBase + path, { method: method, headers: headers, body: body })
        .then(function (res) {
          if (res.status === 204) return null;
          return res.text().then(function (txt) {
            var data = null;
            try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = txt; }
            if (!res.ok) {
              if (res.status === 401 && Agri.store.token) {
                // token 过期/失效：清掉并回登录，否则用户会卡在一个
                // 每个操作都报 401 的界面里，完全不知道发生了什么
                Agri.store.setToken('');
                Agri.store.user = null;
                if (Agri.app && Agri.app.showGate) Agri.app.showGate('登录已过期，请重新登录');
              }
              throw new ApiError(res.status, data);
            }
            return data;
          });
        });
    },

    get: function (p, params) { return this.req('GET', p + qs(params)); },
    post: function (p, body) { return this.req('POST', p, { body: body }); },
    patch: function (p, body) { return this.req('PATCH', p, { body: body }); },
    del: function (p) { return this.req('DELETE', p); },

    /**
     * SSE 流式诊断。
     *
     * ★ 用 fetch + ReadableStream 手工解析，不用 EventSource ——
     *   EventSource 只支持 GET，而诊断要 POST（带图片和表单），
     *   且没法带 Authorization 头。手工解析不复杂，还能顺便处理
     *   服务端分块边界（一个 chunk 里可能是半条事件）。
     */
    streamDiagnose: function (form, files, onEvent) {
      var body = new FormData();
      Object.keys(form).forEach(function (k) {
        if (form[k] !== undefined && form[k] !== null) body.append(k, form[k]);
      });
      (files || []).forEach(function (f) { body.append('files', f); });

      var headers = {};
      if (Agri.store.token) headers['Authorization'] = 'Bearer ' + Agri.store.token;

      return fetch(Agri.cfg.apiBase + '/diagnosis/stream', {
        method: 'POST', headers: headers, body: body
      }).then(function (res) {
        if (!res.ok) {
          return res.text().then(function (t) {
            var d = null; try { d = JSON.parse(t); } catch (e) {}
            throw new ApiError(res.status, d);
          });
        }
        var reader = res.body.getReader();
        var decoder = new TextDecoder('utf-8');
        var buf = '';

        function pump() {
          return reader.read().then(function (r) {
            if (r.done) { flush(true); return; }
            buf += decoder.decode(r.value, { stream: true });
            flush(false);
            return pump();
          });
        }
        function flush(end) {
          // SSE 以空行分隔事件；最后可能剩半条，留着等下一个 chunk
          var blocks = buf.split('\n\n');
          if (!end) buf = blocks.pop();
          else buf = '';
          blocks.forEach(function (blk) {
            var ev = 'message', data = null;
            blk.split('\n').forEach(function (line) {
              if (line.indexOf('event:') === 0) ev = line.slice(6).trim();
              else if (line.indexOf('data:') === 0) {
                try { data = JSON.parse(line.slice(5).trim()); }
                catch (e) { data = null; }
              }
            });
            if (data !== null) onEvent(ev, data);
          });
        }
        return pump();
      });
    }
  };

  /* ==========================================================
     4. UI 工具
     ========================================================== */

  function esc(s) {
    if (s === undefined || s === null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(msg, kind) {
    var wrap = document.getElementById('toasts');
    if (!wrap) return;
    var d = document.createElement('div');
    d.className = 'toast' + (kind ? ' ' + kind : '');
    d.textContent = msg;
    wrap.appendChild(d);
    setTimeout(function () {
      d.style.transition = 'opacity .25s';
      d.style.opacity = '0';
      setTimeout(function () { d.remove(); }, 260);
    }, kind === 'err' ? 4200 : 2600);
  }

  function fmtDate(s) {
    if (!s) return '—';
    var d = new Date(s);
    if (isNaN(d)) return String(s);
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
  }

  function fmtTime(s) {
    if (!s) return '—';
    var d = new Date(s);
    if (isNaN(d)) return String(s);
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
           p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function relTime(s) {
    if (!s) return '';
    var d = new Date(s);
    if (isNaN(d)) return '';
    var diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
    return fmtDate(s);
  }

  /** 术语表：老人模式换成人话。
   *  ★ 不是所有词都要换 —— 只换「农户看不懂会直接影响判断」的那些。
   *    「叶片」改成「叶子」有意义，「地块」改成「地」有意义；
   *    把「农药」改成别的反而会造成歧义。 */
  var TERMS = {
    confidence: { n: '置信度', e: '把握程度' },
    margin: { n: '分差', e: '和其他答案拉开多少' },
    agreement: { n: '一致性', e: '几张照片看法一致吗' },
    coverage: { n: '覆盖度', e: '资料查得全不全' },
    retrieve: { n: '查资料', e: '查资料' },
    ruleCheck: { n: '核对农药登记', e: '查这个药能不能用' },
    intent: { n: '意图', e: '你在问什么' },
    trace: { n: '执行轨迹', e: '系统做了哪些事' },
    confidenceTitle: { n: '置信度', e: '我对这个判断有多大把握' },
    citation: { n: '引用来源', e: '资料是从哪来的' },
    degraded: { n: '降级模式', e: '部分功能未开启' }
  };

  function T(key) {
    var t = TERMS[key];
    if (!t) return key;
    return Agri.store.elder ? t.e : t.n;
  }

  /** 置信度可视化：分数 + 三个分量 + 阈值刻度 */
  function confBlock(conf) {
    if (!conf) return '';
    var pct = Math.round((conf.score || 0) * 100);
    var thr = 55;
    var cls = conf.passed ? '' : (conf.rejected ? 'danger' : 'warn');
    var verdict = conf.rejected ? '无法判断'
                : conf.passed ? '可以给结论'
                : '把握不足，建议补信息';
    return '' +
      '<div class="conf">' +
        '<div class="row between">' +
          '<span class="small muted">' + esc(T('confidenceTitle')) + '</span>' +
          '<span class="strong">' + pct + '%　<span class="small muted">' + esc(verdict) + '</span></span>' +
        '</div>' +
        '<div class="conf-bar">' +
          '<div class="conf-fill ' + cls + '" style="width:' + pct + '%"></div>' +
          '<div class="conf-thresh" style="left:' + thr + '%" title="判定线 55%"></div>' +
        '</div>' +
        '<div class="conf-parts">' +
          part('margin', conf.margin) +
          part('agreement', conf.agreement) +
          part('coverage', conf.coverage) +
        '</div>' +
        (conf.reasons && conf.reasons.length
          ? '<ul class="small muted" style="margin:0;padding-left:18px">' +
            conf.reasons.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') +
            '</ul>'
          : '') +
      '</div>';

    function part(k, v) {
      return '<div class="conf-part">' +
        '<div class="n">' + Math.round((v || 0) * 100) + '%</div>' +
        '<div class="t">' + esc(T(k)) + '</div></div>';
    }
  }

  var NODE_LABEL = {
    intent: '理解问题', context: '读取农田档案', diagnose: '看图识别',
    retrieve: '查资料', rule_check: '核对农药登记', compose: '组织回答',
    persist: '存档', remind: '安排提醒'
  };
  function nodeLabel(n) {
    if (!n) return '';
    return NODE_LABEL[n] || n;
  }

  /** 执行轨迹（诊断结果页 + 流式进度共用） */
  function traceBlock(steps, current) {
    if ((!steps || !steps.length) && !current) return '';
    var out = '<div class="trace">';
    (steps || []).forEach(function (s, i) {
      var cls = s.status === 'error' ? 'error'
              : s.status === 'skipped' ? '' : 'done';
      var title = s.detail ? esc(s.detail) : '';
      var ms = s.elapsed_ms ? ' ' + s.elapsed_ms + 'ms' : '';
      out += '<span class="trace-step ' + cls + '" title="' + title + '">' +
             esc(nodeLabel(s.node)) + ms + '</span>';
      if (i < steps.length - 1 || current) out += '<span class="trace-arrow">›</span>';
    });
    if (current) {
      out += '<span class="trace-step running">' + esc(nodeLabel(current)) + ' …</span>';
    }
    out += '</div>';
    return out;
  }

  /** 回答分节渲染。【发现什么】/【现在做什么】/【什么时候再看】 */
  function answerBlock(answer, sections) {
    if (sections && Object.keys(sections).length) {
      return Object.keys(sections).map(function (k) {
        return '<div class="answer-sec"><div class="t">' + esc(k) + '</div>' +
               '<div class="b">' + esc(sections[k]) + '</div></div>';
      }).join('');
    }
    if (!answer) return '<p class="muted">（没有回答内容）</p>';
    return '<div class="b" style="white-space:pre-wrap">' + esc(answer) + '</div>';
  }

  function riskBadge(level) {
    var map = { high: '高风险', medium: '要留意', low: '风险低' };
    return '<span class="risk-lvl ' + esc(level) + '">' + esc(map[level] || level) + '</span>';
  }

  /**
   * 通用模态框。resolve({ ok, values })。
   *
   * ★ values 必须在移除 DOM **之前**收集。
   *   之前的写法是 `close()` 里先 mask.remove() 再 resolve，调用方在
   *   `.then` 里用 `document.getElementById('xxx').value` 读输入框 ——
   *   那时元素已经不在文档里了，永远读到 undefined，表现为「用户填了
   *   反馈，提交上去却是空的」。这类 bug 不报错、只是数据悄悄丢失。
   *   约定：模态框里的输入控件一律加 name 属性，值通过 values 取。
   */
  function modal(opts) {
    return new Promise(function (resolve) {
      var mask = document.createElement('div');
      mask.className = 'modal-mask';
      mask.innerHTML =
        '<div class="modal">' +
          '<h3>' + esc(opts.title || '') + '</h3>' +
          '<div class="col">' + (opts.html || '') + '</div>' +
          '<div class="modal-foot">' +
            (opts.cancel === false ? '' : '<button class="btn" data-x="0">' + esc(opts.cancelText || '取消') + '</button>') +
            '<button class="btn primary" data-x="1">' + esc(opts.okText || '确定') + '</button>' +
          '</div>' +
        '</div>';

      function collect() {
        var values = {};
        [].forEach.call(mask.querySelectorAll('[name]'), function (el) {
          values[el.name] = (el.type === 'checkbox' || el.type === 'radio')
            ? el.checked : el.value;
        });
        return values;
      }
      function close(ok) {
        var values = collect();   // 先取值，再拆 DOM
        mask.remove();
        resolve({ ok: ok, values: values });
      }

      mask.addEventListener('click', function (e) {
        var x = e.target.getAttribute && e.target.getAttribute('data-x');
        if (x === '1') close(true);
        else if (x === '0') close(false);
        else if (e.target === mask) close(false);
      });
      document.body.appendChild(mask);
      var inp = mask.querySelector('input,textarea,select');
      if (inp) inp.focus();
    });
  }

  function empty(msg, icon) {
    return '<div class="empty"><span class="big">' + (icon || '🌱') + '</span>' + esc(msg) + '</div>';
  }

  function loading(msg) {
    return '<div class="empty"><span class="big">⏳</span>' + esc(msg || '加载中…') + '</div>';
  }

  Agri.ui = {
    esc: esc, toast: toast, T: T,
    fmtDate: fmtDate, fmtTime: fmtTime, relTime: relTime,
    confBlock: confBlock, traceBlock: traceBlock, answerBlock: answerBlock,
    riskBadge: riskBadge, modal: modal, empty: empty, loading: loading,
    nodeLabel: nodeLabel
  };

  /* ==========================================================
     5. 页面注册表（各 page 文件往这里塞自己）
     ========================================================== */
  Agri.pages = {};

  applyElder();

})(window);
