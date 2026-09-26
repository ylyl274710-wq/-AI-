/* ============================================================
   pages/diagnose.js —— 拍照诊断（主链路）
   ------------------------------------------------------------
   这是整个产品的核心页面。设计上的三个刻意选择：

   1. **先拍照，后填字**。表单顺序按农户的实际操作顺序排：
      拍照 → 选作物 → 描述症状。反过来（先选作物再拍照）会让
      用户在上传前就卡在表单里。
   2. **拍摄角度可标注**。文档难点 1 要求引导用户补拍叶片正反面，
      但农户不知道「正反面」是什么意思，所以用「叶片正面/背面」
      这种直白标签，并允许留空（不强制）。
   3. **进度必须可见**。一次诊断要跑几秒到几十秒，没有进度反馈
      用户会以为卡死然后刷新 —— 刷新等于重复提交。
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, api = Agri.api, esc = ui.esc, T = ui.T, cfg = Agri.cfg;

  var st = { crops: [], fields: [], files: [], running: false };

  Agri.pages.diagnose = {
    id: 'diagnose',
    label: '诊断',
    icon: '📷',

    render: function (el) {
      st.files = [];
      st.running = false;
      return Promise.all([
        api.get('/farm/crops'),
        api.get('/farm/farms')
      ]).then(function (r) {
        st.crops = r[0] || [];
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

  /* ---------------- 表单 ---------------- */

  function paint(el) {
    el.innerHTML =
      '<div class="page-head">' +
        '<div><h1>拍张照片，我帮你看看</h1>' +
        '<div class="sub">拍清楚一点，叶子正反面都拍上，判断会准很多</div></div>' +
      '</div>' +

      '<div class="grid two">' +
        '<div class="card">' +
          '<div class="card-head"><h3>① 拍照</h3>' +
            '<span class="tiny faint">最多 ' + cfg.maxImages + ' 张</span></div>' +
          '<div class="card-body">' +
            '<div class="drop" id="drop">' +
              '<span class="big">📷</span>' +
              '<div class="strong">点这里选照片，或者把照片拖进来</div>' +
              '<div class="small muted mt-2">手机可以现拍。' +
                '照片里的位置信息会自动去掉。</div>' +
            '</div>' +
            '<input type="file" id="fileInput" accept="image/*" multiple hidden />' +
            '<div class="thumbs" id="thumbs"></div>' +
          '</div>' +
        '</div>' +

        '<div class="card">' +
          '<div class="card-head"><h3>② 说说情况</h3></div>' +
          '<div class="card-body col">' +
            '<label class="field"><span>种的是什么</span>' +
              '<select id="crop"><option value="">（选一下）</option>' +
              st.crops.map(function (c) {
                return '<option value="' + esc(c.name) + '">' + esc(c.name) + '</option>';
              }).join('') +
              '</select></label>' +

            '<label class="field"><span>哪块地</span>' +
              '<select id="field"><option value="">（不选也行）</option>' +
              st.fields.map(function (f) {
                return '<option value="' + f.id + '">' + esc(f.name) + '</option>';
              }).join('') +
              '</select></label>' +

            '<label class="field"><span>长到哪一步了</span>' +
              '<select id="stage"><option value="">（不清楚）</option>' +
              ['苗期', '生长期', '开花期', '结果期', '成熟期'].map(function (s) {
                return '<option value="' + s + '">' + s + '</option>';
              }).join('') +
              '</select></label>' +

            '<label class="field"><span>有什么不对劲</span>' +
              '<textarea id="query" placeholder="例如：叶子背面有一层白毛，像撒了面粉；' +
                '或者：下部的叶子发黄，边缘干枯"></textarea></label>' +

            '<button class="btn primary lg block" id="submit">开始判断</button>' +
            '<p class="tiny faint center">' +
              '照片和文字至少要有一样。判断结果仅供参考，用药请以产品标签为准。</p>' +
          '</div>' +
        '</div>' +
      '</div>' +

      '<div id="progress" class="mt-4"></div>' +
      '<div id="result" class="mt-4"></div>';

    bindUpload(el);
    el.querySelector('#submit').addEventListener('click', function () {
      submit(el);
    });
  }

  /* ---------------- 上传 ---------------- */

  function bindUpload(el) {
    var drop = el.querySelector('#drop');
    var input = el.querySelector('#fileInput');

    drop.addEventListener('click', function () { input.click(); });
    input.addEventListener('change', function () {
      addFiles(input.files);
      input.value = '';
      paintThumbs(el);
    });

    ['dragenter', 'dragover'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault(); drop.classList.add('over');
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault(); drop.classList.remove('over');
      });
    });
    drop.addEventListener('drop', function (e) {
      if (e.dataTransfer && e.dataTransfer.files) {
        addFiles(e.dataTransfer.files);
        paintThumbs(el);
      }
    });
  }

  function addFiles(list) {
    [].forEach.call(list || [], function (f) {
      if (!/^image\//.test(f.type)) {
        ui.toast('「' + f.name + '」不是图片', 'err');
        return;
      }
      if (st.files.length >= cfg.maxImages) {
        ui.toast('最多 ' + cfg.maxImages + ' 张，多的先不放了', 'err');
        return;
      }
      st.files.push({
        file: f,
        url: URL.createObjectURL(f),
        // 默认猜「叶片背面」：白粉病、霜霉病这类最常见的病害，
        // 症状都集中在叶背，默认值押在概率最高的那个上。
        shot: 'leaf_back'
      });
    });
  }

  function paintThumbs(el) {
    var box = el.querySelector('#thumbs');
    box.innerHTML = st.files.map(function (f, i) {
      return '<div class="thumb">' +
        '<img src="' + f.url + '" alt="" />' +
        '<button class="x" data-del="' + i + '" title="移除">×</button>' +
        '<div class="meta"><select data-shot="' + i + '">' +
          cfg.shotTypes.map(function (s) {
            return '<option value="' + s.v + '"' +
              (s.v === f.shot ? ' selected' : '') + '>' + esc(s.t) + '</option>';
          }).join('') +
        '</select></div>' +
      '</div>';
    }).join('');

    box.onclick = function (e) {
      var d = e.target.closest('[data-del]');
      if (!d) return;
      st.files.splice(parseInt(d.getAttribute('data-del'), 10), 1);
      paintThumbs(el);
    };
    box.onchange = function (e) {
      var s = e.target.closest('[data-shot]');
      if (!s) return;
      st.files[parseInt(s.getAttribute('data-shot'), 10)].shot =
        s.value;
    };
  }

  /* ---------------- 提交 ---------------- */

  function submit(el) {
    if (st.running) return;
    var query = el.querySelector('#query').value.trim();
    if (!st.files.length && !query) {
      ui.toast('请先拍照，或者用文字说说症状', 'err');
      return;
    }

    var btn = el.querySelector('#submit');
    btn.disabled = true;
    btn.textContent = '正在判断…';
    st.running = true;

    var progBox = el.querySelector('#progress');
    el.querySelector('#result').innerHTML = '';
    progBox.innerHTML = '<div class="card"><div class="card-head">' +
      '<h3>正在判断</h3><span class="tiny faint" id="progTip">准备中…</span></div>' +
      '<div class="card-body"><div class="trace" id="traceBox"></div></div></div>';

    var steps = [];
    var fieldId = el.querySelector('#field').value;

    function done() {
      st.running = false;
      btn.disabled = false;
      btn.textContent = '再判断一次';
    }

    api.streamDiagnose({
      query: query,
      crop_name: el.querySelector('#crop').value,
      field_id: fieldId || undefined,
      growth_stage: el.querySelector('#stage').value,
      shot_types: JSON.stringify(st.files.map(function (f) { return f.shot; }))
    }, st.files.map(function (f) { return f.file; }), function (ev, d) {
      if (ev === 'start') {
        setTip(el, '正在读取照片…');
      } else if (ev === 'progress' && d.type === 'trace') {
        steps.push({ node: d.node, status: 'ok', elapsed_ms: d.elapsed_ms });
        el.querySelector('#traceBox').innerHTML = ui.traceBlock(steps);
        setTip(el, '正在' + ui.nodeLabel(d.node) + '…');
      } else if (ev === 'final') {
        progBox.innerHTML = '';
        el.querySelector('#result').innerHTML = resultHtml(d);
        bindResult(el, d);
        done();
      } else if (ev === 'done') {
        setTip(el, '完成');
      } else if (ev === 'error') {
        ui.toast(d.message || '诊断出错', 'err');
        done();
      }
    }).catch(function (e) {
      progBox.innerHTML = '';
      ui.toast(e.message || '诊断失败', 'err');
      done();
    });
  }

  function setTip(el, t) {
    var x = el.querySelector('#progTip');
    if (x) x.textContent = t;
  }

  /* ---------------- 结果 ---------------- */

  function resultHtml(d) {
    var head =
      '<div class="card">' +
        '<div class="card-head">' +
          '<h3>' + (d.top1 ? '可能的判断：' + esc(d.top1) : '判断结果') + '</h3>' +
          '<div class="row" style="gap:6px">' +
            (d.intent ? '<span class="pill">' + esc(intentLabel(d.intent)) + '</span>' : '') +
            (d.degraded ? '<span class="pill warn"><span class="dot"></span>' +
              esc(T('degraded')) + '</span>' : '') +
          '</div>' +
        '</div>' +
        '<div class="card-body">' +
          ui.answerBlock(d.answer, d.answer_sections) +
        '</div>' +
      '</div>';

    var conf = d.confidence
      ? '<div class="card mt-4"><div class="card-head"><h3>' + esc(T('confidenceTitle')) +
        '</h3></div><div class="card-body">' + ui.confBlock(d.confidence) + '</div></div>'
      : '';

    var rule = d.rule ? ruleHtml(d.rule) : '';

    var cites = (d.citations && d.citations.length)
      ? '<div class="card mt-4"><div class="card-head"><h3>' + esc(T('citation')) +
        '</h3></div><div class="card-body"><ol class="small" style="margin:0;padding-left:20px">' +
        d.citations.map(function (c) {
          return '<li>' + esc(c.title || '') +
            (c.source ? '　<span class="muted">' + esc(c.source) + '</span>' : '') +
            (c.url ? '　<a href="' + esc(c.url) + '" target="_blank" rel="noopener">查看原文</a>' : '') +
            '</li>';
        }).join('') + '</ol></div></div>'
      : '';

    var trace = (d.trace && d.trace.length)
      ? '<details class="card mt-4"><summary style="cursor:pointer;padding:12px 16px">' +
        '<strong>' + esc(T('trace')) + '</strong>' +
        '<span class="tiny faint">（点开看系统做了哪些事）</span></summary>' +
        '<div class="card-body">' + ui.traceBlock(d.trace) +
        toolCalls(d.tool_calls) + '</div></details>'
      : '';

    var errs = (d.errors && d.errors.length)
      ? '<div class="card mt-4"><div class="card-body small" style="color:var(--c-danger)">' +
        d.errors.map(function (e) { return '· ' + esc(e); }).join('<br>') + '</div></div>'
      : '';

    return head + conf + rule + cites + trace + errs;
  }

  function intentLabel(i) {
    return { diagnosis: '病虫害诊断', consult: '农业咨询', pesticide: '用药查询',
             farm_analysis: '农情分析', task: '农事提醒' }[i] || i;
  }

  /** 用药建议。★ 剂量一律显示标签原文，系统不生成剂量。 */
  function ruleHtml(rule) {
    var approved = rule.approved || [], blocked = rule.blocked || [];

    if (rule.hard_blocked && !approved.length) {
      return '<div class="card mt-4"><div class="card-head">' +
        '<h3>用药建议</h3><span class="pill danger"><span class="dot"></span>未通过安全校验</span>' +
        '</div><div class="card-body">' +
        '<p>' + esc(rule.message || '没有找到符合条件的登记农药。') + '</p>' +
        '<p class="small muted mt-2">这不是「查不到」，而是「不能用」。' +
        '可能原因：该病害没有对应的登记药剂（例如病毒病），' +
        '或者登记范围不覆盖你种的作物。请咨询当地植保部门。</p>' +
        blockedDetail(blocked) +
        '</div></div>';
    }

    var body = '';
    if (approved.length) {
      body += '<p class="small muted">共核对 ' + rule.checked +
        ' 个登记产品，下面 ' + approved.length + ' 个通过了作物、防治对象、' +
        '生长阶段、安全间隔期和抗性风险的校验：</p>';
      body += approved.map(function (p) {
        return '<div class="item" style="padding-left:0;padding-right:0">' +
          '<div class="main-col">' +
            '<span class="title">' + esc(p.product_name) + '</span>' +
            '<span class="sub">' + esc((p.active_ingredients || []).join('、')) +
              (p.resistance_groups && p.resistance_groups.length
                ? '　·　作用机理组 ' + esc(p.resistance_groups.join('、')) : '') + '</span>' +
            '<span class="sub">用量（标签原文）：' + esc(p.label_dosage_text || '见标签') + '</span>' +
            (p.safety_interval_days !== null && p.safety_interval_days !== undefined
              ? '<span class="sub">安全间隔期：' + p.safety_interval_days + ' 天</span>' : '') +
            ((p.warnings || []).length
              ? '<span class="small" style="color:var(--c-warn)">⚠ ' +
                esc(p.warnings.join('；')) + '</span>' : '') +
          '</div>' +
        '</div>';
      }).join('');
      body += '<p class="tiny faint mt-3">剂量取自农药标签原文，系统不会自己计算或改写剂量。' +
        '实际使用请以你手上的产品标签为准。</p>';
    }
    if (blocked.length) {
      body += '<details class="mt-3"><summary class="small muted" style="cursor:pointer">' +
        '看看被拦下的 ' + blocked.length + ' 个（为什么不能用）</summary>' +
        blockedDetail(blocked) + '</details>';
    }

    return '<div class="card mt-4"><div class="card-head"><h3>用药建议</h3>' +
      (approved.length
        ? '<span class="pill ok"><span class="dot"></span>已通过安全校验</span>'
        : '') +
      '</div><div class="card-body">' + body + '</div></div>';
  }

  function blockedDetail(list) {
    if (!list || !list.length) return '';
    return '<div class="tbl-wrap mt-3"><table class="tbl">' +
      '<thead><tr><th>产品</th><th>不能用的原因</th></tr></thead><tbody>' +
      list.map(function (p) {
        var reasons = p.blocked_messages || p.blocked_reasons || [];
        return '<tr><td>' + esc(p.product_name) + '</td><td>' +
          esc(reasons.join('；') || '不符合登记条件') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function toolCalls(calls) {
    if (!calls || !calls.length) return '';
    return '<div class="mt-3"><div class="small muted">系统调用了这些工具：</div>' +
      '<div class="tbl-wrap mt-2"><table class="tbl">' +
      '<thead><tr><th>步骤</th><th>工具</th><th>结果</th><th>耗时</th></tr></thead><tbody>' +
      calls.map(function (c) {
        return '<tr><td>' + esc(ui.nodeLabel(c.node)) + '</td>' +
          '<td><code>' + esc(c.tool) + '</code></td>' +
          '<td>' + (c.ok ? '成功' : '<span style="color:var(--c-danger)">' +
            esc(c.error || '失败') + '</span>') + '</td>' +
          '<td>' + (c.elapsed_ms || 0) + ' ms</td></tr>';
      }).join('') + '</tbody></table></div></div>';
  }

  /* ---------------- 结果上的交互 ---------------- */

  function bindResult(el, d) {
    if (!d.diagnosis_id) return;
    var box = el.querySelector('#result');
    var bar = document.createElement('div');
    bar.className = 'card mt-4';
    bar.innerHTML =
      '<div class="card-body row between wrap">' +
        '<span class="small muted">这个判断对不对？你说一声，我才能越用越准。</span>' +
        '<div class="row" style="gap:8px">' +
          '<button class="btn" data-fb="1">👍 是对的</button>' +
          '<button class="btn" data-fb="0">👎 不对</button>' +
        '</div>' +
      '</div>';
    box.appendChild(bar);

    bar.addEventListener('click', function (e) {
      var b = e.target.closest('[data-fb]');
      if (!b) return;
      var ok = b.getAttribute('data-fb') === '1';
      ui.modal({
        title: ok ? '谢谢确认' : '那实际是什么情况？',
        html: '<label class="field"><span>补充一句（可以不填）</span>' +
              '<textarea name="fbNote" placeholder="' +
              (ok ? '例如：打了药三天后好转了' : '例如：后来发现是缺镁，不是病害') +
              '"></textarea></label>',
        okText: '提交'
      }).then(function (r) {
        if (!r.ok) return;
        var note = r.values.fbNote || '';
        api.post('/diagnosis/' + d.diagnosis_id + '/feedback' +
                 api.qs({ resolved: ok, note: note }))
          .then(function () { ui.toast('记下了，谢谢', 'ok'); })
          .catch(function (err) { ui.toast(err.message, 'err'); });
      });
    });
  }

})(window.Agri);
