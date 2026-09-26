/* ============================================================
   pages/home.js —— 首页总览
   ------------------------------------------------------------
   ★ 首页的信息优先级是刻意排的：
     天气风险 > 当前作物 > 今天该做的事 > 最近诊断。
     农户打开就想知道「今天我这地有没有要注意的」，
     而不是「系统里一共有多少条数据」。
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, api = Agri.api, esc = ui.esc;

  var STATUS_TEXT = {
    succeeded: '已给出结论',
    needs_more_info: '信息不足',
    rejected: '没认出来',
    failed: '出错了',
    pending: '处理中'
  };

  function greet() {
    var h = new Date().getHours();
    if (h < 6) return '这么早';
    if (h < 11) return '早上好';
    if (h < 14) return '中午好';
    if (h < 18) return '下午好';
    return '晚上好';
  }

  Agri.pages.home = {
    id: 'home',
    label: '首页',
    icon: '🏠',

    render: function (el) {
      return api.get('/farm/overview').then(function (ov) {
        var me = Agri.store.user || {};
        var name = me.display_name || me.username || '';

        el.innerHTML =
          '<div class="page-head">' +
            '<div>' +
              '<h1>' + esc(greet()) + (name ? '，' + esc(name) : '') + '</h1>' +
              '<div class="sub">' + esc(ui.fmtDate(new Date())) +
                '　·　今天地里的情况都在下面</div>' +
            '</div>' +
            '<button class="btn primary lg" id="goDiag">拍照片看看</button>' +
          '</div>' +

          '<div class="grid two">' +
            weatherCard(ov.weather) +
            riskCard(ov.risks) +
          '</div>' +

          '<div class="grid two mt-4">' +
            cropCard(ov.current_crops) +
            taskCard(ov.pending_tasks) +
          '</div>' +

          '<div class="mt-4">' + recentCard(ov.recent_diagnoses) + '</div>';

        el.querySelector('#goDiag').addEventListener('click', function () {
          Agri.app.go('diagnose');
        });
        // 空状态里的引导按钮只在对应卡片为空时存在，所以要判空再绑
        var gf = el.querySelector('#goFarm');
        if (gf) gf.addEventListener('click', function () { Agri.app.go('farm'); });
        var gc = el.querySelector('#goChat');
        if (gc) gc.addEventListener('click', function () { Agri.app.go('chat'); });
        bindTasks(el);
      });
    }
  };

  /* ---------------- 天气 ---------------- */

  function weatherCard(w) {
    if (!w) {
      return '<div class="card"><div class="card-head"><h3>天气</h3></div>' +
             ui.empty('还没有天气数据。添加一块地之后就会自动获取。', '🌤️') + '</div>';
    }
    return '' +
      '<div class="card">' +
        '<div class="card-head">' +
          '<h3>最近三天天气</h3>' +
          (w.is_mock ? '<span class="pill warn"><span class="dot"></span>模拟数据</span>'
                     : '<span class="pill ok"><span class="dot"></span>实时</span>') +
        '</div>' +
        '<div class="card-body">' +
          '<div class="grid three" style="gap:12px">' +
            stat(num(w.temp_avg, '℃'), '平均气温') +
            stat(num(w.humidity_avg, '%'), '平均湿度') +
            stat(num(w.rainfall_hours, '小时'), '累计降雨') +
          '</div>' +
          '<div class="row wrap mt-3" style="gap:8px">' +
            '<span class="badge">最高 ' + num(w.temp_max, '℃') + '</span>' +
            '<span class="badge">最低 ' + num(w.temp_min, '℃') + '</span>' +
            '<span class="badge">湿度峰值 ' + num(w.humidity_max, '%') + '</span>' +
            '<span class="badge">风速 ' + num(w.wind_level, ' m/s') + '</span>' +
          '</div>' +
          (w.caveat
            ? '<p class="small" style="color:var(--c-warn);margin-top:12px">⚠️ ' +
              esc(w.caveat) + '</p>'
            : '') +
        '</div>' +
      '</div>';

    function stat(v, l) {
      return '<div class="stat"><span class="num">' + esc(v) + '</span>' +
             '<span class="lbl">' + esc(l) + '</span></div>';
    }
  }

  function num(v, unit) {
    if (v === null || v === undefined) return '—';
    return (Math.round(v * 10) / 10) + (unit || '');
  }

  /* ---------------- 风险 ---------------- */

  function riskCard(risks) {
    var body;
    if (!risks || !risks.length) {
      body = ui.empty('当前天气条件下，暂时没有需要特别留意的病虫害。', '✅');
    } else {
      body = '<div class="card-body">' +
        risks.map(function (r) {
          return '<div class="risk-row">' +
            ui.riskBadge(r.risk_level) +
            '<div class="grow">' +
              '<div class="strong">' + esc(r.disease_name) + '</div>' +
              '<div class="risk-why">' + esc(r.advice) + '</div>' +
            '</div>' +
            '<span class="badge">' + Math.round((r.score || 0) * 100) + '%</span>' +
          '</div>';
        }).join('') +
        '</div>';
    }
    return '<div class="card pad-0">' +
      '<div class="card-head"><h3>天气相关的病害风险</h3>' +
        '<span class="tiny faint">只是提醒留意，不是诊断结论</span></div>' +
      body + '</div>';
  }

  /* ---------------- 当前作物 ---------------- */

  function cropCard(list) {
    var body;
    if (!list || !list.length) {
      body = ui.empty('还没有登记种了什么。登记之后，系统才知道该按什么作物帮你判断。', '🌱') +
        '<div class="center" style="padding-bottom:16px">' +
        '<button class="btn primary" id="goFarm">去登记我的地</button></div>';
    } else {
      body = '<div class="list">' + list.map(function (c) {
        var d = c.days_to_harvest;
        var tip = (d === null || d === undefined) ? ''
                : d > 0 ? '距预计收获 ' + d + ' 天'
                : d === 0 ? '今天预计收获'
                : '已过预计收获期 ' + Math.abs(d) + ' 天';
        return '<div class="item">' +
          '<div class="main-col">' +
            '<span class="title">' + esc(c.crop_name || '未填作物') +
              (c.variety ? ' · ' + esc(c.variety) : '') + '</span>' +
            '<span class="sub">' + esc(c.field_name || '') +
              (c.stage ? '　·　' + esc(c.stage) : '') + '</span>' +
          '</div>' +
          (tip ? '<div class="side"><span class="badge">' + esc(tip) + '</span></div>' : '') +
        '</div>';
      }).join('') + '</div>';
    }
    var card = '<div class="card pad-0">' +
      '<div class="card-head"><h3>现在种的是什么</h3></div>' + body + '</div>';
    return card;
  }

  /* ---------------- 待办任务 ---------------- */

  function taskCard(tasks) {
    var body;
    if (!tasks || !tasks.length) {
      body = ui.empty('暂时没有待办的事。诊断之后系统会自动帮你排复查。', '📋');
    } else {
      body = '<div class="list">' + tasks.map(function (t) {
        var overdue = t.due_date && new Date(t.due_date) < new Date(new Date().toDateString());
        return '<div class="item">' +
          '<input type="checkbox" data-task="' + t.id + '" style="margin-top:5px" />' +
          '<div class="main-col">' +
            '<span class="title">' + esc(t.title) + '</span>' +
            '<span class="sub">' +
              (t.due_date ? '应做日期 ' + esc(ui.fmtDate(t.due_date)) : '未定日期') +
              (t.detail ? '　·　' + esc(t.detail) : '') +
            '</span>' +
          '</div>' +
          (overdue ? '<div class="side"><span class="pill danger">已过期</span></div>' : '') +
        '</div>';
      }).join('') + '</div>';
    }
    return '<div class="card pad-0">' +
      '<div class="card-head"><h3>这几天要做的事</h3>' +
        '<span class="tiny faint">打勾表示做完了</span></div>' + body + '</div>';
  }

  function bindTasks(el) {
    el.addEventListener('change', function (e) {
      var cb = e.target.closest('[data-task]');
      if (!cb) return;
      var id = cb.getAttribute('data-task');
      cb.disabled = true;
      ui.modal({
        title: '做完了',
        html: '<p class="muted small">可以顺手记一句做得怎么样，' +
              '这对判断系统给的建议有没有用很重要（不填也行）。</p>' +
              '<label class="field mt-3"><span>一句话反馈</span>' +
              '<input name="fbText" placeholder="例如：去看了，确实好转了" /></label>',
        okText: '确认完成', cancelText: '还没做'
      }).then(function (r) {
        if (!r.ok) { cb.checked = false; cb.disabled = false; return; }
        var note = r.values.fbText || '';
        api.post('/tasks/' + id + '/done' + api.qs({ feedback: note }))
          .then(function () {
            ui.toast('记下了', 'ok');
            Agri.app.go('home');
          })
          .catch(function (err) {
            ui.toast(err.message, 'err');
            cb.checked = false;
            cb.disabled = false;
          });
      });
    });
  }

  /* ---------------- 最近诊断 ---------------- */

  function recentCard(list) {
    if (!list || !list.length) {
      return '<div class="card"><div class="card-head"><h3>最近的诊断</h3></div>' +
        ui.empty('还没有诊断记录。拍张照片试试吧。', '📷') + '</div>';
    }
    return '<div class="card pad-0">' +
      '<div class="card-head"><h3>最近的诊断</h3>' +
        '<button class="btn sm ghost" id="goChat">看全部 ›</button></div>' +
      '<div class="list">' + list.map(function (d) {
        var pct = d.confidence === null || d.confidence === undefined
          ? '' : Math.round(d.confidence * 100) + '%';
        return '<div class="item">' +
          '<div class="main-col">' +
            '<span class="title">' + esc(d.top1 || '未能判断') + '</span>' +
            '<span class="sub">' + esc(d.input_text || '（只有图片）') + '</span>' +
          '</div>' +
          '<div class="side">' +
            '<span class="badge">' + esc(STATUS_TEXT[d.status] || d.status) + '</span>' +
            (pct ? '<span class="tiny faint">把握 ' + pct + '</span>' : '') +
            '<span class="tiny faint">' + esc(ui.relTime(d.created_at)) + '</span>' +
          '</div>' +
        '</div>';
      }).join('') + '</div></div>';
  }

})(window.Agri);
