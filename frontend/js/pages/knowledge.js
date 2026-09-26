/* ============================================================
   pages/knowledge.js —— 知识库查询与检索调试
   ------------------------------------------------------------
   ★ 这个页面有两个受众，刻意分成两个标签页：

     「查病害」给农户/农技员看 —— 只要答案和出处。
     「检索调试」给开发者看 —— 把 RRF 的向量排名、关键词排名、
       融合分全部摊开。

   为什么必须把检索过程暴露出来：文档难点 5（RAG 不准）和难点 13
   （结果无法验证）的共同解法就是**可观测**。没有这个页面，
   换 Embedding 模型、调阈值、改分词策略之后效果是变好还是变坏，
   只能凭感觉。有了它，任何一次检索都能截图对比。
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, api = Agri.api, esc = ui.esc;

  var st = { tab: 'query', crops: [], diseases: [], detail: null };

  Agri.pages.knowledge = {
    id: 'knowledge',
    label: '资料库',
    icon: '📚',

    render: function (el) {
      return Promise.all([
        api.get('/farm/crops'),
        api.get('/knowledge/stats')
      ]).then(function (r) {
        st.crops = r[0] || [];
        st.stats = r[1] || {};
        st.tab = 'query';
        paint(el);
      });
    }
  };

  function paint(el) {
    el.innerHTML =
      '<div class="page-head">' +
        '<div><h1>资料库</h1>' +
        '<div class="sub">系统判断时查的就是这里面的资料</div></div>' +
        '<div class="seg" style="max-width:280px">' +
          '<button class="seg-btn' + (st.tab === 'query' ? ' is-on' : '') +
            '" data-tab="query">查病害</button>' +
          '<button class="seg-btn' + (st.tab === 'debug' ? ' is-on' : '') +
            '" data-tab="debug">检索调试</button>' +
        '</div>' +
      '</div>' +
      statsCard() +
      '<div id="tabBody" class="mt-4"></div>';

    el.querySelector('.seg').addEventListener('click', function (e) {
      var b = e.target.closest('[data-tab]');
      if (!b) return;
      st.tab = b.getAttribute('data-tab');
      paint(el);
    });

    if (st.tab === 'query') renderQuery(el);
    else renderDebug(el);
  }

  function statsCard() {
    var s = st.stats || {};
    var warn = (s.chunks_orphan || 0) + (s.chunks_unembedded || 0);
    return '<div class="card mt-4"><div class="card-body">' +
      '<div class="grid three" style="gap:12px">' +
        '<div class="stat"><span class="num">' + (s.documents || 0) + '</span>' +
          '<span class="lbl">篇资料</span></div>' +
        '<div class="stat"><span class="num">' + (s.chunks || 0) + '</span>' +
          '<span class="lbl">个知识片段</span></div>' +
        '<div class="stat"><span class="num">' + (s.diseases || 0) + '</span>' +
          '<span class="lbl">条病害虫害</span></div>' +
      '</div>' +
      (warn
        ? '<p class="small mt-3" style="color:var(--c-warn)">⚠ 有 ' +
          (s.chunks_orphan || 0) + ' 个孤立片段、' + (s.chunks_unembedded || 0) +
          ' 个未向量化片段 —— 这些内容检索不到，需要重建索引。</p>'
        : '<p class="small muted mt-3">片段索引状态正常：' +
          '没有孤立片段，也没有未向量化的片段。</p>') +
      (s.documents_pending
        ? '<p class="small muted">还有 ' + s.documents_pending +
          ' 篇待人工审核，审核通过后才会进入检索范围。</p>' : '') +
      '</div></div>';
  }

  /* ================= 查病害 ================= */

  function renderQuery(el) {
    var box = el.querySelector('#tabBody');
    box.innerHTML =
      '<div class="card">' +
        '<div class="card-head"><h3>按作物查</h3></div>' +
        '<div class="card-body">' +
          '<div class="row wrap" style="gap:8px">' +
            '<button class="btn sm" data-crop="">全部</button>' +
            st.crops.map(function (c) {
              return '<button class="btn sm" data-crop="' + esc(c.name) + '">' +
                esc(c.name) + '</button>';
            }).join('') +
          '</div>' +
          '<div id="disList" class="mt-4">' + ui.loading() + '</div>' +
        '</div>' +
      '</div>';

    box.querySelector('.row').addEventListener('click', function (e) {
      var b = e.target.closest('[data-crop]');
      if (!b) return;
      loadDiseases(el, b.getAttribute('data-crop'));
    });
    loadDiseases(el, '');
  }

  function loadDiseases(el, crop) {
    var box = el.querySelector('#disList');
    box.innerHTML = ui.loading();
    api.get('/knowledge/diseases', { crop_name: crop || undefined, limit: 100 })
      .then(function (list) {
        st.diseases = list || [];
        if (!list.length) { box.innerHTML = ui.empty('这个作物下暂时没有条目'); return; }
        var CAT = { disease: '病害', pest: '虫害',
                    physiological: '生理障碍', environmental: '环境问题' };
        box.innerHTML = '<div class="list">' + list.map(function (d) {
          return '<div class="item clickable" data-did="' + d.id + '">' +
            '<div class="main-col">' +
              '<span class="title">' + esc(d.standard_name) + '</span>' +
              '<span class="sub">' + esc(CAT[d.category] || d.category) +
                '　·　适用：' + esc((d.crop_names || []).join('、') || '通用') + '</span>' +
            '</div>' +
            '<div class="side">' +
              (d.severity ? '<span class="badge">' + esc(d.severity) + '</span>' : '') +
            '</div>' +
          '</div>';
        }).join('') + '</div>';

        box.onclick = function (e) {
          var it = e.target.closest('[data-did]');
          if (!it) return;
          showDisease(parseInt(it.getAttribute('data-did'), 10));
        };
      })
      .catch(function (e) { box.innerHTML = ui.empty(e.message); });
  }

  function showDisease(id) {
    api.get('/knowledge/diseases/' + id).then(function (d) {
      var cond = d.conditions || {};
      var condRows = Object.keys(cond).filter(function (k) {
        return k.charAt(0) !== '_';
      }).map(function (k) {
        return '<div class="kv"><span class="k">' + esc(COND_LABEL[k] || k) +
               '</span><span class="v">' + esc(cond[k]) + '</span></div>';
      }).join('');

      var stages = d.stage_susceptibility || {};
      var stageRows = Object.keys(stages).map(function (k) {
        return '<span class="badge">' + esc(k) + '：' + esc(stages[k]) + '</span>';
      }).join(' ');

      ui.modal({
        title: d.standard_name,
        cancel: false,
        okText: '关闭',
        html:
          '<div class="row wrap" style="gap:6px">' +
            '<span class="pill">' + esc(d.category) + '</span>' +
            '<span class="pill">适用 ' + esc((d.crop_names || []).join('、') || '通用') + '</span>' +
            (d.severity ? '<span class="pill warn">严重度 ' + esc(d.severity) + '</span>' : '') +
          '</div>' +
          (d.aliases && d.aliases.length
            ? '<p class="small muted">也叫：' + esc(d.aliases.join('、')) + '</p>' : '') +
          (d.symptoms
            ? '<div class="answer-sec"><div class="t">长什么样</div>' +
              '<div class="b">' + esc(d.symptoms) + '</div></div>' : '') +
          (d.pathogen
            ? '<div class="answer-sec"><div class="t">什么引起的</div>' +
              '<div class="b">' + esc(d.pathogen) + '</div></div>' : '') +
          (condRows
            ? '<div class="answer-sec"><div class="t">什么条件下容易发生</div>' +
              '<div class="b">' + condRows + '</div></div>' : '') +
          (stageRows
            ? '<div class="answer-sec"><div class="t">各生长期的易感程度</div>' +
              '<div class="b">' + stageRows + '</div></div>' : '') +
          (d.control_advice
            ? '<div class="answer-sec"><div class="t">怎么防怎么治</div>' +
              '<div class="b">' + esc(d.control_advice) + '</div></div>' : '')
      });
    }).catch(function (e) { ui.toast(e.message, 'err'); });
  }

  var COND_LABEL = {
    temp_min: '最低适宜温度(℃)', temp_max: '最高适宜温度(℃)',
    humidity_min: '最低湿度(%)', rain_hours_min: '累计降雨要求(小时)',
    duration_days: '持续天数', wind_max: '最大风速(m/s)',
    wind: '风速要求', note: '补充说明'
  };

  /* ================= 检索调试 ================= */

  function renderDebug(el) {
    var box = el.querySelector('#tabBody');
    box.innerHTML =
      '<div class="card">' +
        '<div class="card-head"><h3>检索调试</h3>' +
          '<span class="tiny faint">看系统是怎么把资料找出来的</span></div>' +
        '<div class="card-body col">' +
          '<label class="field"><span>用农户的原话试试（越口语越好）</span>' +
            '<input name="q" id="dq" placeholder="例如：叶背有白毛，像撒了一层面粉" /></label>' +
          '<div class="grid two" style="gap:12px">' +
            '<label class="field"><span>限定作物（可选）</span>' +
              '<select id="dcrop"><option value="">不限</option>' +
              st.crops.map(function (c) {
                return '<option value="' + esc(c.name) + '">' + esc(c.name) + '</option>';
              }).join('') + '</select></label>' +
            '<label class="field"><span>取前几条</span>' +
              '<input id="dk" type="number" value="5" min="1" max="20" /></label>' +
          '</div>' +
          '<button class="btn primary" id="dgo">查一下</button>' +
        '</div>' +
      '</div>' +
      '<div id="dout" class="mt-4"></div>';

    box.querySelector('#dgo').addEventListener('click', doSearch);
    box.querySelector('#dq').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') doSearch();
    });
  }

  function doSearch() {
    var q = document.getElementById('dq').value.trim();
    if (!q) { ui.toast('写点什么再查', 'err'); return; }
    var crop = document.getElementById('dcrop').value;
    var k = parseInt(document.getElementById('dk').value, 10) || 5;
    var out = document.getElementById('dout');
    out.innerHTML = ui.loading('正在检索…');

    api.post('/knowledge/search', {
      query: q, crop_name: crop || null, top_k: k
    }).then(function (r) {
      var hits = r.hits || [];
      out.innerHTML =
        '<div class="card">' +
          '<div class="card-head"><h3>检索过程</h3>' +
            (r.by_llm ? '<span class="pill info">大模型改写</span>'
                      : '<span class="pill">本地规则改写</span>') + '</div>' +
          '<div class="card-body">' +
            '<div class="kv"><span class="k">你的原话</span>' +
              '<span class="v">' + esc(r.query_original) + '</span></div>' +
            '<div class="kv"><span class="k">扩展出的专业词</span>' +
              '<span class="v">' + esc((r.expanded_terms || []).join('、') || '（无）') +
              '</span></div>' +
            '<div class="kv"><span class="k">实际用于向量检索的文本</span>' +
              '<span class="v small">' + esc(r.query_rewritten) + '</span></div>' +
            '<p class="small muted mt-3">这一步就是文档难点 5 的关键：' +
              '农户说「白毛」，资料里写「白粉病 菌丝」—— 不做口语改写，' +
              '向量检索几乎必然落空。</p>' +
          '</div>' +
        '</div>' +
        (hits.length
          ? '<div class="card mt-4"><div class="card-head">' +
            '<h3>命中 ' + hits.length + ' 条</h3>' +
            '<span class="tiny faint">融合分 = 两路排名倒数加权求和</span></div>' +
            '<div class="tbl-wrap"><table class="tbl"><thead><tr>' +
            '<th>#</th><th>病害 / 标题</th><th>融合分</th>' +
            '<th>向量排名</th><th>关键词排名</th><th>来源</th>' +
            '</tr></thead><tbody>' +
            hits.map(function (h, i) {
              var ranks = h.ranks || {};
              return '<tr>' +
                '<td>' + (i + 1) + '</td>' +
                '<td><strong>' + esc(h.disease_name || h.title) + '</strong>' +
                  '<div class="tiny muted">' + esc((h.content || '').slice(0, 90)) +
                  '…</div></td>' +
                '<td>' + (h.fused_score || 0).toFixed(5) + '</td>' +
                '<td>' + (ranks.vector !== undefined ? '第 ' + ranks.vector : '未命中') + '</td>' +
                '<td>' + (ranks.keyword !== undefined ? '第 ' + ranks.keyword : '未命中') + '</td>' +
                '<td class="tiny">' + esc(h.source_name || '—') + '</td>' +
              '</tr>';
            }).join('') + '</tbody></table></div></div>'
          : '<div class="card mt-4">' + ui.empty('没有检索到内容。试试换个说法，或者去掉作物限定。') + '</div>');
    }).catch(function (e) {
      out.innerHTML = ui.empty(e.message);
    });
  }

})(window.Agri);
