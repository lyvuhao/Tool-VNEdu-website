// ExtJS 4 giả lập tối thiểu cho popup "Chi tiết tiết học" (dùng để so sánh fill_form cũ/mới).
window.__events = [];
function ev(s) { window.__events.push(s); }
function mkRecord(d) { return { data: d, get: function (k) { return d[k]; } }; }
function mkStore(rows, loading) {
  return {
    rows: rows.slice(), loading: !!loading,
    getCount: function () { return this.rows.length; },
    each: function (fn) { this.rows.forEach(function (r) { fn(mkRecord(r)); }); },
    findRecord: function (f, v) { var r = this.rows.find(function (x) { return String(x[f]) === String(v); }); return r ? mkRecord(r) : null; },
    isLoading: function () { return this.loading; }
  };
}
var idSeq = 0;
function mkField(cfg) {
  var f = {
    id: 'fld-' + (++idSeq), name: cfg.name, xtype: cfg.xtype || 'textfield', fieldLabel: cfg.label || '',
    value: cfg.value === undefined ? '' : cfg.value, valueField: 'id', displayField: 'name',
    store: cfg.store || null, lazyRows: cfg.lazyRows || null, isExpanded: false,
    getName: function () { return this.name; }, getXType: function () { return this.xtype; },
    setValue: function (v) { this.value = v; ev(this.name + '.setValue=' + v); },
    getValue: function () { return this.value; },
    getRawValue: function () {
      if (!this.store) return String(this.value);
      var rec = this.store.findRecord('id', this.value); return rec ? String(rec.get('name')) : String(this.value);
    },
    fireEvent: function (name) { ev(this.name + '.' + name); if (this.onEvent) this.onEvent(name); },
    validate: function () { return true; },
    expand: function () {
      this.isExpanded = true; ev(this.name + '.expand');
      var self = this;
      if (self.lazyRows && self.store && self.store.getCount() === 0) {
        setTimeout(function () { self.store.rows = self.lazyRows.slice(); ev(self.name + '.lazyLoaded'); }, 120);
      }
    },
    collapse: function () { this.isExpanded = false; },
    bindStore: function (s) { this.store = s; ev(this.name + '.bindStore'); }
  };
  if (f.xtype === 'combobox') { f.getStore = function () { return this.store; }; }
  return f;
}
window.setupFake = function (scn) {
  window.__events = []; idSeq = 0;
  var mon = mkField({ name: 'mon_hoc_id', xtype: 'combobox', label: 'Môn học',
    store: mkStore(scn.monLazy ? [] : scn.mon), lazyRows: scn.monLazy ? scn.mon : null });
  var phan = mkField({ name: 'phan_mon_id', xtype: 'combobox', label: 'Phân môn', store: mkStore([]) });
  var xl = mkField({ name: 'xep_loai', xtype: 'combobox', label: 'Xếp loại', store: mkStore(scn.bindXepLoai ? [] : scn.xepLoai) });
  // Phân môn phụ thuộc Môn học: chọn môn -> nạp phân môn sau 200ms
  mon.onEvent = function (name) {
    if (name !== 'select') return;
    phan.store.loading = true;
    setTimeout(function () { phan.store.rows = (scn.phanByMon[String(mon.value)] || []).slice(); phan.store.loading = false; ev('phan_mon.loaded'); }, 200);
  };
  var plain = ['tiet_ppct', 'soluong_nghi', 'noi_dung', 'nhan_xet', 'diem'].map(function (n) {
    return mkField({ name: n, xtype: n === 'noi_dung' || n === 'nhan_xet' ? 'textareafield' : 'numberfield' });
  });
  var fields = [mon, phan].concat(plain).concat([xl]);
  if (scn.missingField) fields = fields.filter(function (f) { return f.name !== scn.missingField; });
  var form = { findField: function (n) { return fields.find(function (f) { return f.name === n; }) || null; },
               getFields: function () { return { items: fields }; } };
  var formPanel = { id: 'form-popup', getForm: function () { return form; }, isVisible: function () { return true; } };
  var win = { title: scn.noPopup ? 'Cửa sổ khác' : 'Chi tiết tiết học', isVisible: function () { return true; },
              down: function () { return formPanel; } };
  // combo cấp trang cùng tên (để thử bindStore)
  var pageXl = mkField({ name: 'xep_loai', xtype: 'combobox', store: mkStore(scn.xepLoai) });
  window.Ext = { ComponentQuery: { query: function (sel) {
    if (sel === 'window') return [win];
    if (sel === 'form') return scn.noPopup ? [] : [formPanel];
    if (sel === 'combobox') return [mon, phan, xl, pageXl];
    return [];
  } } };
  window.__fields = fields;
};
window.fakeState = function () {
  var out = {};
  (window.__fields || []).forEach(function (f) { out[f.name] = String(f.value); });
  return { values: out, events: window.__events.slice() };
};
