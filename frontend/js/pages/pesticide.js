/* ============================================================
   pages/pesticide.js —— 用药记录 / 农药查询 / 合规校验
   ------------------------------------------------------------
   ★ 这个页面有一个不显眼但很重要的功能：**抗性自查**。
     文档难点 8 说「用药记录容易遗漏」，而遗漏的真正后果不是
     「记录不全」，是**同一作用机理被连着用，病菌产生抗药性**。
     所以这里主动把「最近 30 天同一作用机理用了几次」算出来摆在
     最上面 —— 农户不需要理解什么是抗性，他只需要看到「这个已经
     用了 2 次，建议换个机理」。
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, api = Agri.api, esc = ui.esc;

  var st = { usage: [], fields: [], tab: 'usage' };
  var REPEAT_LIMIT = 2;      // 与后端 settings.resistance_repeat_limit 保持一致
  var WINDOW_DAYS = 30;      // 与 rules/pesticide_rules._recent_usage 默认值一致

  Agri.pages.pesticide = {
    id: 'pesticide',
    label: '用药',
    icon: '🧪',

    render: function (el) {
      return Promise.all([
        api.get('/pesticide/usage'),
        api.get('/farm/farms')
      ]).then(function (r) {
        st.usage = r[0] || [];
        st.fields = [];
        (r[1] || []).forEach(function (farm) {
          (farm.fields || []).forEach(function (fd) {
            st.fields.push({ id: fd.id, name: farm.name + ' · ' + fd.name });
          });
        });
        paint(el);
      });
    }
  };

  function paint(el) {
    el.innerHTML =
      '<div class="page-head">' +
        '<div><h1>用药记录</h1>' +
        '<div class="sub">记下来有两个用处：避免同一个药连着用，以及算安全间隔期</div></div>' +
        '<div class="row" style="gap:8px">' +
          '<button class="btn" id="tabCheck">查药能不能用</button>' +
          '<button class="btn primary" id="addUsage">+ 补记一次用药</button>' +
        '</div>' +
      '</div>' +
      resistanceCard() +
      '<div class="card pad-0 mt-4">' +
        '<div class="card-head"><h3>用药记录</h3>' +
          '<span class="tiny faint">共 ' + st.usage.length + ' 条</span></div>' +
        (st.usage.length
          ? '<div class="list">' + st.usage.map(row).join('') + '</div>'
          : ui.empty('还没有用药记录。补记一次之后，系统就能提醒你换药了。', '🧪')) +
      '</div>';

    el.querySelector('#addUsage').addEventListener('click', function () {
      usageForm(el, null);
    });
    el.querySelector('#tabCheck').addEventListener('click', function () {
      checkForm(el);
    });
    el.addEventListener('click', function (e) {
      var b = e.target.closest('[data-del]');
      if (!b) return;
      var id = parseInt(b.getAttribute('data-del'), 10);
      ui.modal({
        title: '删除这条记录',
        html: '<p>这条记录会<strong>彻底删掉</strong>，不是隐藏。</p>' +
              '<p class="small muted mt-2">这样做的原因：记错的一条如果只隐藏，' +
              '还会继续参与「这个药是不是连着用了」的计算，' +
              '系统会一直提醒你换药，反而添乱。</p>',
        okText: '确认删除'
      }).then(function (r) {
        if (!r.ok) return;
        api.del('/pesticide/usage/' + id).then(function () {
          ui.toast('已删除', 'ok');
          Agri.app.go('pesticide');
        }).catch(function (e2) { ui.toast(e2.message, 'err'); });
      });
    });
  }

  /* ---------------- 抗性自查 ---------------- */

  function resistanceCard() {
    var since = Date.now() - WINDOW_DAYS * 86400000;
    var counts = {};
    st.usage.forEach(function (u) {
      if (!u.resistance_group) return;
      var t = u.used_at ? new Date(u.used_at).getTime() : null;
      if (t !== null && t < since) return;
      counts[u.resistance_group] = (counts[u.resistance_group] || 0) + 1;
    });
    var keys = Object.keys(counts);
    if (!keys.length) {
      return '<div class="card mt-4"><div class="card-body small muted">' +
        '最近 ' + WINDOW_DAYS + ' 天没有登记作用机理的用药记录。' +
        '补记时填上有效成分，系统就能帮你盯着抗性风险。</div></div>';
    }
    var risky = keys.filter(function (k) { return counts[k] >= REPEAT_LIMIT; });
    return '<div class="card mt-4">' +
      '<div class="card-head"><h3>最近 ' + WINDOW_DAYS + ' 天的抗性自查</h3>' +
        (risky.length
          ? '<span class="pill danger"><span class="dot"></span>有风险</span>'
          : '<span class="pill ok"><span class="dot"></span>正常</span>') +
      '</div>' +
      '<div class="card-body">' +
        '<div class="row wrap" style="gap:8px">' +
        keys.map(function (k) {
          var bad = counts[k] >= REPEAT_LIMIT;
          return '<span class="pill ' + (bad ? 'danger' : '') + '">' +
            '作用机理 ' + esc(k) + '：用了 ' + counts[k] + ' 次</span>';
        }).join('') + '</div>' +
        (risky.length
          ? '<p class="mt-3" style="color:var(--c-danger)">⚠ 作用机理 ' +
            esc(risky.join('、')) + ' 已经用了 ' + REPEAT_LIMIT +
            ' 次以上。同一个机理连着用，病菌容易产生抗药性 —— ' +
            '建议换成作用机理不同的药（标签上会写「作用机理」或英文分组号）。</p>'
          : '<p class="small muted mt-3">同一作用机理暂时没有连着用，继续保持。</p>') +
      '</div></div>';
  }

  /* ---------------- 记录行 ---------------- */

  function row(u) {
    var field = st.fields.filter(function (f) { return f.id === u.field_id; })[0];
    return '<div class="item">' +
      '<div class="main-col">' +
        '<span class="title">' + esc(u.product_name_raw || u.active_ingredient_name || '未填药名') + '</span>' +
        '<span class="sub">' +
          (u.active_ingredient_name ? '有效成分 ' + esc(u.active_ingredient_name) + '　' : '') +
          (u.resistance_group ? '机理 ' + esc(u.resistance_group) + '　' : '') +
          (field ? esc(field.name) + '　' : '') +
          esc(ui.fmtDate(u.used_at)) +
        '</span>' +
        (u.note ? '<span class="sub faint">' + esc(u.note) + '</span>' : '') +
      '</div>' +
      '<div class="side">' +
        '<span class="badge">' + (u.record_source === 'manual' ? '补记' : '诊断后记录') + '</span>' +
        '<button class="btn sm ghost danger" data-del="' + u.id + '">删</button>' +
      '</div>' +
    '</div>';
  }

  /* ---------------- 表单 ---------------- */

  function usageForm(el) {
    ui.modal({
      title: '补记一次用药',
      html:
        '<label class="field"><span>用的什么药（必填）</span>' +
        '<input name="product_name_raw" placeholder="照包装上写就行" /></label>' +
        '<div class="grid two mt-3" style="gap:12px">' +
          '<label class="field"><span>有效成分</span>' +
            '<input name="active_ingredient_name" placeholder="例如：嘧菌酯" /></label>' +
          '<label class="field"><span>作用机理分组</span>' +
            '<input name="resistance_group" placeholder="标签上的分组号，如 11" /></label>' +
        '</div>' +
        '<div class="grid two mt-3" style="gap:12px">' +
          '<label class="field"><span>哪天打的</span>' +
            '<input name="used_at" type="date" value="' +
            esc(new Date().toISOString().slice(0, 10)) + '" /></label>' +
          '<label class="field"><span>哪块地</span>' +
            '<select name="field_id"><option value="">（不选也行）</option>' +
            st.fields.map(function (f) {
              return '<option value="' + f.id + '">' + esc(f.name) + '</option>';
            }).join('') + '</select></label>' +
        '</div>' +
        '<label class="field mt-3"><span>备注</span>' +
        '<input name="note" placeholder="例如：只打了半棚，剩下的明天打" /></label>' +
        '<p class="tiny faint mt-3">有效成分和作用机理填了之后，系统才能帮你算抗性。' +
        '不知道就空着，不影响记录。</p>',
      okText: '记下来'
    }).then(function (r) {
      if (!r.ok) return;
      var v = r.values;
      if (!v.product_name_raw && !v.active_ingredient_name) {
        ui.toast('至少填个药名', 'err');
        return;
      }
      api.post('/pesticide/usage', {
        product_name_raw: v.product_name_raw || null,
        active_ingredient_name: v.active_ingredient_name || null,
        resistance_group: v.resistance_group || null,
        field_id: v.field_id ? Number(v.field_id) : null,
        used_at: v.used_at || null,
        note: v.note || null
      }).then(function () {
        ui.toast('记下了', 'ok');
        Agri.app.go('pesticide');
      }).catch(function (e) { ui.toast(e.message, 'err'); });
    });
  }

  /** 合规校验工具：不经过模型，直接问规则引擎 */
  function checkForm(el) {
    api.get('/farm/crops').then(function (crops) {
      ui.modal({
        title: '这个药能不能用',
        html:
          '<label class="field"><span>作物</span>' +
          '<select name="crop_name"><option value="">（选一下）</option>' +
          (crops || []).map(function (c) {
            return '<option value="' + esc(c.name) + '">' + esc(c.name) + '</option>';
          }).join('') + '</select></label>' +
          '<label class="field mt-3"><span>要防的是什么</span>' +
          '<input name="disease_name" placeholder="例如：番茄晚疫病" /></label>' +
          '<label class="field mt-3"><span>生长阶段</span>' +
          '<select name="growth_stage"><option value="">（不清楚）</option>' +
          ['苗期', '生长期', '开花期', '结果期', '成熟期'].map(function (s) {
            return '<option value="' + s + '">' + s + '</option>';
          }).join('') + '</select></label>' +
          '<p class="tiny faint mt-3">这一步不走大模型，是纯规则校验：' +
          '逐条核对登记作物、防治对象、生长阶段、安全间隔期和抗性风险。</p>',
        okText: '查一下'
      }).then(function (r) {
        if (!r.ok) return;
        var v = r.values;
        if (!v.crop_name || !v.disease_name) {
          ui.toast('作物和防治对象都要填', 'err');
          return;
        }
        api.post('/pesticide/rule-check', {
          crop_name: v.crop_name,
          disease_name: v.disease_name.trim(),
          growth_stage: v.growth_stage || null
        }).then(function (res) {
          showCheck(res);
        }).catch(function (e) { ui.toast(e.message, 'err'); });
      });
    });
  }

  function showCheck(res) {
    var approved = res.approved || [], blocked = res.blocked || [];
    ui.modal({
      title: approved.length ? '可以用这些' : '没有能用的',
      cancel: false,
      okText: '知道了',
      html:
        '<p class="small muted">共核对 ' + res.checked + ' 个登记产品。' +
        esc(res.message || '') + '</p>' +
        (approved.length
          ? '<div class="mt-3">' + approved.map(function (p) {
              return '<div class="answer-sec">' +
                '<div class="t">' + esc(p.product_name) + '</div>' +
                '<div class="b small">' +
                  esc((p.active_ingredients || []).join('、')) + '<br>' +
                  '用量（标签原文）：' + esc(p.label_dosage_text || '见标签') +
                  (p.safety_interval_days !== null && p.safety_interval_days !== undefined
                    ? '<br>安全间隔期：' + p.safety_interval_days + ' 天' : '') +
                  ((p.warnings || []).length ? '<br>⚠ ' + esc(p.warnings.join('；')) : '') +
                '</div></div>';
            }).join('') + '</div>'
          : '<p class="mt-3" style="color:var(--c-danger)">' +
            '没有找到符合条件的登记农药。可能是这个病害本身没有对症的登记药' +
            '（例如病毒病），也可能登记范围不含你种的作物。' +
            '请咨询当地植保部门。</p>') +
        (blocked.length
          ? '<details class="mt-3"><summary class="small muted" style="cursor:pointer">' +
            '被拦下的 ' + blocked.length + ' 个，看看原因</summary>' +
            '<div class="tbl-wrap mt-2"><table class="tbl">' +
            '<thead><tr><th>产品</th><th>原因</th></tr></thead><tbody>' +
            blocked.map(function (p) {
              return '<tr><td>' + esc(p.product_name) + '</td><td>' +
                esc((p.blocked_messages || p.blocked_reasons || []).join('；')) +
                '</td></tr>';
            }).join('') + '</tbody></table></div></details>'
          : '')
    });
  }

})(window.Agri);
