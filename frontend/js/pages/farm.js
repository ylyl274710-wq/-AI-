/* ============================================================
   pages/farm.js —— 我的地（农田档案）
   ------------------------------------------------------------
   ★ 这份档案是「个性化农田记忆」的载体，也是 Agent 上下文节点
     的主要数据源。农户填得越全，诊断越准 —— 但**不能强制填**。
     所以除名称外全部可选，缺字段时给明确的「填了有什么用」提示，
     而不是拿红色星号逼他填。
   ============================================================ */
(function (Agri) {
  'use strict';
  var ui = Agri.ui, api = Agri.api, esc = ui.esc;

  var st = { farms: [], crops: [], growth: [] };
  var STAGES = ['苗期', '生长期', '开花期', '结果期', '成熟期'];

  Agri.pages.farm = {
    id: 'farm',
    label: '我的地',
    icon: '🌾',

    render: function (el) {
      return Promise.all([
        api.get('/farm/farms'),
        api.get('/farm/crops'),
        api.get('/farm/growth-records')
      ]).then(function (r) {
        st.farms = r[0] || [];
        st.crops = r[1] || [];
        st.growth = r[2] || [];
        paint(el);
      });
    }
  };

  function cropName(id) {
    var c = st.crops.filter(function (x) { return x.id === id; })[0];
    return c ? c.name : ('作物#' + id);
  }

  /** 地块当前种什么：同一地块只保留一条 is_current=true 的记录 */
  function currentGrowth(fieldId) {
    return st.growth.filter(function (g) {
      return g.field_id === fieldId && g.is_current;
    })[0];
  }

  function paint(el) {
    el.innerHTML =
      '<div class="page-head">' +
        '<div><h1>我的地</h1>' +
        '<div class="sub">登记之后，系统才知道该按什么作物、什么生长期帮你判断</div></div>' +
        '<button class="btn primary" id="addFarm">+ 加一个农场/大棚</button>' +
      '</div>' +
      (st.farms.length
        ? st.farms.map(farmCard).join('')
        : '<div class="card">' +
          ui.empty('还没有登记地块。加一个之后，诊断时就能自动带上「这块地种的是什么」。', '🌾') +
          '</div>');

    el.querySelector('#addFarm').addEventListener('click', function () {
      farmForm(el, null);
    });

    el.addEventListener('click', function (e) {
      var b = e.target.closest('[data-act]');
      if (!b) return;
      var act = b.getAttribute('data-act');
      var id = parseInt(b.getAttribute('data-id'), 10);
      if (act === 'edit-farm') farmForm(el, id);
      else if (act === 'del-farm') delFarm(el, id);
      else if (act === 'add-field') fieldForm(el, id);
      else if (act === 'crop') growthForm(el, id);
    });
  }

  function farmCard(f) {
    var fields = f.fields || [];
    return '<div class="card pad-0 mt-4">' +
      '<div class="card-head">' +
        '<div><h3>' + esc(f.name) + '</h3>' +
          '<span class="small muted">' +
            esc([f.province, f.city, f.district].filter(Boolean).join(' ') || '未填位置') +
            (f.total_area_mu ? '　·　约 ' + f.total_area_mu + ' 亩' : '') +
          '</span></div>' +
        '<div class="row" style="gap:6px">' +
          '<button class="btn sm" data-act="add-field" data-id="' + f.id + '">+ 地块</button>' +
          '<button class="btn sm ghost" data-act="edit-farm" data-id="' + f.id + '">改</button>' +
          '<button class="btn sm ghost danger" data-act="del-farm" data-id="' + f.id + '">删</button>' +
        '</div>' +
      '</div>' +
      (fields.length
        ? '<div class="list">' + fields.map(function (fd) {
            var g = currentGrowth(fd.id);
            return '<div class="item">' +
              '<div class="main-col">' +
                '<span class="title">' + esc(fd.name) + '</span>' +
                '<span class="sub">' +
                  (fd.area_mu ? fd.area_mu + ' 亩' : '面积未填') +
                  (fd.soil_type ? '　·　' + esc(fd.soil_type) : '') +
                  (fd.irrigation_type ? '　·　' + esc(fd.irrigation_type) : '') +
                '</span>' +
                '<span class="sub">' +
                  (g
                    ? '现在种：' + esc(cropName(g.crop_id)) +
                      (g.variety ? '（' + esc(g.variety) + '）' : '') +
                      (g.stage ? '　·　' + esc(g.stage) : '') +
                      (g.expected_harvest_date
                        ? '　·　预计 ' + esc(ui.fmtDate(g.expected_harvest_date)) + ' 收获'
                        : '')
                    : '<span class="faint">还没登记种什么</span>') +
                '</span>' +
              '</div>' +
              '<div class="side">' +
                '<button class="btn sm" data-act="crop" data-id="' + fd.id + '">' +
                  (g ? '改作物' : '登记作物') + '</button>' +
              '</div>' +
            '</div>';
          }).join('') + '</div>'
        : ui.empty('这个农场还没有地块', '🟫')) +
    '</div>';
  }

  /* ---------------- 表单 ---------------- */

  function farmForm(el, id) {
    var f = id ? st.farms.filter(function (x) { return x.id === id; })[0] : null;
    ui.modal({
      title: f ? '修改农场' : '加一个农场/大棚',
      html:
        '<label class="field"><span>叫什么（必填）</span>' +
        '<input name="name" value="' + esc(f ? f.name : '') +
        '" placeholder="例如：村东头大棚" /></label>' +
        '<div class="grid two mt-3" style="gap:12px">' +
          '<label class="field"><span>省</span><input name="province" value="' +
            esc(f ? f.province || '' : '') + '" placeholder="河南省" /></label>' +
          '<label class="field"><span>市</span><input name="city" value="' +
            esc(f ? f.city || '' : '') + '" placeholder="郑州市" /></label>' +
        '</div>' +
        '<div class="grid two mt-3" style="gap:12px">' +
          '<label class="field"><span>区/县</span><input name="district" value="' +
            esc(f ? f.district || '' : '') + '" placeholder="中牟县" /></label>' +
          '<label class="field"><span>总面积（亩）</span><input name="total_area_mu" ' +
            'type="number" step="0.1" value="' +
            esc(f && f.total_area_mu !== null ? f.total_area_mu : '') + '" /></label>' +
        '</div>' +
        '<label class="field mt-3"><span>备注</span>' +
        '<input name="remark" value="' + esc(f ? f.remark || '' : '') +
        '" placeholder="随便记点什么" /></label>',
      okText: f ? '保存' : '创建'
    }).then(function (r) {
      if (!r.ok) return;
      var v = r.values;
      if (!v.name || !v.name.trim()) { ui.toast('名字得填一下', 'err'); return; }
      var body = {
        name: v.name.trim(),
        province: v.province || null,
        city: v.city || null,
        district: v.district || null,
        remark: v.remark || null,
        total_area_mu: v.total_area_mu === '' ? null : Number(v.total_area_mu)
      };
      var p = f ? api.patch('/farm/farms/' + f.id, body)
                : api.post('/farm/farms', body);
      p.then(function () {
        ui.toast(f ? '改好了' : '建好了', 'ok');
        Agri.app.go('farm');
      }).catch(function (e) { ui.toast(e.message, 'err'); });
    });
  }

  function delFarm(el, id) {
    ui.modal({
      title: '删除农场',
      html: '<p>删掉之后列表里就不显示了。' +
            '<strong>以前的诊断记录和用药记录不会丢</strong>，' +
            '它们还要用来判断抗性和复查。</p>',
      okText: '确认删除'
    }).then(function (r) {
      if (!r.ok) return;
      api.del('/farm/farms/' + id).then(function () {
        ui.toast('已删除', 'ok');
        Agri.app.go('farm');
      }).catch(function (e) { ui.toast(e.message, 'err'); });
    });
  }

  function fieldForm(el, farmId) {
    ui.modal({
      title: '加一块地',
      html:
        '<label class="field"><span>地块叫什么（必填）</span>' +
        '<input name="name" placeholder="例如：1 号棚 / 东边那块" /></label>' +
        '<div class="grid two mt-3" style="gap:12px">' +
          '<label class="field"><span>面积（亩）</span>' +
            '<input name="area_mu" type="number" step="0.1" /></label>' +
          '<label class="field"><span>土质</span>' +
            '<input name="soil_type" placeholder="沙壤土 / 黏土" /></label>' +
        '</div>' +
        '<label class="field mt-3"><span>浇水方式</span>' +
        '<input name="irrigation_type" placeholder="滴灌 / 漫灌 / 喷灌" /></label>',
      okText: '创建'
    }).then(function (r) {
      if (!r.ok) return;
      var v = r.values;
      if (!v.name || !v.name.trim()) { ui.toast('地块名字得填', 'err'); return; }
      api.post('/farm/farms/' + farmId + '/fields', {
        name: v.name.trim(),
        area_mu: v.area_mu === '' ? null : Number(v.area_mu),
        soil_type: v.soil_type || null,
        irrigation_type: v.irrigation_type || null
      }).then(function () {
        ui.toast('地块建好了', 'ok');
        Agri.app.go('farm');
      }).catch(function (e) { ui.toast(e.message, 'err'); });
    });
  }

  function growthForm(el, fieldId) {
    var g = currentGrowth(fieldId);
    var options = st.crops.map(function (c) {
      return '<option value="' + c.id + '"' +
        (g && g.crop_id === c.id ? ' selected' : '') + '>' + esc(c.name) + '</option>';
    }).join('');

    ui.modal({
      title: g ? '改这块地种的作物' : '这块地种了什么',
      html:
        '<label class="field"><span>作物（必填）</span>' +
        '<select name="crop_id"><option value="">（选一下）</option>' +
        options + '</select></label>' +
        '<label class="field mt-3"><span>品种</span>' +
        '<input name="variety" value="' + esc(g ? g.variety || '' : '') +
        '" placeholder="例如：金棚 1 号" /></label>' +
        '<label class="field mt-3"><span>现在长到哪一步（必填）</span>' +
        '<select name="stage"><option value="">（选一下）</option>' +
        STAGES.map(function (s) {
          return '<option value="' + s + '"' +
            (g && g.stage === s ? ' selected' : '') + '>' + s + '</option>';
        }).join('') + '</select></label>' +
        '<div class="grid two mt-3" style="gap:12px">' +
          '<label class="field"><span>播种日期</span>' +
            '<input name="sowing_date" type="date" value="' +
            esc(g && g.sowing_date ? g.sowing_date : '') + '" /></label>' +
          '<label class="field"><span>预计收获</span>' +
            '<input name="expected_harvest_date" type="date" value="' +
            esc(g && g.expected_harvest_date ? g.expected_harvest_date : '') + '" /></label>' +
        '</div>' +
        '<p class="tiny faint mt-3">生长期填了之后，系统判断「这个阶段容易得什么病」' +
        '和「这个阶段能不能用某个药」都会更准。</p>',
      okText: '保存'
    }).then(function (r) {
      if (!r.ok) return;
      var v = r.values;
      if (!v.crop_id) { ui.toast('选一下种的什么作物', 'err'); return; }
      if (!v.stage) { ui.toast('选一下长到哪一步了', 'err'); return; }
      api.post('/farm/growth-records', {
        field_id: fieldId,
        crop_id: Number(v.crop_id),
        variety: v.variety || null,
        stage: v.stage,
        sowing_date: v.sowing_date || null,
        expected_harvest_date: v.expected_harvest_date || null
      }).then(function () {
        ui.toast('记下了', 'ok');
        Agri.app.go('farm');
      }).catch(function (e) { ui.toast(e.message, 'err'); });
    });
  }

})(window.Agri);
