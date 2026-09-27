/* ============================================================
   pages/chat.js —— 对话历史
   ------------------------------------------------------------
   ★ 这里展示的是「对话」，但真正的价值在于每条回答都能展开
     看当时的检索引用与执行轨迹（难点 13：结果可验证）。
     农户只关心问答本身，农技员/开发者才会展开轨迹 ——
     所以默认折叠，需要时才展开。
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, api = Agri.api, esc = ui.esc;

  var st = { sessions: [], current: '', msgs: [] };

  Agri.pages.chat = {
    id: 'chat',
    label: '对话',
    icon: '💬',

    render: function (el) {
      return api.get('/chat/sessions').then(function (list) {
        st.sessions = list || [];
        if (!st.sessions.length) {
          el.innerHTML = '<div class="page-head"><div><h1>对话记录</h1>' +
            '<div class="sub">同一话题的连续追问会归入同一个会话</div></div></div>' +
            '<div class="card">' +
            ui.empty('还没有对话。去拍张照片问一次吧。', '💬') +
            '<div class="center" style="padding-bottom:16px">' +
            '<button class="btn primary" id="goDiag">开始一次诊断</button></div></div>';
          el.querySelector('#goDiag').addEventListener('click', function () {
            Agri.store.newDiagTopic();
            Agri.app.go('diagnose');
          });
          return;
        }
        // 默认打开最近一次会话
        st.current = st.sessions[0].session_id;
        paint(el);
      });
    }
  };

  function paint(el) {
    el.innerHTML =
      '<div class="page-head">' +
        '<div><h1>对话记录</h1>' +
        '<div class="sub">共 ' + st.sessions.length + ' 个会话</div></div>' +
        '<button class="btn primary" id="newDiag">开新话题</button>' +
      '</div>' +
      '<div class="grid" style="grid-template-columns:280px minmax(0,1fr);gap:16px" id="chatGrid">' +
        '<div class="card pad-0" id="sessList"></div>' +
        '<div class="card" id="msgBox">' + ui.loading() + '</div>' +
      '</div>';

    el.querySelector('#newDiag').addEventListener('click', function () {
      Agri.store.newDiagTopic();
      Agri.app.go('diagnose');
    });
    paintSessions(el);
    loadMsgs(el, st.current);

    // 窄屏改成上下堆叠，否则 280px 的侧栏会把消息区挤没
    if (window.innerWidth < 760) {
      el.querySelector('#chatGrid').style.gridTemplateColumns = '1fr';
    }
  }

  function dayLabel(iso) {
    var d = (iso || '').slice(0, 10);
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    var now = new Date();
    var today = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    var y = new Date(now.getTime() - 86400000);
    var yest = y.getFullYear() + '-' + pad(y.getMonth() + 1) + '-' + pad(y.getDate());
    if (d === today) return '今天';
    if (d === yest) return '昨天';
    return (d.slice(5) || '').replace('-', '月') + '日';
  }

  function paintSessions(el) {
    // 按日期分组 + 单行省略：会话多的时候一眼能扫到目标，
    // 之前「每次问答一条大卡片」的堆法被用户点名批评过
    var html = '<div class="card-head"><h3>会话</h3></div>';
    var lastLabel = '';
    st.sessions.forEach(function (s) {
      var label = dayLabel(s.last_at);
      if (label !== lastLabel) {
        html += '<div class="tiny faint" style="padding:6px 12px 2px;' +
          'border-top:1px solid var(--c-border);margin-top:2px">' + esc(label) + '</div>';
        lastLabel = label;
      }
      html += '<div class="item clickable" data-sid="' +
        esc(s.session_id) + '"' +
        (s.session_id === st.current ? ' style="background:var(--c-primary-soft)"' : '') +
        '>' +
        '<div class="main-col">' +
          '<span class="title" style="font-weight:500;font-size:var(--fs-sm);' +
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block">' +
            esc(s.last_question || '（无提问）') + '</span>' +
          '<span class="sub tiny">' + esc(ui.relTime(s.last_at)) +
            '　·　' + s.message_count + ' 条</span>' +
        '</div>' +
      '</div>';
    });
    el.querySelector('#sessList').innerHTML = html + '</div>';

    el.querySelector('#sessList').onclick = function (e) {
      var it = e.target.closest('[data-sid]');
      if (!it) return;
      st.current = it.getAttribute('data-sid');
      paintSessions(el);
      loadMsgs(el, st.current);
    };
  }

  function loadMsgs(el, sid) {
    var box = el.querySelector('#msgBox');
    box.innerHTML = ui.loading();
    api.get('/chat/sessions/' + encodeURIComponent(sid)).then(function (msgs) {
      st.msgs = msgs || [];
      if (!st.msgs.length) { box.innerHTML = ui.empty('这次会话没有内容'); return; }
      box.innerHTML =
        '<div class="card-head"><h3>' + esc(ui.fmtTime(st.msgs[0].created_at)) +
          ' 的对话</h3>' +
          '<div class="row" style="gap:8px">' +
            '<button class="btn sm" id="contSess">继续这个话题</button>' +
            '<button class="btn sm danger" id="delSess">删除</button>' +
          '</div></div>' +
        '<div class="card-body chat-wrap">' +
          st.msgs.map(function (m) { return msgHtml(m); }).join('') +
        '</div>';

      box.querySelector('#contSess').addEventListener('click', function () {
        // 续聊旧话题：把当前会话设为诊断页的活动话题，跳过去直接追问，
        // 多轮记忆会带上这个会话的全部历史
        Agri.store.setDiagSession(sid);
        ui.toast('已切换到这个话题，去继续追问', 'ok');
        Agri.app.go('diagnose');
      });

      box.querySelector('#delSess').addEventListener('click', function () {
        ui.modal({
          title: '删除这次对话',
          html: '<p>删掉之后看不到了。诊断结论本身还会保留在诊断记录里，' +
                '不会影响用药历史。</p>',
          okText: '确认删除'
        }).then(function (r) {
          if (!r.ok) return;
          api.del('/chat/sessions/' + encodeURIComponent(sid)).then(function () {
            ui.toast('已删除', 'ok');
            Agri.app.go('chat');
          }).catch(function (e) { ui.toast(e.message, 'err'); });
        });
      });

      // 展开轨迹
      box.addEventListener('click', function (e) {
        var t = e.target.closest('[data-toggle]');
        if (!t) return;
        var target = box.querySelector('#' + t.getAttribute('data-toggle'));
        if (target) target.hidden = !target.hidden;
      });
    }).catch(function (e) {
      box.innerHTML = ui.empty(e.message);
    });
  }

  function msgHtml(m) {
    var isUser = m.role === 'user';
    var body = '<div class="bubble ' + (isUser ? 'me' : 'ai') + '">' +
      esc(m.content || '') + '</div>';
    if (isUser) return '<div class="col">' + body + '</div>';

    var id = 'd' + m.id;
    var extra = '';
    var hasExtra = (m.trace && m.trace.length) ||
                   (m.tool_calls && m.tool_calls.length) ||
                   (m.references && m.references.length);
    if (hasExtra) {
      extra =
        '<div class="small"><button class="btn sm ghost" data-toggle="' + id + '">' +
          '看系统做了什么 ▾</button></div>' +
        '<div class="col" id="' + id + '" hidden style="gap:8px">' +
          (m.trace && m.trace.length
            ? '<div>' + ui.traceBlock(m.trace) + '</div>' : '') +
          (m.tool_calls && m.tool_calls.length
            ? '<div class="tbl-wrap"><table class="tbl"><thead><tr>' +
              '<th>步骤</th><th>工具</th><th>结果</th><th>耗时</th></tr></thead><tbody>' +
              m.tool_calls.map(function (c) {
                return '<tr><td>' + esc(ui.nodeLabel(c.node)) + '</td>' +
                  '<td><code>' + esc(c.tool) + '</code></td>' +
                  '<td>' + (c.ok ? '成功' : esc(c.error || '失败')) + '</td>' +
                  '<td>' + (c.elapsed_ms || 0) + ' ms</td></tr>';
              }).join('') + '</tbody></table></div>' : '') +
          (m.references && m.references.length
            ? '<div class="small muted">资料来自：' +
              esc(m.references.map(function (r) {
                return r.title || r.source_name || '';
              }).join('；')) + '</div>' : '') +
        '</div>';
    }
    var tags = [];
    if (m.intent) tags.push(intentLabel(m.intent));
    if (m.degraded) tags.push('降级模式');
    var tagHtml = tags.length
      ? '<div class="row" style="gap:6px">' + tags.map(function (t) {
          return '<span class="pill">' + esc(t) + '</span>';
        }).join('') + '</div>'
      : '';

    return '<div class="col">' + body + tagHtml + extra + '</div>';
  }

  function intentLabel(i) {
    return { diagnosis: '病虫害诊断', consult: '农业咨询', pesticide: '用药查询',
             farm_analysis: '农情分析', task: '农事提醒' }[i] || i;
  }

})(window.Agri);
