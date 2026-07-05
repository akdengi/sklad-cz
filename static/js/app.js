const STATUSES = ["— не указан —","Эмитирован","Нанесён","В обороте","Продан","Выбыл"];
const CZ_STATUS_MAP = {
  'EMITTED': 'Эмитирован',
  'APPLIED': 'Нанесён',
  'INTRODUCED': 'В обороте',
  'WRITTEN_OFF': 'Списан',
  'RETIRED': 'Выбыл',
  'WITHDRAWN': 'Выбыл',
  'INTRODUCED_RETURNED': 'Возвращён в оборот',
  'REAPPLY': 'Повторное нанесение',
  'BLOCKED': 'Заблокирован',
  'UNDEFINED': 'Не определён',
  'EMPTY': 'Пусто',
};
const CZ_STATUS_EXT_MAP = {
  'WAIT_SHIPMENT': 'Ожидает приёмку',
  'EXPORTED': 'Экспортирован',
  'LOAN_RETIRED': 'Выведен по рассрочке',
  'REMARK_RETIRED': 'Выбыл при перемаркировке',
};
const CZ_TO_UNIT_STATUS = {
  'EMITTED': 1, 'APPLIED': 2, 'INTRODUCED': 3, 'INTRODUCED_RETURNED': 3,
  'RETIRED': 5, 'WITHDRAWN': 5, 'WRITTEN_OFF': 5,
};
const DISPOSAL_TYPES = {"shipment":"Перед отгрузкой","return":"При возврате товара"};
const DISPOSAL_REASONS = {
  "remote_sale":"Дистанционная продажа",
  "remote_sale_return":"Возврат при дистанц. продаже",
  "own_needs":"Использование для собственных нужд",
  "production":"Использование для производственных целей",
  "gratuitous_transfer":"Безвозмездная передача",
  "eea_sale":"Трансграничная продажа в страны ЕЭАС",
  "export_eaes":"Экспорт за пределы ЕАЭС",
  "loss":"Утрата",
  "market_recall":"Отзыв товара с рынка",
};
const DISPOSAL_REASONS_NO_DOCS = ["own_needs","production","gratuitous_transfer","loss","market_recall"];
const DISPOSAL_STATUSES = ["Не начато","Отправлено в ЧЗ","Подтверждено ЧЗ"];

let editingSkuId = null, editingUnitId = null;
let czDuplicateCheckTimer = null;
let scanCount = 0;
let stockSort = { field: 'id', dir: 'desc' };
let unitDetailModal = null;
let cachedWarehouses = [];
let cachedDefaultAddress = '';
let cachedDefaultFias = '';
let qsFoundUnit = null;
let stockPage = 1, soldPage = 1, disposalPage = 1;
const PER_PAGE = 100;

document.addEventListener('DOMContentLoaded', () => {
  unitDetailModal = new bootstrap.Modal(document.getElementById('unit-detail-modal'));
  document.querySelectorAll('#nav .nav-link').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      document.querySelectorAll('#nav .nav-link').forEach(x => x.classList.remove('active'));
      link.classList.add('active');
      document.querySelectorAll('.tab').forEach(t => t.classList.add('d-none'));
      document.getElementById('tab-' + link.dataset.tab).classList.remove('d-none');
      render();
    });
  });
  document.querySelectorAll('#stock-table th.sortable').forEach(th => {
    th.addEventListener('click', () => toggleSort(th.dataset.sort));
  });
  document.getElementById('stock-filter').addEventListener('change', () => { stockPage = 1; renderStock(); });
  document.getElementById('stock-sku-filter').addEventListener('change', () => { stockPage = 1; renderStock(); });
  document.getElementById('stock-status-filter').addEventListener('change', () => { stockPage = 1; renderStock(); });
  document.getElementById('stock-no-cz').addEventListener('change', () => { stockPage = 1; renderStock(); });
  document.getElementById('stock-search').addEventListener('input', () => { stockPage = 1; renderStock(); });
  document.getElementById('disposal-status-filter').addEventListener('change', () => { disposalPage = 1; renderDisposal(); });
  document.getElementById('disposal-search').addEventListener('input', () => { disposalPage = 1; renderDisposal(); });
  document.getElementById('disposal-warehouse-filter').addEventListener('change', () => { disposalPage = 1; renderDisposal(); });
  document.getElementById('disposal-date-from').addEventListener('change', () => { disposalPage = 1; renderDisposal(); });
  document.getElementById('disposal-date-to').addEventListener('change', () => { disposalPage = 1; renderDisposal(); });
  document.getElementById('disposal-sort').addEventListener('change', () => { disposalPage = 1; renderDisposal(); });
  document.getElementById('unit-disposal-reason').addEventListener('change', updateDisposalFields);
  document.getElementById('unit-status-select').addEventListener('change', function() {
    document.getElementById('unit-status').value = this.value;
  });
  document.getElementById('qs-target-warehouse').addEventListener('change', function() {
    qsWarehouseManuallyChanged = true;
    validateQuickSell();
  });
  document.getElementById('sold-sku-filter').addEventListener('change', () => { soldPage = 1; renderSold(); });
  document.getElementById('sold-warehouse-filter').addEventListener('change', () => { soldPage = 1; renderSold(); });
  document.getElementById('sold-date-from').addEventListener('change', () => { soldPage = 1; renderSold(); });
  document.getElementById('sold-date-to').addEventListener('change', () => { soldPage = 1; renderSold(); });
  document.getElementById('sold-sort').addEventListener('change', () => { soldPage = 1; renderSold(); });
  document.getElementById('sold-search').addEventListener('input', () => { soldPage = 1; renderSold(); });
  loadCZSettings();
  loadSyncSettings();
  render();
});

async function api(url, opts = {}) {
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  if (!r.ok) {
    let msg = await r.text();
    try { msg = JSON.parse(msg).error || msg; } catch (e) {
      const m = msg.match(/<p>(.*?)<\/p>/);
      if (m) msg = m[1];
    }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, type = 'info') {
  const el = document.getElementById('appToast');
  el.querySelector('.toast-body').textContent = msg;
  el.className = 'toast';
  if (type === 'error') el.classList.add('bg-danger', 'text-white');
  else if (type === 'success') el.classList.add('bg-success', 'text-white');
  else el.classList.add('bg-dark', 'text-white');
  new bootstrap.Toast(el, { delay: 2500 }).show();
}

function fmtDate(d) {
  if (!d) return '';
  const [y, m, dd] = d.split('-');
  return `${dd}.${m}.${y}`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function warehouseBadge(name) {
  const w = cachedWarehouses.find(x => x.name === name);
  if (w && w.color) {
    const hex = w.color;
    return `<span class="badge" style="background:${hex}20;color:${hex};border:1px solid ${hex}40">${esc(name)}</span>`;
  }
  const defaultColors = { 'Озон': '#005BFF', 'Яндекс Маркет': '#FFCC00' };
  if (defaultColors[name]) {
    const hex = defaultColors[name];
    return `<span class="badge" style="background:${hex}20;color:${hex};border:1px solid ${hex}40">${esc(name)}</span>`;
  }
  return `<span class="badge bg-secondary bg-opacity-10 text-secondary">${esc(name)}</span>`;
}

function whOption(w, selected) {
  const tag = w.wh_type === 'virtual' ? ' <small class="text-info">(маркетплейс)</small>' : '';
  const sel = selected == w.id ? ' selected' : '';
  return `<option value="${w.id}"${sel}>${esc(w.name)}${tag}</option>`;
}

function statusBadge(s) {
  const v = ['secondary', 'warning', 'info', 'success', 'primary', 'danger'];
  return `<span class="badge bg-${v[s] || 'secondary'}">${STATUSES[s]}</span>`;
}

function disposalBadge(s) {
  const v = ['secondary', 'warning', 'info', 'success'];
  return `<span class="badge bg-${v[s] || 'secondary'}">${DISPOSAL_STATUSES[s] || '—'}</span>`;
}

function updateDisposalFields() {
  const reason = document.getElementById('unit-disposal-reason').value;
  const isNoDocs = DISPOSAL_REASONS_NO_DOCS.includes(reason);
  const docsEls = document.querySelectorAll('.disposal-docs-required');
  const hintEl = document.getElementById('disposal-hint');
  docsEls.forEach(el => {
    if (isNoDocs) {
      el.style.display = 'none';
      el.querySelectorAll('input, select').forEach(inp => {
        inp.removeAttribute('required');
      });
    } else {
      el.style.display = '';
    }
  });
  if (isNoDocs) {
    hintEl.textContent = 'Для этой причины документы и цена не обязательны. Обязательны: дата и адрес выбытия.';
  } else if (reason === 'eea_sale' || reason === 'export_eaes') {
    hintEl.textContent = 'Для этой причины документы и цена обязательны.';
  } else {
    hintEl.textContent = '';
  }
}

function czStatusBadge(status) {
  if (!status) return '<span class="text-muted">—</span>';
  const ru = CZ_STATUS_MAP[status] || CZ_STATUS_EXT_MAP[status] || status;
  const s = status.toLowerCase();
  if (['introduced', 'introduced_returned'].includes(s))
    return `<span class="badge bg-success">${esc(ru)}</span>`;
  if (['emitted', 'applied', 'reapply'].includes(s))
    return `<span class="badge bg-warning text-dark">${esc(ru)}</span>`;
  if (['retired', 'withdrawn', 'written_off', 'disaggregation', 'disaggregated', 'remark_retired', 'loan_retired'].includes(s))
    return `<span class="badge bg-danger">${esc(ru)}</span>`;
  if (s === 'exported')
    return `<span class="badge bg-info">${esc(ru)}</span>`;
  if (s === 'wait_shipment')
    return `<span class="badge bg-primary">${esc(ru)}</span>`;
  if (s === 'blocked')
    return `<span class="badge bg-dark">${esc(ru)}</span>`;
  return `<span class="badge bg-secondary">${esc(ru)}</span>`;
}

function getDeadlineInfo(soldDate, hasMarking) {
  if (!soldDate) return { urgent: false, warning: false, hint: '', daysLeft: null };
  if (hasMarking === false) return { urgent: false, warning: false, hint: '', daysLeft: null };
  const sold = new Date(soldDate);
  const now = new Date();
  const diffMs = now - sold;
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  const daysLeft = 3 - diffDays;
  if (daysLeft < 0) {
    return { urgent: true, warning: false, hint: `ПРОСРОЧЕНО! Отчёт необходимо был подать ${Math.abs(daysLeft)} дн. назад! Штраф!`, daysLeft };
  }
  if (daysLeft <= 1) {
    return { urgent: false, warning: true, hint: `Осталось ${daysLeft === 0 ? 'менее 1 дня' : '1 день'}! Подайте отчёт — будут штрафы!`, daysLeft };
  }
  return { urgent: false, warning: false, hint: `До дедлайна: ${daysLeft} дн.`, daysLeft };
}

// ============ GLOBAL SEARCH ============
async function globalSearch(e) {
  e.preventDefault();
  const code = document.getElementById('global-search').value.trim();
  if (!code) return;
  const searchCode = stripCZCrypto(code);
  try {
    const data = await api(`/api/units/find-by-code?code=${encodeURIComponent(searchCode)}`);
    if (data.found) {
      document.querySelector('#nav .nav-link[data-tab="stock"]').click();
      setTimeout(() => {
        document.getElementById('stock-search').value = code;
        renderStock();
        toast(`Найдена единица #${data.unit.id}`, 'success');
      }, 200);
    } else {
      toast('Товар с таким кодом ЧЗ не найден', 'error');
    }
  } catch (e) { toast(e.message, 'error'); }
}

// ============ RENDER ============
async function render() {
  await Promise.all([renderDashboard(), renderWarehouses(), renderSkuTable(), renderStock(), renderSold(), renderDisposal(), renderSelects(), renderScanSelects(), renderQuickSell(), renderBackups()]);
}

async function renderDashboard() {
  const data = await api('/api/summary');
  let html = '';
  const stats = [
    { num: data.skus, lbl: 'SKU', icon: 'bi-tags', color: 'primary' },
    { num: data.units, lbl: 'Единиц на складе', icon: 'bi-boxes', color: 'primary' },
    { num: data.marked, lbl: 'Нанесено', icon: 'bi-check-circle', color: 'success' },
    { num: data.unmarked, lbl: 'Без полученного КМ ЧЗ', icon: 'bi-exclamation-circle', color: 'warning' },
    { num: data.returned, lbl: 'Возвращено', icon: 'bi-arrow-counterclockwise', color: 'info' },
    { num: data.sold_units, lbl: 'Продано', icon: 'bi-cart-check', color: 'danger' },
    { num: data.sold_total_price ? data.sold_total_price.toFixed(0) + ' ₽' : '0 ₽', lbl: 'Сумма продаж', icon: 'bi-cash', color: 'success' },
    { num: data.disposal_ready, lbl: 'Готовы к выводу', icon: 'bi-arrow-up-right', color: 'info' },
    { num: data.disposal_sent, lbl: 'Подтверждено в ЧЗ', icon: 'bi-check-circle', color: 'success' },
    { num: data.warehouses, lbl: 'Складов', icon: 'bi-building', color: 'secondary' },
  ];
  stats.forEach(s => {
    html += `<div class="col"><div class="card stat-card"><div class="num text-${s.color}">${s.num}</div><div class="lbl"><i class="bi ${s.icon}"></i> ${s.lbl}</div></div></div>`;
  });
  data.by_warehouse.forEach(w => {
    html += `<div class="col"><div class="card stat-card"><div class="num">${w.count}</div><div class="lbl">${esc(w.name)}</div></div></div>`;
  });
  document.getElementById('summary').innerHTML = html;

  const alertsContainer = document.getElementById('deadline-alerts');
  if (alertsContainer) alertsContainer.remove();
  let alertsHtml = '';
  if (data.deadline_overdue && data.deadline_overdue.length > 0) {
    alertsHtml += `<div class="alert alert-danger"><i class="bi bi-exclamation-triangle-fill"></i> <strong>Просроченные отчёты о выводе из оборота: ${data.deadline_overdue.length} шт.</strong><ul class="mb-0 mt-1">`;
    data.deadline_overdue.forEach(item => {
      alertsHtml += `<li>#${item.id} ${esc(item.sku_name)} (${esc(item.sku_article || '—')}) — продан ${fmtDate(item.sold_date)}</li>`;
    });
    alertsHtml += '</ul></div>';
  }
  if (data.deadline_warning && data.deadline_warning.length > 0) {
    alertsHtml += `<div class="alert alert-warning"><i class="bi bi-exclamation-circle"></i> <strong>Срок подачи отчёта истекает завтра/сегодня: ${data.deadline_warning.length} шт.</strong><ul class="mb-0 mt-1">`;
    data.deadline_warning.forEach(item => {
      alertsHtml += `<li>#${item.id} ${esc(item.sku_name)} (${esc(item.sku_article || '—')}) — продан ${fmtDate(item.sold_date)}</li>`;
    });
    alertsHtml += '</ul></div>';
  }
  if (alertsHtml) {
    const alertsDiv = document.createElement('div');
    alertsDiv.id = 'deadline-alerts';
    alertsDiv.innerHTML = alertsHtml;
    document.getElementById('summary').parentElement.insertAdjacentElement('beforebegin', alertsDiv);
  }

  if (data.by_sku && data.by_sku.length > 0) {
    const totals = data.by_sku.filter(s => s.warehouse_name === '_TOTAL_');
    if (totals.length > 0) {
      let skuHtml = '<div class="card mt-3"><div class="card-body"><h6 class="card-title mb-3"><i class="bi bi-bar-chart"></i> Остатки по товарам</h6><div class="table-responsive"><table class="table table-sm table-hover mb-0" id="sku-dashboard-table"><thead class="table-light"><tr><th>Товар</th><th>Артикул</th><th>ЧЗ</th><th>На складе</th><th>Промарк.</th><th>Продано</th><th>Сумма продаж</th><th>На выводе</th><th>Тираж</th></tr></thead><tbody>';
      totals.forEach(s => {
        const pct = s.edition_total > 0 ? Math.round(s.marked * 100 / s.edition_total) : 0;
        skuHtml += `<tr>
          <td><strong>${esc(s.sku_name)}</strong></td>
          <td><span class="text-primary font-monospace">${esc(s.sku_article || '—')}</span></td>
          <td>${s.has_marking ? '<span class="badge bg-success">Да</span>' : '<span class="badge bg-secondary">Нет</span>'}</td>
          <td>${s.total}</td>
          <td><span class="text-success fw-semibold">${s.marked}</span></td>
          <td>${s.sold || 0}</td>
          <td class="text-success fw-semibold">${s.sold_price ? s.sold_price.toFixed(2) + ' ₽' : '—'}</td>
          <td>${s.in_disposal || 0}</td>
          <td>${s.edition_total > 0 ? `<div class="progress" style="width:100px;height:6px"><div class="progress-bar bg-success" style="width:${pct}%"></div></div> <small>${s.edition_total}</small>` : '—'}</td>
        </tr>`;
      });
      skuHtml += '</tbody></table></div></div></div>';
      const oldTable = document.getElementById('sku-dashboard-table');
      if (oldTable) oldTable.closest('.card').remove();
      document.getElementById('summary').parentElement.insertAdjacentHTML('afterend', skuHtml);
    }
  }

  const tbody = document.querySelector('#recent-table tbody');
  tbody.innerHTML = data.recent.map(u => `
    <tr class="${u.was_returned ? 'unit-returned' : (!u.cz_code ? 'table-warning' : '')}">
      <td><strong class="font-monospace">#${u.id}</strong></td>
      <td>${esc(u.sku_name || '')}</td>
      <td><span class="text-primary fw-semibold font-monospace">${esc(u.sku_article || '—')}</span></td>
      <td>${warehouseBadge(u.warehouse_name)}</td>
      <td>${statusBadge(u.status)}${u.was_returned ? ' <span class="badge bg-danger"><i class="bi bi-arrow-counterclockwise"></i> Возвращено</span>' : ''}</td>
      <td>${esc(u.order_number || '—')}</td>
      <td>${u.cz_code ? '<i class="bi bi-check-circle-fill text-success"></i>' : ''}</td>
      <td>${u.disposal_status ? disposalBadge(u.disposal_status) : '—'}</td>
      <td><button class="btn btn-outline-primary btn-sm" onclick="showUnitDetail(${u.id})"><i class="bi bi-info-circle"></i></button></td>
    </tr>
  `).join('') || '<tr><td colspan="9" class="text-center text-muted">Нет данных</td></tr>';
}

async function renderWarehouses() {
  const list = await api('/api/warehouses');
  cachedWarehouses = list;
  const el = document.getElementById('warehouse-list');
  if (!list.length) { el.innerHTML = '<p class="text-muted">Складов пока нет</p>'; return; }
  el.innerHTML = `<table class="table table-hover align-middle mb-0">
    <thead class="table-light"><tr><th>Название</th><th>Тип</th><th>На складе</th><th>Продано</th><th></th></tr></thead>
    <tbody>${list.map(w => {
      const skuLines = (w.sku_breakdown || []).map(s =>
        `<div class="small text-muted">${esc(s.sku_name)}: <span class="text-success">${s.in_stock}</span> / <span class="text-warning">${s.sold}</span></div>`
      ).join('');
      return `
      <tr>
        <td><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:${esc(w.color || '#6c757d')};margin-right:6px;vertical-align:middle"></span><strong>${esc(w.name)}</strong>${skuLines ? `<div class="mt-1">${skuLines}</div>` : ''}</td>
        <td><span class="badge ${w.wh_type === 'virtual' ? 'bg-info' : 'bg-secondary'}">${w.wh_type === 'virtual' ? 'Виртуальный' : 'Физический'}</span></td>
        <td><span class="badge bg-success">${w.in_stock}</span></td>
        <td><span class="badge bg-warning text-dark">${w.sold}</span></td>
        <td class="text-end">
          <button class="btn btn-sm btn-outline-primary me-1" onclick="openWarehouseModal(${w.id}, '${esc(w.name).replace(/'/g, "\\'")}', '${w.wh_type}', '${esc(w.color || '')}')" title="Изменить"><i class="bi bi-pencil"></i></button>
          <button class="btn btn-sm btn-outline-danger" onclick="deleteWarehouse(${w.id}, '${esc(w.name)}')" title="Удалить"><i class="bi bi-trash"></i></button>
        </td>
      </tr>`;
    }).join('')}</tbody></table>`;
}

async function addWarehouse() {
  const name = document.getElementById('new-warehouse-name').value.trim();
  const wh_type = document.getElementById('new-warehouse-type').value;
  const color = document.getElementById('new-warehouse-color').value;
  if (!name) return;
  try {
    await api('/api/warehouses', { method: 'POST', body: JSON.stringify({ name, wh_type, color }) });
    document.getElementById('new-warehouse-name').value = '';
    render();
  } catch (e) { toast(e.message, 'error'); }
}

let editingWarehouseId = null;
function openWarehouseModal(id, name, wh_type, color) {
  editingWarehouseId = id;
  document.getElementById('warehouse-modal-title').textContent = id ? 'Редактировать склад' : 'Новый склад';
  document.getElementById('wh-name').value = name || '';
  document.getElementById('wh-type').value = wh_type || 'physical';
  document.getElementById('wh-color').value = color || '#0d6efd';
  document.getElementById('wh-color-hex').textContent = color || '#0d6efd';
  document.getElementById('wh-color').oninput = function() { document.getElementById('wh-color-hex').textContent = this.value; };
  new bootstrap.Modal(document.getElementById('warehouse-modal')).show();
}

function closeWarehouseModal() { bootstrap.Modal.getInstance(document.getElementById('warehouse-modal')).hide(); }

async function saveWarehouse() {
  const name = document.getElementById('wh-name').value.trim();
  const wh_type = document.getElementById('wh-type').value;
  const color = document.getElementById('wh-color').value;
  if (!name) { toast('Укажите название', 'error'); return; }
  try {
    if (editingWarehouseId) {
      await api(`/api/warehouses/${editingWarehouseId}`, { method: 'PUT', body: JSON.stringify({ name, wh_type, color }) });
    } else {
      await api('/api/warehouses', { method: 'POST', body: JSON.stringify({ name, wh_type, color }) });
    }
    closeWarehouseModal();
    render();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteWarehouse(id, name) {
  if (!confirm(`Удалить склад «${name}»?\n\nЭто действие нельзя отменить.`)) return;
  try { await api(`/api/warehouses/${id}`, { method: 'DELETE' }); render(); }
  catch (e) { toast(e.message, 'error'); }
}

async function renderSkuTable() {
  const skus = await api('/api/skus');
  const tbody = document.querySelector('#sku-table tbody');
  tbody.innerHTML = skus.map(s => {
    const total = s.total_quantity || 0;
    const marked = s.marked_count || 0;
    const badge = s.has_marking !== false
      ? '<span class="badge bg-primary">ЧЗ</span>'
      : '<span class="badge bg-secondary">Без ЧЗ</span>';
    let progressHtml = '';
    if (s.has_marking !== false && total > 0) {
      const pct = Math.min(100, Math.round(marked * 100 / total));
      const variant = pct === 100 ? 'success' : pct > 0 ? 'warning' : 'secondary';
      progressHtml = `<div class="progress" style="width:100px;height:6px"><div class="progress-bar bg-${variant}" style="width:${pct}%"></div></div> <small class="text-${variant} fw-semibold">${marked}/${total} (${pct}%)</small>`;
    } else if (s.has_marking !== false) {
      progressHtml = `<small class="text-muted">${marked} промарк.</small>`;
    } else {
      const unitCount = (s.total_units || 0);
      progressHtml = `<small class="text-muted">${unitCount} ед.</small>`;
    }
    return `<tr>
      <td class="font-monospace fw-bold">${s.id}</td>
      <td><strong>${esc(s.name)}</strong></td>
      <td><span class="text-primary font-monospace fw-semibold">${esc(s.article || '—')}</span></td>
      <td><code>${esc(s.tnved_code || '—')}</code></td>
      <td><code>${esc(s.gtin14)}</code></td>
      <td>${esc(s.ean13 || '')}</td>
      <td>${fmtDate(s.production_date)}</td>
      <td>${total > 0 ? total : '—'}</td>
      <td><div>${badge}</div><div class="mt-1">${progressHtml}</div></td>
      <td class="text-nowrap">
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary" onclick="openUnitModal(null,${s.id})" title="Добавить единицу"><i class="bi bi-plus"></i></button>
          <button class="btn btn-outline-success" onclick="openBatchModal(${s.id})" title="Создать партию"><i class="bi bi-box-seam"></i></button>
          <button class="btn btn-outline-purple" style="color:#7c3aed;border-color:#7c3aed" onclick="openScanForSku(${s.id})" title="Сканировать"><i class="bi bi-upc-scan"></i></button>
          <button class="btn btn-outline-secondary" onclick="openSkuModal(${s.id})" title="Изменить"><i class="bi bi-pencil"></i></button>
          <button class="btn btn-outline-danger" onclick="deleteSku(${s.id})" title="Удалить"><i class="bi bi-trash"></i></button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="10" class="text-center text-muted">Нет SKU</td></tr>';
}

async function openSkuModal(id) {
  editingSkuId = id || null;
  document.getElementById('sku-modal-title').textContent = id ? 'Редактировать SKU' : 'Новый SKU';
  if (id) {
    const s = (await api('/api/skus')).find(x => x.id === id);
    document.getElementById('sku-name').value = s.name;
    document.getElementById('sku-article').value = s.article || '';
    document.getElementById('sku-tnved').value = s.tnved_code || '';
    document.getElementById('sku-gtin').value = s.gtin14;
    document.getElementById('sku-ean').value = s.ean13 || '';
    document.getElementById('sku-date').value = s.production_date || '';
    document.getElementById('sku-total').value = s.total_quantity || 0;
    document.getElementById('sku-permit').value = s.permit_doc || '';
    document.getElementById('sku-has-marking').checked = s.has_marking !== false;
  } else {
    ['sku-name', 'sku-article', 'sku-tnved', 'sku-gtin', 'sku-ean', 'sku-permit'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('sku-date').value = '';
    document.getElementById('sku-total').value = '0';
    document.getElementById('sku-has-marking').checked = true;
  }
  new bootstrap.Modal(document.getElementById('sku-modal')).show();
}

function closeSkuModal() { bootstrap.Modal.getInstance(document.getElementById('sku-modal')).hide(); }

function toggleMarkingFields() {
  const marking = document.getElementById('sku-has-marking').checked;
  const gtinLabel = document.querySelector('label[for="sku-gtin"]');
  const gtinInput = document.getElementById('sku-gtin');
  if (marking) {
    gtinLabel.innerHTML = 'GTIN-14 *';
    gtinInput.required = true;
  } else {
    gtinLabel.innerHTML = 'GTIN-14';
    gtinInput.required = false;
  }
}

function autoEAN() {
  const g = document.getElementById('sku-gtin').value;
  const ean = document.getElementById('sku-ean');
  if (g.length === 14 && g[0] === '0' && !ean.value) ean.value = g.substring(1);
}

let _tnvedTimer = null;
function openTnvedSearch(e) {
  if (e) e.stopPropagation();
  const input = document.getElementById('sku-tnved');
  const q = input.value.trim();
  if (q.length < 2) { toast('Введите минимум 2 символа', 'error'); return; }
  doTnvedSearch(q);
}
function onTnvedInput() {
  clearTimeout(_tnvedTimer);
  const q = document.getElementById('sku-tnved').value.trim();
  if (q.length >= 2) _tnvedTimer = setTimeout(() => doTnvedSearch(q), 300);
}
async function doTnvedSearch(q) {
  const box = document.getElementById('tnved-results');
  try {
    const items = await api(`/api/tnved/search?q=${encodeURIComponent(q)}&limit=15`);
    if (!items.length) { box.classList.add('d-none'); return; }
    box.innerHTML = items.map(i =>
      `<button type="button" class="list-group-item list-group-item-action py-1" onclick="selectTnved('${esc(i.code)}','${esc(i.name)}')">
        <code>${esc(i.code)}</code> <small class="text-muted">${esc(i.name)}</small>
      </button>`
    ).join('');
    box.classList.remove('d-none');
  } catch(e) { box.classList.add('d-none'); }
}
function selectTnved(code, name) {
  document.getElementById('sku-tnved').value = code;
  document.getElementById('tnved-results').classList.add('d-none');
}

document.addEventListener('click', function(e) {
  const box = document.getElementById('tnved-results');
  if (box && !box.contains(e.target) && e.target.id !== 'sku-tnved') {
    box.classList.add('d-none');
  }
});

async function saveSku() {
  const data = {
    name: document.getElementById('sku-name').value.trim(),
    article: document.getElementById('sku-article').value.trim(),
    tnved_code: document.getElementById('sku-tnved').value.trim(),
    gtin14: document.getElementById('sku-gtin').value.trim(),
    ean13: document.getElementById('sku-ean').value.trim(),
    production_date: document.getElementById('sku-date').value,
    total_quantity: parseInt(document.getElementById('sku-total').value) || 0,
    permit_doc: document.getElementById('sku-permit').value.trim(),
    has_marking: document.getElementById('sku-has-marking').checked
  };
  if (!data.name) { toast('Укажите название', 'error'); return; }
  if (data.has_marking && !data.gtin14) { toast('Для товара с маркировкой укажите GTIN', 'error'); return; }
  try {
    if (editingSkuId) await api(`/api/skus/${editingSkuId}`, { method: 'PUT', body: JSON.stringify(data) });
    else await api('/api/skus', { method: 'POST', body: JSON.stringify(data) });
    closeSkuModal(); render(); toast('Сохранено', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteSku(id) {
  if (!confirm('Удалить SKU и все его единицы?')) return;
  await api(`/api/skus/${id}`, { method: 'DELETE' }); render();
}

async function openBatchModal(presetSkuId) {
  const [skus, warehouses] = await Promise.all([api('/api/skus'), api('/api/warehouses')]);
  document.getElementById('batch-sku').innerHTML = skus.map(s => {
    const remaining = s.total_quantity > 0 ? ` [тираж: ${s.total_quantity}, на складе: ${s.marked_count + s.unmarked_count}]` : '';
    return `<option value="${s.id}">${esc(s.name)} ${s.article ? '(' + esc(s.article) + ')' : ''}${remaining}</option>`;
  }).join('');
  document.getElementById('batch-warehouse').innerHTML = warehouses.map(w => whOption(w)).join('');
  if (presetSkuId) document.getElementById('batch-sku').value = presetSkuId;
  document.getElementById('batch-count').value = 100;
  new bootstrap.Modal(document.getElementById('batch-modal')).show();
}

function closeBatchModal() { bootstrap.Modal.getInstance(document.getElementById('batch-modal')).hide(); }

async function createBatch() {
  const skuId = parseInt(document.getElementById('batch-sku').value);
  const warehouseId = parseInt(document.getElementById('batch-warehouse').value);
  const count = parseInt(document.getElementById('batch-count').value);
  if (!count || count < 1) { toast('Укажите количество', 'error'); return; }
  try {
    const r = await api('/api/units/batch-create', { method: 'POST', body: JSON.stringify({ sku_id: skuId, warehouse_id: warehouseId, count }) });
    closeBatchModal(); render();
    toast(`Создано ${r.created} единиц`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function renderScanSelects() {
  const skus = await api('/api/skus');
  const warehouses = await api('/api/warehouses');
  const scanSku = document.getElementById('scan-sku');
  const scanWh = document.getElementById('scan-warehouse');
  if (scanSku) scanSku.innerHTML = skus.map(s => `<option value="${s.id}">${esc(s.name)} ${s.article ? '(' + esc(s.article) + ')' : ''} [без ЧЗ: ${s.unmarked_count}]</option>`).join('');
  if (scanWh) scanWh.innerHTML = warehouses.map(w => whOption(w)).join('');
}

function openScanForSku(skuId) {
  document.querySelector('#nav .nav-link[data-tab=scan]').click();
  setTimeout(() => {
    document.getElementById('scan-sku').value = skuId;
    document.getElementById('scan-cz').focus();
  }, 100);
}

function handleScanInput() {
  const cz = document.getElementById('scan-cz').value.trim();
  if (cz.length > 20 && cz.includes('\n')) processScan();
}

async function processScan() {
  const cz = document.getElementById('scan-cz').value.trim();
  if (!cz) { toast('Введите код ЧЗ', 'error'); return; }
  const skuId = parseInt(document.getElementById('scan-sku').value);
  const warehouseId = parseInt(document.getElementById('scan-warehouse').value);
  const status = parseInt(document.getElementById('scan-status').value);
  const resultDiv = document.getElementById('scan-result');
  const historyDiv = document.getElementById('scan-history');
  try {
    const r = await api('/api/units/scan', { method: 'POST', body: JSON.stringify({ cz_code: cz, sku_id: skuId, warehouse_id: warehouseId, status }) });
    scanCount++;
    document.getElementById('scan-counter').textContent = scanCount;
    resultDiv.innerHTML = `<div class="alert alert-success"><i class="bi bi-check-circle"></i> Код привязан к единице #${r.unit_id} (${esc(r.sku_name)})</div>`;
    historyDiv.innerHTML = `<div class="scan-history-item text-success">#${scanCount} &rarr; Ед. #${r.unit_id}</div>` + historyDiv.innerHTML;
    document.getElementById('scan-cz').value = '';
    document.getElementById('scan-cz').focus();
    toast(`Отсканировано: ${scanCount}`, 'success');
  } catch (e) {
    resultDiv.innerHTML = `<div class="alert alert-danger"><i class="bi bi-x-circle"></i> ${esc(e.message)}</div>`;
    historyDiv.innerHTML = `<div class="scan-history-item text-danger">#${scanCount + 1} &rarr; Ошибка: ${esc(e.message)}</div>` + historyDiv.innerHTML;
    toast(e.message, 'error');
  }
}

function clearScanHistory() {
  document.getElementById('scan-history').innerHTML = '';
  document.getElementById('scan-result').innerHTML = '';
}

function resetScanCounter() {
  scanCount = 0;
  document.getElementById('scan-counter').textContent = '0';
}

async function checkDuplicate() {
  const cz = document.getElementById('unit-cz').value.trim();
  const statusEl = document.getElementById('cz-check-status');
  const warnEl = document.getElementById('dup-warning');
  const saveBtn = document.getElementById('unit-save-btn');
  if (!cz) {
    statusEl.textContent = '';
    warnEl.classList.add('d-none');
    saveBtn.disabled = false;
    return;
  }
  clearTimeout(czDuplicateCheckTimer);
  czDuplicateCheckTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/api/units/check-duplicate?cz=${encodeURIComponent(cz)}${editingUnitId ? `&exclude_id=${editingUnitId}` : ''}`);
      const data = await r.json();
      if (data.duplicate) {
        statusEl.innerHTML = `<span class="text-danger fw-semibold"><i class="bi bi-exclamation-triangle"></i> Дубликат: ед. #${data.existing_id}</span>`;
        warnEl.classList.remove('d-none');
        saveBtn.disabled = true;
      } else {
        statusEl.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i> Код уникален</span>`;
        warnEl.classList.add('d-none');
        saveBtn.disabled = false;
      }
    } catch (e) { statusEl.textContent = ''; }
  }, 400);
}

async function openUnitModal(id, presetSkuId) {
  editingUnitId = id || null;
  document.getElementById('unit-modal-title').textContent = id ? 'Редактировать единицу' : 'Новая единица';
  document.getElementById('dup-warning').classList.add('d-none');
  document.getElementById('cz-check-status').textContent = '';
  document.getElementById('unit-save-btn').disabled = false;
  const [skus, warehouses] = await Promise.all([api('/api/skus'), api('/api/warehouses')]);
  _unitModalSkus = skus;
  document.getElementById('unit-sku').innerHTML = skus.map(s => `<option value="${s.id}">${esc(s.name)} ${s.article ? '(' + esc(s.article) + ')' : ''} ${s.has_marking === false ? '<span class=\"text-muted\">(без ЧЗ)</span>' : ''}</option>`).join('');
  document.getElementById('unit-loc').innerHTML = warehouses.map(w => whOption(w)).join('');
  if (id) {
    let u;
    try {
      const r = await api(`/api/units/${id}`);
      u = r.unit;
    } catch (e) {}
    if (!u) { toast('Единица не найдена', 'error'); return; }
    document.getElementById('unit-sku').value = u.sku_id;
    onUnitSkuChange();
    document.getElementById('unit-cz').value = normalizeCZ(u.cz_code || '').replace(/^\xe8/, '');
    const czMapped = u.cz_status ? CZ_TO_UNIT_STATUS[u.cz_status] : undefined;
    const effectiveStatus = czMapped !== undefined ? czMapped : u.status;
    document.getElementById('unit-status').value = effectiveStatus;
    document.getElementById('unit-status-select').value = effectiveStatus;
    const soldCheck = document.getElementById('unit-sold-check');
    soldCheck.checked = !!u.sold_date;
    document.getElementById('unit-cz-status-display').innerHTML = u.cz_status
      ? czStatusBadge(u.cz_status) + (u.cz_check_date ? ` <small class="text-muted">(${esc(u.cz_check_date)})</small>` : '')
      : '<span class="text-muted">— не проверен —</span>';
    document.getElementById('unit-cz-status-val').value = u.cz_status || '';
    document.getElementById('unit-sold-date').value = fmtDate(u.sold_date);
    document.getElementById('unit-order').value = u.order_number || '';
    document.getElementById('unit-loc').value = u.warehouse_id;
    document.getElementById('unit-disposal-type').value = u.disposal_type || '';
    document.getElementById('unit-disposal-reason').value = u.disposal_reason || '';
    document.getElementById('unit-disposal-doc-type').value = u.disposal_doc_type || '';
    document.getElementById('unit-disposal-doc-name').value = u.disposal_doc_name || '';
    document.getElementById('unit-disposal-doc-number').value = u.disposal_doc_number || '';
    document.getElementById('unit-disposal-doc-date').value = u.disposal_doc_date || '';
    document.getElementById('unit-disposal-address').value = u.disposal_address || '';
    document.getElementById('unit-disposal-fias-id').value = u.disposal_fias_id || '';
    document.getElementById('unit-disposal-price').value = u.disposal_price || '';
    document.getElementById('unit-disposal-status').value = u.disposal_status || 0;
    setTimeout(checkDuplicate, 100);
    setTimeout(updateDisposalFields, 100);
    setTimeout(toggleUnitSoldFields, 10);
  } else {
    ['unit-cz','unit-status','unit-status-select','unit-sold-date','unit-order','unit-disposal-doc-name','unit-disposal-doc-number','unit-disposal-doc-date','unit-disposal-price'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    document.getElementById('unit-disposal-type').value = 'shipment';
    document.getElementById('unit-disposal-reason').value = 'remote_sale';
    document.getElementById('unit-disposal-doc-type').value = 'прочее';
    document.getElementById('unit-disposal-status').value = '0';
    document.getElementById('unit-sold-check').checked = false;
    document.getElementById('unit-cz-status-display').innerHTML = '<span class="text-muted">— не проверен —</span>';
    document.getElementById('cz-check-status').textContent = '';
    document.getElementById('unit-disposal-address').value = cachedDefaultAddress;
    document.getElementById('unit-disposal-fias-id').value = cachedDefaultFias;
    if (presetSkuId) document.getElementById('unit-sku').value = presetSkuId;
    onUnitSkuChange();
    setTimeout(toggleUnitSoldFields, 10);
  }
  new bootstrap.Modal(document.getElementById('unit-modal')).show();
}

function closeUnitModal() { bootstrap.Modal.getInstance(document.getElementById('unit-modal')).hide(); }

let _unitModalSkus = [];
function onUnitSkuChange() {
  const skuId = parseInt(document.getElementById('unit-sku').value);
  const sku = _unitModalSkus.find(s => s.id === skuId);
  const czFields = document.getElementById('unit-cz-fields');
  const disposalSection = document.getElementById('disposal-section');
  if (sku && sku.has_marking === false) {
    czFields.classList.add('d-none');
    disposalSection.classList.add('d-none');
  } else {
    czFields.classList.remove('d-none');
    disposalSection.classList.remove('d-none');
  }
}

function toggleUnitSoldFields() {
  const soldCheck = document.getElementById('unit-sold-check');
  const statusHidden = document.getElementById('unit-status');
  const statusSelect = document.getElementById('unit-status-select');
  const status = parseInt(statusHidden.value);
  const czStatus = document.getElementById('unit-cz-status-val').value;
  const isRetired = ['RETIRED', 'WITHDRAWN', 'WRITTEN_OFF'].includes(czStatus);
  const els = document.querySelectorAll('.unit-sold-only');
  els.forEach(el => el.style.display = soldCheck.checked ? '' : 'none');
  const disposalSel = document.getElementById('unit-disposal-status');
  if (disposalSel) {
    const opt2 = disposalSel.querySelector('option[value="2"]');
    if (opt2) {
      if (!isRetired) {
        opt2.disabled = true;
        if (parseInt(disposalSel.value) === 2) disposalSel.value = '1';
      } else {
        opt2.disabled = false;
      }
    }
  }
}

function onSoldCheckChange() {
  toggleUnitSoldFields();
}

function pasteCZ() {
  navigator.clipboard.readText().then(t => {
    document.getElementById('unit-cz').value = normalizeCZ(t).replace(/^\xe8/, '');
    checkDuplicate();
  });
}

async function czCheckFromEdit() {
  if (!editingUnitId) { toast('Сначала сохраните единицу', 'error'); return; }
  const btn = document.getElementById('cz-check-btn');
  const statusEl = document.getElementById('cz-check-status');
  const czDisplay = document.getElementById('unit-cz-status-display');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Проверка...';
  try {
    const r = await czCheckSingle(editingUnitId);
    if (r && r.ok) {
      statusEl.innerHTML = `<span class="text-success fw-semibold"><i class="bi bi-check-circle"></i> OK</span>`;
      const czName = CZ_STATUS_MAP[r.cz_status] || CZ_STATUS_EXT_MAP[r.cz_status] || r.cz_status;
      czDisplay.innerHTML = czStatusBadge(r.cz_status) + (r.cz_check_date ? ` <small class="text-muted">(${esc(r.cz_check_date)})</small>` : '');
      document.getElementById('unit-cz-status-val').value = r.cz_status || '';
      if (r.unit_status !== undefined) {
        document.getElementById('unit-status').value = String(r.unit_status);
        document.getElementById('unit-status-select').value = String(r.unit_status);
      }
    } else {
      statusEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> ${r ? (r.error || 'Не найден') : 'Ошибка'}</span>`;
    }
  } catch (e) { statusEl.innerHTML = `<span class="text-danger">${esc(e.message)}</span>`; }
  btn.disabled = false;
  btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Проверить статус ЧЗ';
}

function normalizeCZ(code) {
  const FNC1 = "\xe8";
  const GS = "\u001d";
  code = code.replace(/\\u001d/gi, GS).replace(/\\x1d/gi, GS);
  code = code.replace(/\\u00e8/gi, FNC1).replace(/\\xe8/gi, FNC1);
  code = code.replace(/\u241d/g, GS);
  if (!code) return code;
  if (code[0] !== FNC1 && code[0] !== GS) code = FNC1 + code;
  else if (code[0] === GS) code = FNC1 + code.slice(1);
  code = FNC1 + code.slice(1).replace(/\xe8/g, GS);
  for (const ai of ["91", "92"]) {
    let idx = code.indexOf(ai);
    if (idx > 0 && code[idx - 1] !== GS) {
      code = code.slice(0, idx) + GS + code.slice(idx);
    }
  }
  return code;
}

async function saveUnit() {
  if (document.getElementById('unit-save-btn').disabled) {
    toast('Нельзя сохранить: код ЧЗ уже существует', 'error');
    return;
  }
  const disposalReason = document.getElementById('unit-disposal-reason').value;
  const disposalStatus = parseInt(document.getElementById('unit-disposal-status').value);
  const disposalAddress = document.getElementById('unit-disposal-address').value.trim();
  if (disposalStatus > 0 && disposalReason) {
    if (!disposalAddress) {
      toast('Укажите адрес места выбытия', 'error');
      return;
    }
    const isNoDocs = DISPOSAL_REASONS_NO_DOCS.includes(disposalReason);
    if (!isNoDocs && (disposalReason === 'eea_sale' || disposalReason === 'export_eaes')) {
      const docType = document.getElementById('unit-disposal-doc-type').value;
      const price = document.getElementById('unit-disposal-price').value;
      if (!docType) {
        toast('Укажите вид документа', 'error');
        return;
      }
      if (!price || parseFloat(price) <= 0) {
        toast('Укажите цену за единицу', 'error');
        return;
      }
    }
  }
  const data = {
    sku_id: parseInt(document.getElementById('unit-sku').value),
    cz_code: document.getElementById('unit-cz').value.trim() || null,
    status: document.getElementById('unit-sold-check').checked ? 4 : parseInt(document.getElementById('unit-status-select').value),
    sold_date: parseRuDate(document.getElementById('unit-sold-date').value) || null,
    order_number: document.getElementById('unit-order').value.trim(),
    warehouse_id: parseInt(document.getElementById('unit-loc').value),
    disposal_type: document.getElementById('unit-disposal-type').value || null,
    disposal_reason: document.getElementById('unit-disposal-reason').value || null,
    disposal_doc_type: document.getElementById('unit-disposal-doc-type').value || null,
    disposal_doc_name: document.getElementById('unit-disposal-doc-name').value.trim() || null,
    disposal_doc_number: document.getElementById('unit-disposal-doc-number').value.trim() || null,
    disposal_doc_date: document.getElementById('unit-disposal-doc-date').value || null,
    disposal_address: document.getElementById('unit-disposal-address').value.trim() || null,
    disposal_fias_id: document.getElementById('unit-disposal-fias-id').value.trim() || null,
    disposal_price: parseFloat(document.getElementById('unit-disposal-price').value) || null,
    disposal_status: parseInt(document.getElementById('unit-disposal-status').value),
  };
  try {
    if (editingUnitId) await api(`/api/units/${editingUnitId}`, { method: 'PUT', body: JSON.stringify(data) });
    else await api('/api/units', { method: 'POST', body: JSON.stringify(data) });
    closeUnitModal(); render(); toast('Сохранено', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteUnit(id) {
  if (!confirm('Удалить единицу?')) return;
  await api(`/api/units/${id}`, { method: 'DELETE' }); render();
}

async function moveUnit(id, newWarehouseId) {
  await api(`/api/units/${id}/move`, { method: 'POST', body: JSON.stringify({ warehouse_id: newWarehouseId }) });
  render(); toast('Перемещено', 'success');
}

async function showUnitDetail(id) {
  let u;
  try {
    const r = await api(`/api/units/${id}`);
    u = r.unit;
  } catch (e) { alert('Ошибка загрузки: ' + e.message); return; }
  if (!u) { alert('Единица не найдена: id=' + id); return; }
  const full = normalizeCZ(u.cz_code || '');
  const ozon = full.replace(/\xe8/g, '').replace(/\u001d/g, '\\u001d');
  const turn = full.split('\u001d')[0].replace(/^\xe8/, '');
  let disposalHtml = '';
  if (u.disposal_type && full) {
    disposalHtml = `
      <div class="modal-disposal ${u.disposal_status >= 1 ? 'border-success' : ''}">
        <h6><i class="bi bi-arrow-up-right"></i> Данные для отчёта о выводе из оборота</h6>
        <p class="mb-1"><strong>Тип операции:</strong> ${DISPOSAL_TYPES[u.disposal_type] || u.disposal_type}</p>
        <p class="mb-1"><strong>Причина:</strong> ${DISPOSAL_REASONS[u.disposal_reason] || u.disposal_reason || '—'}</p>
        <p class="mb-1"><strong>Вид документа:</strong> ${esc(u.disposal_doc_type || '—')}</p>
        <p class="mb-1"><strong>Наименование:</strong> ${esc(u.disposal_doc_name || '—')}</p>
        <p class="mb-1"><strong>Номер:</strong> ${esc(u.disposal_doc_number || '—')}</p>
        <p class="mb-1"><strong>Дата:</strong> ${fmtDate(u.disposal_doc_date)}</p>
        <p class="mb-1"><strong>Адрес:</strong> ${esc(u.disposal_address || '(пусто)')}</p>
        <p class="mb-1"><strong>FIAS ID:</strong> ${u.disposal_fias_id ? '<code>' + esc(u.disposal_fias_id) + '</code>' : '—'}</p>
        <p class="mb-1"><strong>Цена:</strong> ${u.disposal_price ? u.disposal_price.toFixed(2) + ' руб' : '—'}</p>
        <p class="mb-0"><strong>Статус отчёта:</strong> ${disposalBadge(u.disposal_status)}</p>
      </div>
    `;
  }
  const html = `
    <p><strong>SKU:</strong> ${esc(u.sku_name)} &middot; <strong>Артикул:</strong> <span class="text-primary font-monospace fw-semibold">${esc(u.sku_article || '—')}</span></p>
    <p><strong>GTIN-14:</strong> <code>${esc(u.gtin14)}</code> &middot; <strong>EAN-13:</strong> ${esc(u.ean13 || '—')}</p>
    ${u.sku_permit_doc ? `<p><strong>Разрешительная документация:</strong> ${esc(u.sku_permit_doc)}</p>` : ''}
    <p><strong>ID:</strong> <span class="font-monospace fw-bold">#${u.id}</span> &middot; <strong>Склад:</strong> ${warehouseBadge(u.warehouse_name)} &middot; <strong>Статус:</strong> ${statusBadge(u.status)}</p>
    ${u.cz_status ? `<p><strong>Статус в ЧЗ:</strong> ${czStatusBadge(u.cz_status)} <small class="text-muted">(${esc(u.cz_check_date || '—')})</small> ${full ? `<button class="btn btn-outline-info btn-sm ms-2" onclick="czCheckSingle(${u.id}).then(r => { if(r && r.ok) showUnitDetail(${u.id}) })"><i class="bi bi-arrow-clockwise"></i> Обновить</button>` : ''}</p>` : (full ? `<p><strong>Статус в ЧЗ:</strong> <button class="btn btn-outline-info btn-sm" onclick="czCheckSingle(${u.id}).then(r => { if(r && r.ok) showUnitDetail(${u.id}) })"><i class="bi bi-arrow-clockwise"></i> Проверить</button></p>` : '')}
    ${u.order_number ? `<p><strong>Номер заказа:</strong> ${esc(u.order_number)}</p>` : ''}
    ${u.sold_date ? `<p><strong>Дата продажи:</strong> ${fmtDate(u.sold_date)}</p>` : ''}
    ${full ? `
      <h6 class="mt-3">Код маркировки (КИЗ) — полный</h6>
      <div class="text-center mb-2"><img src="/api/units/${u.id}/dm-image" alt="DataMatrix КИЗ" style="max-width:200px;border:1px solid #ddd;border-radius:4px" /></div>
      <div class="code-box mb-2">${esc(full).replace(/\xe8/g, '<b class="text-primary">FNC1</b>').replace(/\u001d/g, '<b class="text-danger">GS</b>')}</div>
      <button class="btn btn-outline-secondary btn-sm mb-2" onclick="copyText('${full.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
      <h6>КМ для Маркетплейсов</h6>
      <div class="code-box mb-2">${esc(ozon)}</div>
      <button class="btn btn-outline-secondary btn-sm mb-2" onclick="copyText('${ozon.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
      <h6>КМ для ввода в оборот и вывода из него</h6>
      <div class="code-box mb-2">${esc(turn)}</div>
      <button class="btn btn-outline-secondary btn-sm mb-2" onclick="copyText('${turn.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
    ` : '<p class="text-warning fw-semibold"><i class="bi bi-hourglass-split"></i> Нет кода ЧЗ</p>'}
    ${disposalHtml}
  `;
  document.getElementById('unit-detail-content').innerHTML = html;
  unitDetailModal.show();
}

function copyText(t) {
  navigator.clipboard.writeText(t);
  toast('Скопировано', 'success');
}

// ============ QUICK SELL ============
async function renderQuickSell() {
  const warehouses = await api('/api/warehouses');
  cachedWarehouses = warehouses;
  const sel = document.getElementById('qs-target-warehouse');
  if (sel) {
    sel.innerHTML = warehouses.map(w => whOption(w)).join('');
  }
}

let qsAutoSearchTimer = null;

function stripCZCrypto(code) {
  let c = normalizeCZ(code);
  const gsIdx = c.indexOf('\u001d');
  if (gsIdx > 0) return c.substring(0, gsIdx);
  const idx91 = c.indexOf('91');
  if (idx91 > 0) return c.substring(0, idx91);
  return c;
}

async function handleQuickSellInput() {
  const cz = document.getElementById('qs-cz').value.trim();
  const statusEl = document.getElementById('qs-cz-status');
  const resultDiv = document.getElementById('qs-find-result');
  const targetWhSel = document.getElementById('qs-target-warehouse');
  const whWarnEl = document.getElementById('qs-warehouse-warning');
  clearTimeout(qsAutoSearchTimer);
  if (!cz) {
    qsFoundUnit = null;
    statusEl.textContent = '';
    resultDiv.innerHTML = '';
    qsWarehouseManuallyChanged = false;
    validateQuickSell();
    return;
  }
  statusEl.textContent = 'Поиск...';
  statusEl.className = 'text-muted';
  const searchCode = stripCZCrypto(cz);
  qsAutoSearchTimer = setTimeout(async () => {
    try {
      const warehouses = await api('/api/warehouses');
      cachedWarehouses = warehouses;
      const virtuals = warehouses.filter(w => w.wh_type === 'virtual');

      const data = await api(`/api/units/find-by-code?code=${encodeURIComponent(searchCode)}`);
      if (data.found) {
        qsFoundUnit = data.unit;
        const u = data.unit;
        const full = normalizeCZ(u.cz_code || '');
        const ozonCode = full.replace(/\xe8/g, '').replace(/\u001d/g, '\\u001d');
        const sourceWh = warehouses.find(w => w.id === u.warehouse_id);
        const isPhysical = sourceWh && sourceWh.wh_type === 'physical';

        statusEl.textContent = `Найден: #${u.id} ${u.sku_name}`;
        statusEl.className = 'text-success';

        if (isPhysical && virtuals.length > 0) {
          targetWhSel.innerHTML = virtuals.map(w => whOption(w)).join('');
          if (!qsWarehouseManuallyChanged) {
            targetWhSel.value = virtuals[0].id;
          } else {
            const prevVal = targetWhSel.value;
            if (virtuals.find(w => w.id === parseInt(prevVal))) {
              targetWhSel.value = prevVal;
            } else {
              targetWhSel.value = virtuals[0].id;
            }
          }

          resultDiv.innerHTML = `
            <div class="card mb-3 border-warning">
              <div class="card-body py-2">
                <div class="d-flex justify-content-between align-items-center">
                  <div>
                    <strong>Единица #${u.id}</strong> &middot; ${esc(u.sku_name)} &middot; ${esc(u.sku_article || '—')} &middot; ${warehouseBadge(u.warehouse_name)} &middot; ${statusBadge(u.status)}
                    ${u.cz_status ? ` &middot; ${czStatusBadge(u.cz_status)}` : ''}
                    ${u.order_number ? ` &middot; Заказ: ${esc(u.order_number)}` : ''}
                  </div>
                </div>
                <div class="alert alert-info py-2 px-3 mt-2 mb-2">
                  <i class="bi bi-info-circle"></i> Товар на складе <strong>${esc(u.warehouse_name)}</strong>.
                  Код маркировки будет перенесён на выбранный маркетплейс перед продажей.
                  <br><small class="text-muted">Выберите маркетплейс для продажи:</small>
                </div>
                <div class="mt-2">
                  <small class="fw-semibold">КМ для Маркетплейсов:</small>
                  <div class="code-box mb-1" style="font-size:11px">${esc(ozonCode)}</div>
                  <button class="btn btn-outline-primary btn-sm" onclick="copyText('${ozonCode.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
                </div>
              </div>
            </div>
          `;
        } else if (!isPhysical && virtuals.length > 1) {
          const otherVirtuals = virtuals.filter(w => w.id !== u.warehouse_id);
          if (otherVirtuals.length > 0) {
            targetWhSel.innerHTML = virtuals.map(w => whOption(w, qsWarehouseManuallyChanged ? undefined : u.warehouse_id)).join('');
            if (!qsWarehouseManuallyChanged) {
              targetWhSel.value = u.warehouse_id;
            }
            resultDiv.innerHTML = `
              <div class="card mb-3 border-success">
                <div class="card-body py-2">
                  <div class="d-flex justify-content-between align-items-center">
                    <div>
                      <strong>Единица #${u.id}</strong> &middot; ${esc(u.sku_name)} &middot; ${esc(u.sku_article || '—')} &middot; ${warehouseBadge(u.warehouse_name)} &middot; ${statusBadge(u.status)}
                      ${u.cz_status ? ` &middot; ${czStatusBadge(u.cz_status)}` : ''}
                      ${u.order_number ? ` &middot; Заказ: ${esc(u.order_number)}` : ''}
                    </div>
                  </div>
                  <div class="mt-2">
                    <small class="fw-semibold">КМ для Маркетплейсов:</small>
                    <div class="code-box mb-1" style="font-size:11px">${esc(ozonCode)}</div>
                    <button class="btn btn-outline-primary btn-sm" onclick="copyText('${ozonCode.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
                  </div>
                </div>
              </div>
            `;
          } else {
            targetWhSel.innerHTML = warehouses.map(w => whOption(w, u.warehouse_id)).join('');
            resultDiv.innerHTML = `
              <div class="card mb-3 border-success">
                <div class="card-body py-2">
                  <div>
                    <strong>Единица #${u.id}</strong> &middot; ${esc(u.sku_name)} &middot; ${esc(u.sku_article || '—')} &middot; ${warehouseBadge(u.warehouse_name)} &middot; ${statusBadge(u.status)}
                    ${u.cz_status ? ` &middot; ${czStatusBadge(u.cz_status)}` : ''}
                  </div>
                </div>
              </div>
            `;
          }
        } else {
          targetWhSel.innerHTML = warehouses.map(w => whOption(w)).join('');
          targetWhSel.value = u.warehouse_id;

          resultDiv.innerHTML = `
            <div class="card mb-3 border-success">
              <div class="card-body py-2">
                <div class="d-flex justify-content-between align-items-center">
                  <div>
                    <strong>Единица #${u.id}</strong> &middot; ${esc(u.sku_name)} &middot; ${esc(u.sku_article || '—')} &middot; ${warehouseBadge(u.warehouse_name)} &middot; ${statusBadge(u.status)}
                    ${u.cz_status ? ` &middot; ${czStatusBadge(u.cz_status)}` : ''}
                    ${u.order_number ? ` &middot; Заказ: ${esc(u.order_number)}` : ''}
                  </div>
                </div>
                <div class="mt-2">
                  <small class="fw-semibold">КМ для Маркетплейсов:</small>
                  <div class="code-box mb-1" style="font-size:11px">${esc(ozonCode)}</div>
                  <button class="btn btn-outline-primary btn-sm" onclick="copyText('${ozonCode.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
                </div>
              </div>
            </div>
          `;
        }
      } else {
        qsFoundUnit = null;
        statusEl.textContent = 'Товар не найден';
        statusEl.className = 'text-danger';
        resultDiv.innerHTML = '<div class="alert alert-warning py-2 mb-0"><i class="bi bi-exclamation-triangle"></i> Товар с таким кодом ЧЗ не найден на складе</div>';
      }
    } catch (e) {
      qsFoundUnit = null;
      statusEl.textContent = 'Ошибка поиска';
      statusEl.className = 'text-danger';
      resultDiv.innerHTML = `<div class="alert alert-danger py-2 mb-0">${esc(e.message)}</div>`;
    }
    validateQuickSell();
  }, 500);
}

function validateQuickSell() {
  const cz = document.getElementById('qs-cz').value.trim();
  const order = document.getElementById('qs-order').value.trim();
  const price = document.getElementById('qs-price').value;
  const targetWh = parseInt(document.getElementById('qs-target-warehouse').value);
  const btn = document.getElementById('qs-sell-btn');
  const whWarnEl = document.getElementById('qs-warehouse-warning');

  if (qsFoundUnit && targetWh && qsFoundUnit.warehouse_id !== targetWh) {
    const targetWhObj = cachedWarehouses.find(w => w.id === targetWh);
    const isToVirtual = targetWhObj && targetWhObj.wh_type === 'virtual';

    if (!isToVirtual) {
      const targetName = targetWhObj ? targetWhObj.name : '';
      whWarnEl.innerHTML = `<i class="bi bi-exclamation-octagon"></i> <strong>Продажа возможна только на виртуальный склад!</strong> Выберите маркетплейс для продажи.`;
      whWarnEl.classList.remove('d-none');
      btn.disabled = true;
      return;
    }
  }
  whWarnEl.classList.add('d-none');
  const hasInput = cz || document.getElementById('qs-no-marking').value.trim();
  const valid = qsFoundUnit && hasInput && order && price && parseFloat(price) >= 0;
  btn.disabled = !valid;
}

let _noMarkingTimer = null;
async function handleNoMarkingInput() {
  const input = document.getElementById('qs-no-marking').value.trim();
  const statusEl = document.getElementById('qs-no-marking-status');
  const resultDiv = document.getElementById('qs-find-result');
  const targetWhSel = document.getElementById('qs-target-warehouse');
  clearTimeout(_noMarkingTimer);
  if (!input) {
    qsFoundUnit = null;
    statusEl.textContent = '';
    resultDiv.innerHTML = '';
    validateQuickSell();
    return;
  }
  statusEl.textContent = 'Поиск...';
  statusEl.className = 'text-muted';
  _noMarkingTimer = setTimeout(async () => {
    try {
      const warehouses = await api('/api/warehouses');
      cachedWarehouses = warehouses;
      const virtuals = warehouses.filter(w => w.wh_type === 'virtual');
      const allSkus = await api('/api/skus');
      const q = input.toLowerCase();
      const foundSku = allSkus.find(s => s.has_marking === false && (
        (s.article && s.article.toLowerCase() === q) ||
        (s.ean13 && s.ean13 === input) ||
        s.name.toLowerCase().includes(q)
      ));
      if (!foundSku) {
        qsFoundUnit = null;
        statusEl.textContent = 'Товар без маркировки не найден';
        statusEl.className = 'text-danger';
        resultDiv.innerHTML = '';
        validateQuickSell();
        return;
      }
      const existingUnits = await api(`/api/units?sku_id=${foundSku.id}&per_page=500`);
      const availableUnit = existingUnits.units.find(u => u.status !== 4 && u.status !== 5);
      if (!availableUnit) {
        qsFoundUnit = null;
        statusEl.textContent = `Товар найден (${foundSku.name}), но нет единиц на складе`;
        statusEl.className = 'text-warning';
        resultDiv.innerHTML = '';
        validateQuickSell();
        return;
      }
      qsFoundUnit = availableUnit;
      qsFoundUnit._is_no_marking = true;
      const u = availableUnit;
      statusEl.textContent = `Найден: #${u.id} ${u.sku_name}`;
      statusEl.className = 'text-info';
      resultDiv.innerHTML = `
        <div class="card mb-3 border-info">
          <div class="card-body py-2">
            <strong>Единица #${u.id}</strong> &middot; ${esc(u.sku_name)} &middot; ${esc(u.sku_article || '—')} &middot; ${warehouseBadge(u.warehouse_name)}
            <div class="alert alert-info py-2 px-3 mt-2 mb-0">
              <i class="bi bi-info-circle"></i> Товар <strong>без маркировки</strong>. Выбытие в ЧЗ не потребуется.
            </div>
          </div>
        </div>
      `;
      if (virtuals.length > 0) {
        targetWhSel.innerHTML = virtuals.map(w => whOption(w)).join('');
      }
      validateQuickSell();
    } catch(e) {
      statusEl.textContent = 'Ошибка поиска';
      statusEl.className = 'text-danger';
    }
  }, 300);
}

let qsWarehouseManuallyChanged = false;

async function processQuickSell() {
  if (document.getElementById('qs-sell-btn').disabled) return;
  const cz = document.getElementById('qs-cz').value.trim();
  const targetWh = parseInt(document.getElementById('qs-target-warehouse').value);
  const orderNumber = document.getElementById('qs-order').value.trim();
  const price = document.getElementById('qs-price').value;
  const resultDiv = document.getElementById('qs-result');

  const u = qsFoundUnit;
  const sourceWh = cachedWarehouses.find(w => w.id === u.warehouse_id);
  const targetWhObj = cachedWarehouses.find(w => w.id === targetWh);
  const willTransfer = targetWhObj && targetWhObj.wh_type === 'virtual' && u.warehouse_id !== targetWh;

  let html = `<div class="row g-3">`;
  html += `<div class="col-md-6"><div class="card border-primary"><div class="card-body">`;
  html += `<h6 class="text-primary"><i class="bi bi-info-circle"></i> Товар</h6>`;
  html += `<table class="table table-sm mb-0"><tbody>`;
  html += `<tr><td class="fw-semibold">Единица</td><td><span class="font-monospace fw-bold">#${u.id}</span></td></tr>`;
  html += `<tr><td class="fw-semibold">SKU</td><td>${esc(u.sku_name)}</td></tr>`;
  html += `<tr><td class="fw-semibold">Артикул</td><td>${esc(u.sku_article || '—')}</td></tr>`;
  html += `<tr><td class="fw-semibold">Код ЧЗ</td><td><small class="font-monospace">${esc((normalizeCZ(u.cz_code || '').replace(/^\xe8/, '')).substring(0, 40))}${normalizeCZ(u.cz_code || '').length > 40 ? '...' : ''}</small></td></tr>`;
  if (u.cz_status) html += `<tr><td class="fw-semibold">Статус ЧЗ</td><td>${czStatusBadge(u.cz_status)}</td></tr>`;
  html += `</tbody></table></div></div></div>`;

  html += `<div class="col-md-6"><div class="card border-warning"><div class="card-body">`;
  html += `<h6 class="text-warning"><i class="bi bi-truck"></i> Склад и продажа</h6>`;
  html += `<table class="table table-sm mb-0"><tbody>`;
  html += `<tr><td class="fw-semibold">Текущий склад</td><td>${warehouseBadge(u.warehouse_name)}</td></tr>`;
  html += `<tr><td class="fw-semibold">Склад продажи</td><td>${warehouseBadge(targetWhObj ? targetWhObj.name : u.warehouse_name)}</td></tr>`;
  if (willTransfer) {
    html += `<tr><td class="fw-semibold">Остаток на маркетплейсе</td><td><span id="qs-confirm-virtual-count">...</span></td></tr>`;
  }
  html += `<tr><td class="fw-semibold">Номер заказа</td><td><strong>${esc(orderNumber)}</strong></td></tr>`;
  html += `<tr><td class="fw-semibold">Цена</td><td><strong class="text-success">${parseFloat(price).toFixed(2)} ₽</strong></td></tr>`;
  html += `</tbody></table></div></div></div>`;
  html += `</div>`;

  if (willTransfer) {
    const skuId = u.sku_id;
    api(`/api/units?sku_id=${skuId}&warehouse_id=${targetWh}`).then(d => {
      const countEl = document.getElementById('qs-confirm-virtual-count');
      if (countEl) {
        const cnt = d.total || 0;
        countEl.innerHTML = `<strong>${cnt}</strong> ед.`;
        if (cnt > 0) {
          countEl.innerHTML += ` <span class="badge bg-warning text-dark">−1 удалится перед продажей</span>`;
        } else {
          countEl.innerHTML += ` <span class="badge bg-info">пусто — товар будет создан</span>`;
        }
      }
    });
    html += `<div class="alert alert-info mt-3 mb-0"><i class="bi bi-arrow-left-right"></i> Код маркировки будет перенесён со склада «${esc(u.warehouse_name)}» на «${esc(targetWhObj.name)}».</div>`;
  }

  document.getElementById('sell-confirm-body').innerHTML = html;
  new bootstrap.Modal(document.getElementById('sell-confirm-modal')).show();
}

function closeSellConfirmModal() {
  const modal = bootstrap.Modal.getInstance(document.getElementById('sell-confirm-modal'));
  if (modal) modal.hide();
}

async function confirmQuickSell() {
  const btn = document.getElementById('sell-confirm-btn');
  btn.disabled = true;
  btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Обработка...';

  const cz = document.getElementById('qs-cz').value.trim();
  const targetWh = parseInt(document.getElementById('qs-target-warehouse').value);
  const orderNumber = document.getElementById('qs-order').value.trim();
  const price = document.getElementById('qs-price').value;
  const resultDiv = document.getElementById('qs-result');

  try {
    let r;
    // Товар без маркировки — другой endpoint
    if (qsFoundUnit && qsFoundUnit._is_no_marking) {
      const payload = { sku_id: qsFoundUnit.sku_id };
      if (targetWh) payload.target_warehouse_id = targetWh;
      if (orderNumber) payload.order_number = orderNumber;
      if (price) payload.disposal_price = parseFloat(price);
      r = await api('/api/units/sell-no-marking', { method: 'POST', body: JSON.stringify(payload) });
    } else {
      const payload = { cz_code: cz };
      if (targetWh) payload.target_warehouse_id = targetWh;
      if (orderNumber) payload.order_number = orderNumber;
      if (price) payload.disposal_price = parseFloat(price);
      r = await api('/api/units/quick-sell', { method: 'POST', body: JSON.stringify(payload) });
    }
    const u = r.unit;
    const full = normalizeCZ(u.cz_code || '');
    const ozonCode = full.replace(/\xe8/g, '').replace(/\u001d/g, '\\u001d');
    const deadline = getDeadlineInfo(u.sold_date, u.has_marking);

    closeSellConfirmModal();

    resultDiv.innerHTML = `
      <div class="alert alert-success">
        <i class="bi bi-check-circle"></i> <strong>${esc(r.message)}</strong><br>
        Единица #${u.id} &middot; ${esc(u.sku_name || '')} &middot; ${warehouseBadge(u.warehouse_name)}
        ${r.transferred ? '<br><span class="badge bg-info mt-1"><i class="bi bi-arrow-left-right"></i> Код автоматически перенесён с физического склада на виртуальный</span>' : ''}
      </div>
      <div class="card mt-2"><div class="card-body">
        <h6><i class="bi bi-upc-scan"></i> КМ для Маркетплейсов (вставьте при сборке)</h6>
        <div class="code-box mb-2" style="font-size:12px">${esc(ozonCode)}</div>
        <button class="btn btn-outline-primary btn-sm" onclick="copyText('${ozonCode.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
        ${deadline.urgent ? `<div class="alert alert-danger mt-2 mb-0"><i class="bi bi-exclamation-triangle"></i> ${deadline.hint}</div>` : ''}
        ${deadline.warning ? `<div class="alert alert-warning mt-2 mb-0"><i class="bi bi-exclamation-circle"></i> ${deadline.hint}</div>` : ''}
      </div></div>
    `;
    document.getElementById('qs-cz').value = '';
    document.getElementById('qs-order').value = '';
    document.getElementById('qs-price').value = '';
    document.getElementById('qs-cz-status').textContent = '';
    document.getElementById('qs-find-result').innerHTML = '';
    qsFoundUnit = null;
    qsWarehouseManuallyChanged = false;
    validateQuickSell();
    toast('Продажа оформлена', 'success');
    render();
  } catch (e) {
    resultDiv.innerHTML = `<div class="alert alert-danger"><i class="bi bi-x-circle"></i> ${esc(e.message)}</div>`;
    toast(e.message, 'error');
  }

  btn.disabled = false;
  btn.innerHTML = '<i class="bi bi-lightning"></i> Продать';
}

async function findUnitByCode() {
  const code = document.getElementById('qs-find-code').value.trim();
  if (!code) { toast('Введите код', 'error'); return; }
  const resultDiv = document.getElementById('qs-find-result');
  const statusEl = document.getElementById('qs-cz-status');
  const searchCode = stripCZCrypto(code);
  try {
    const data = await api(`/api/units/find-by-code?code=${encodeURIComponent(searchCode)}`);
    if (!data.found) {
      qsFoundUnit = null;
      resultDiv.innerHTML = '<div class="alert alert-warning py-2">Товар не найден</div>';
      statusEl.textContent = '';
      validateQuickSell();
      return;
    }
    const u = data.unit;
    const isNoMarking = !u.cz_code;
    qsFoundUnit = u;
    if (isNoMarking) qsFoundUnit._is_no_marking = true;
    document.getElementById('qs-cz').value = u.cz_code || u.ean13 || u.sku_article || '';
    statusEl.textContent = isNoMarking ? `Найден (без ЧЗ): #${u.id} ${u.sku_name}` : `Найден: #${u.id} ${u.sku_name}`;
    statusEl.className = isNoMarking ? 'text-info' : 'text-success';

    let czSection = '';
    if (!isNoMarking) {
      const full = normalizeCZ(u.cz_code || '');
      const ozonCode = full.replace(/\xe8/g, '').replace(/\u001d/g, '\\u001d');
      czSection = `
        <div class="mt-2">
          <small class="fw-semibold">КМ для Маркетплейсов:</small>
          <div class="code-box mb-1" style="font-size:11px">${esc(ozonCode)}</div>
          <button class="btn btn-outline-primary btn-sm" onclick="copyText('${ozonCode.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')"><i class="bi bi-clipboard"></i> Копировать</button>
        </div>`;
    }

    resultDiv.innerHTML = `
      <div class="card mt-2 border-${isNoMarking ? 'info' : 'success'}">
        <div class="card-body py-2">
          <h6>${isNoMarking ? 'Единица без маркировки' : 'Найдена единица'} #${u.id}</h6>
          <p class="mb-1"><strong>SKU:</strong> ${esc(u.sku_name)} &middot; <strong>Артикул:</strong> ${esc(u.sku_article || '—')}</p>
          <p class="mb-1"><strong>Склад:</strong> ${warehouseBadge(u.warehouse_name)} &middot; <strong>Статус:</strong> ${statusBadge(u.status)}</p>
          ${isNoMarking ? '<div class="alert alert-info py-1 px-2 mt-1 mb-0 small"><i class="bi bi-info-circle"></i> Без маркировки — отчёт о выбытии не потребуется</div>' : ''}
          ${u.cz_status ? `<p class="mb-1"><strong>Статус в ЧЗ:</strong> ${czStatusBadge(u.cz_status)}</p>` : ''}
          ${u.order_number ? `<p class="mb-1"><strong>Заказ:</strong> ${esc(u.order_number)}</p>` : ''}
          ${u.sold_date ? `<p class="mb-1"><strong>Дата продажи:</strong> ${fmtDate(u.sold_date)}</p>` : ''}
          ${czSection}
          <div class="d-flex gap-2 mt-2">
            <button class="btn btn-sm btn-outline-primary" onclick="showUnitDetail(${u.id})"><i class="bi bi-info-circle"></i> Подробнее</button>
            <button class="btn btn-sm btn-outline-secondary" onclick="openUnitModal(${u.id})"><i class="bi bi-pencil"></i> Изменить</button>
          </div>
        </div>
      </div>
    `;
    validateQuickSell();
  } catch (e) {
    resultDiv.innerHTML = `<div class="alert alert-danger">${esc(e.message)}</div>`;
  }
}

// ============ SORTING ============
function updateSortHeaders() {
  document.querySelectorAll('#stock-table th.sortable').forEach(th => {
    th.classList.toggle('active', th.dataset.sort === stockSort.field);
  });
}

function toggleSort(field) {
  if (stockSort.field === field) stockSort.dir = stockSort.dir === 'asc' ? 'desc' : 'asc';
  else { stockSort.field = field; stockSort.dir = 'asc'; }
  stockPage = 1;
  updateSortHeaders();
  renderStock();
}

async function renderStock() {
  const warehouses = await api('/api/warehouses');
  const skus = await api('/api/skus');
  const locFilter = document.getElementById('stock-filter');
  const curLoc = locFilter.value;
  locFilter.innerHTML = '<option value="all">Все склады</option>' + warehouses.map(w => whOption(w)).join('');
  if (curLoc) locFilter.value = curLoc;

  const skuFilter = document.getElementById('stock-sku-filter');
  const curSku = skuFilter.value;
  skuFilter.innerHTML = '<option value="all">Все SKU</option>' + skus.map(s => `<option value="${s.id}">${esc(s.name)} ${s.article ? '(' + esc(s.article) + ')' : ''}</option>`).join('');
  if (curSku) skuFilter.value = curSku;

  const params = new URLSearchParams();
  if (locFilter.value !== 'all') params.set('warehouse_id', locFilter.value);
  if (skuFilter.value !== 'all') params.set('sku_id', skuFilter.value);
  const st = document.getElementById('stock-status-filter').value;
  if (st !== 'all') params.set('status', st);
  if (document.getElementById('stock-no-cz').checked) params.set('no_cz', '1');
  const q = document.getElementById('stock-search').value;
  if (q) params.set('q', q);
  params.set('sort', stockSort.field);
  params.set('order', stockSort.dir);
  params.set('page', stockPage);
  params.set('per_page', PER_PAGE);

  const data = await api('/api/units?' + params.toString());
  const units = data.units;
  const total = data.total;
  const totalPages = Math.ceil(total / PER_PAGE);
  const tbody = document.querySelector('#stock-table tbody');
  tbody.innerHTML = units.map(u => {
    const moveOpts = warehouses.filter(w => w.id !== u.warehouse_id).map(w => whOption(w)).join('');
    return `<tr class="${u.was_returned ? 'unit-returned' : (!u.cz_code ? 'table-warning' : '')}">
      <td class="font-monospace fw-bold">#${u.id}</td>
      <td>${esc(u.sku_name || '')}<br><small class="text-muted"><code>${esc(u.gtin14 || '')}</code></small></td>
      <td><span class="text-primary font-monospace fw-semibold">${esc(u.sku_article || '—')}</span></td>
      <td>${warehouseBadge(u.warehouse_name)}</td>
      <td>${statusBadge(u.status)}${u.was_returned ? ' <span class="badge bg-danger"><i class="bi bi-arrow-counterclockwise"></i> Возвраты</span>' : ''}</td>
      <td>${u.cz_code ? `<div class="code-box" style="font-size:10px">${esc(normalizeCZ(u.cz_code).replace(/\xe8/g, '')).replace(/\u001d/g, '<b class="text-danger">␝</b>')}</div>` : '<span class="text-warning"><i class="bi bi-hourglass-split"></i> нет ЧЗ</span>'}</td>
      <td class="text-nowrap">
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary" onclick="showUnitDetail(${u.id})" title="Подробнее"><i class="bi bi-info-circle"></i></button>
          <button class="btn btn-outline-secondary" onclick="openUnitModal(${u.id})" title="Изменить"><i class="bi bi-pencil"></i></button>
          <button class="btn btn-outline-info" onclick="openLabelsForUnit(${u.sku_id},${u.id})" title="Этикетка"><i class="bi bi-tag"></i></button>
          <select class="form-select form-select-sm" style="width:auto" onchange="if(this.value){moveUnit(${u.id},parseInt(this.value));this.value=''}">
            <option value="">Переместить…</option>${moveOpts}
          </select>
          <button class="btn btn-outline-danger" onclick="deleteUnit(${u.id})" title="Удалить"><i class="bi bi-trash"></i></button>
        </div>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="text-center text-muted">Нет единиц</td></tr>';

  renderPagination('stock', stockPage, totalPages, total, 'stockPage');
}

async function renderSold() {
  const [skus, warehouses] = await Promise.all([api('/api/skus'), api('/api/warehouses')]);
  const skuFilter = document.getElementById('sold-sku-filter');
  const whFilter = document.getElementById('sold-warehouse-filter');
  const curSku = skuFilter.value;
  const curWh = whFilter.value;
  skuFilter.innerHTML = '<option value="all">Все</option>' + skus.map(s => `<option value="${s.id}">${esc(s.name)} ${s.article ? '(' + esc(s.article) + ')' : ''}</option>`).join('');
  whFilter.innerHTML = '<option value="all">Все</option>' + warehouses.map(w => whOption(w)).join('');
  if (curSku) skuFilter.value = curSku;
  if (curWh) whFilter.value = curWh;

  const params = new URLSearchParams();
  if (skuFilter.value !== 'all') params.set('sku_id', skuFilter.value);
  if (whFilter.value !== 'all') params.set('warehouse_id', whFilter.value);
  const dateFrom = parseRuDate(document.getElementById('sold-date-from').value);
  const dateTo = parseRuDate(document.getElementById('sold-date-to').value);
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  params.set('sort', document.getElementById('sold-sort').value);
  const q = document.getElementById('sold-search').value;
  if (q) params.set('q', q);
  params.set('page', soldPage);
  params.set('per_page', PER_PAGE);

  const data = await api('/api/units/sold?' + params.toString());
  document.getElementById('sold-count').textContent = data.count;
  document.getElementById('sold-total-price').textContent = data.total_price ? data.total_price.toFixed(2) + ' ₽' : '0 ₽';
  const totalPages = Math.ceil(data.count / PER_PAGE);

  const tbody = document.querySelector('#sold-table tbody');
  tbody.innerHTML = data.units.map(u => {
    const deadline = getDeadlineInfo(u.sold_date, u.has_marking);
    let deadlineBadge = '';
    if (u.has_marking && u.disposal_status === 0 && u.sold_date) {
      if (deadline.urgent) deadlineBadge = `<span class="badge bg-danger"><i class="bi bi-exclamation-triangle"></i> Просрочено!</span>`;
      else if (deadline.warning) deadlineBadge = `<span class="badge bg-warning text-dark"><i class="bi bi-clock"></i> Сгорает!</span>`;
      else deadlineBadge = `<span class="badge bg-success"><i class="bi bi-clock"></i> ${deadline.daysLeft} дн.</span>`;
    }
    return `
    <tr class="${u.has_marking && u.disposal_status === 0 && deadline.urgent ? 'table-danger' : ''}">
      <td class="font-monospace fw-bold">#${u.id}</td>
      <td>${esc(u.sku_name || '')}</td>
      <td><span class="text-primary font-monospace">${esc(u.sku_article || '—')}</span></td>
      <td>${warehouseBadge(u.warehouse_name)}</td>
      <td>${esc(u.order_number || '—')}</td>
      <td>${fmtDate(u.sold_date)} ${deadlineBadge}</td>
      <td class="text-success fw-semibold">${u.disposal_price ? u.disposal_price.toFixed(2) + ' ₽' : '—'}</td>
      <td>${u.sold_date ? '<i class="bi bi-check-square-fill text-success"></i>' : '<i class="bi bi-square text-muted"></i>'}</td>
      <td>${u.has_marking ? (u.cz_status ? czStatusBadge(u.cz_status) : '<span class="text-muted">—</span>') : '<span class="text-muted">—</span>'}</td>
      <td>${u.has_marking ? disposalBadge(u.disposal_status) : '<span class="text-muted">—</span>'}</td>
      <td>${u.cz_code ? `<div class="code-box" style="font-size:10px;max-width:150px">${esc((normalizeCZ(u.cz_code).replace(/^\xe8/, '')).split('\u001d')[0])}</div>` : '—'}</td>
      <td class="text-nowrap">
        <button class="btn btn-outline-primary btn-sm" onclick="showUnitDetail(${u.id})" title="Подробнее"><i class="bi bi-info-circle"></i></button>
        <button class="btn btn-outline-secondary btn-sm" onclick="openUnitModal(${u.id})" title="Изменить"><i class="bi bi-pencil"></i></button>
        <button class="btn btn-outline-info btn-sm" onclick="openLabelsForUnit(${u.sku_id},${u.id})" title="Этикетка"><i class="bi bi-tag"></i></button>
        ${u.status === 5 && u.disposal_status === 3 ? `<button class="btn btn-outline-warning btn-sm" onclick="openReturnModal(${u.id})" title="Вернуть на склад"><i class="bi bi-arrow-counterclockwise"></i></button>` : ''}
        <button class="btn btn-outline-danger btn-sm" onclick="openDeleteSaleModal(${u.id})" title="Удалить продажу"><i class="bi bi-trash"></i></button>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="12" class="text-center text-muted">Нет проданных товаров</td></tr>';

  renderPagination('sold', soldPage, totalPages, data.count, 'soldPage');
}

async function openReturnModal(unitId) {
  document.getElementById('return-unit-id').value = unitId;
  const warehouses = await api('/api/warehouses');
  document.getElementById('return-warehouse').innerHTML = warehouses.map(w => whOption(w)).join('');
  new bootstrap.Modal(document.getElementById('return-modal')).show();
}

function showReturnConfirm() {}

async function processReturn() {
  const unitId = document.getElementById('return-unit-id').value;
  const warehouseId = document.getElementById('return-warehouse').value;
  try {
    const r = await api(`/api/units/${unitId}`);
    if (r.unit.status !== 3) {
      toast('Сначала подайте отчёт о возврате в ЛК Честный Знак', 'error');
      return;
    }
  } catch (e) { toast('Ошибка проверки статуса', 'error'); return; }
  if (!confirm('Вернуть товар на склад?')) return;
  try {
    const r = await api(`/api/units/${unitId}/return`, { method: 'POST', body: JSON.stringify({ warehouse_id: parseInt(warehouseId) }) });
    bootstrap.Modal.getInstance(document.getElementById('return-modal')).hide();
    toast(r.message, 'warning');
    renderSold();
    renderDisposal();
    renderStock();
    renderDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

function openDeleteSaleModal(unitId) {
  document.getElementById('delete-sale-unit-id').value = unitId;
  new bootstrap.Modal(document.getElementById('delete-sale-modal')).show();
}

async function processDeleteSale() {
  const unitId = document.getElementById('delete-sale-unit-id').value;
  try {
    const r = await api(`/api/units/${unitId}/delete-sale`, { method: 'POST' });
    bootstrap.Modal.getInstance(document.getElementById('delete-sale-modal')).hide();
    toast(r.message, 'warning');
    renderSold();
    renderDisposal();
    renderStock();
    renderDashboard();
  } catch (e) { toast(e.message, 'error'); }
}

async function renderDisposal() {
  const warehouses = await api('/api/warehouses');
  const whFilter = document.getElementById('disposal-warehouse-filter');
  const curWh = whFilter.value;
  whFilter.innerHTML = '<option value="all">Все</option>' + warehouses.map(w => whOption(w)).join('');
  if (curWh && curWh !== 'all') whFilter.value = curWh;

  const params = new URLSearchParams();
  if (whFilter.value !== 'all') params.set('warehouse_id', whFilter.value);
  const st = document.getElementById('disposal-status-filter').value;
  if (st !== 'all') params.set('disposal_status', st);
  const dateFrom = parseRuDate(document.getElementById('disposal-date-from').value);
  const dateTo = parseRuDate(document.getElementById('disposal-date-to').value);
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  params.set('sort', document.getElementById('disposal-sort').value);
  const q = document.getElementById('disposal-search').value;
  if (q) params.set('q', q);
  params.set('page', disposalPage);
  params.set('per_page', PER_PAGE);

  const data = await api('/api/units/disposal?' + params.toString());
  const units = data.units;
  const totalPages = Math.ceil(data.total / PER_PAGE);
  const tbody = document.querySelector('#disposal-table tbody');
  tbody.innerHTML = units.map(u => {
    const deadline = getDeadlineInfo(u.sold_date, u.has_marking);
    let deadlineBadge = '';
    if (u.disposal_status === 0 && u.sold_date) {
      if (deadline.urgent) deadlineBadge = `<span class="badge bg-danger ms-1"><i class="bi bi-exclamation-triangle"></i> Просрочено</span>`;
      else if (deadline.warning) deadlineBadge = `<span class="badge bg-warning text-dark ms-1"><i class="bi bi-clock"></i> Сгорает!</span>`;
    }
    return `<tr class="${u.disposal_status === 0 && deadline.urgent ? 'table-danger' : ''}">
      <td class="font-monospace fw-bold">#${u.id}</td>
      <td>${esc(u.sku_name || '')} <span class="text-primary font-monospace">${esc(u.sku_article || '')}</span></td>
      <td>${u.cz_code ? `<div class="code-box" style="font-size:9px;max-width:200px;cursor:pointer" onclick="copyText('${(normalizeCZ(u.cz_code).replace(/^\xe8/, '')).split('\u001d')[0].replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')" title="Нажмите, чтобы скопировать">${esc((normalizeCZ(u.cz_code).replace(/^\xe8/, '')).split('\u001d')[0])}</div>` : '<i class="bi bi-hourglass-split"></i> нет'}</td>
      <td>${warehouseBadge(u.warehouse_name)}</td>
      <td>${DISPOSAL_TYPES[u.disposal_type] || '—'}</td>
      <td>${DISPOSAL_REASONS[u.disposal_reason] || u.disposal_reason || '—'}</td>
      <td>${esc(u.disposal_doc_type || '')} ${u.disposal_doc_number ? '№' + esc(u.disposal_doc_number) : ''}</td>
      <td>${fmtDate(u.disposal_doc_date)}${deadlineBadge}</td>
      <td>${u.disposal_price ? u.disposal_price.toFixed(2) : '—'}</td>
      <td>${disposalBadge(u.disposal_status)}</td>
      <td class="text-nowrap">
        <button class="btn btn-outline-primary btn-sm" onclick="showUnitDetail(${u.id})" title="Подробнее"><i class="bi bi-info-circle"></i></button>
        <button class="btn btn-outline-secondary btn-sm" onclick="openUnitModal(${u.id})" title="Изменить"><i class="bi bi-pencil"></i></button>
        ${[4,5].includes(u.status) && u.disposal_status === 2 && ['RETIRED','WITHDRAWN','WRITTEN_OFF'].includes(u.cz_status) ? `<button class="btn btn-outline-warning btn-sm" onclick="openReturnModal(${u.id})" title="Вернуть в оборот"><i class="bi bi-arrow-counterclockwise"></i></button>` : ''}
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="11" class="text-center text-muted">Нет единиц для вывода из оборота</td></tr>';

  renderPagination('disposal', disposalPage, totalPages, data.total, 'disposalPage');
}

async function renderSelects() {
  const skus = await api('/api/skus');
  const warehouses = await api('/api/warehouses');
  const skuOpts = skus.map(s => `<option value="${s.id}">${esc(s.name)} ${s.article ? '(' + esc(s.article) + ')' : ''}</option>`).join('');
  ['lbl-sku', 'imp-sku'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = skuOpts; });
  const impLoc = document.getElementById('imp-loc');
  if (impLoc) impLoc.innerHTML = warehouses.map(w => whOption(w)).join('');
}

async function importCSV() {
  const f = document.getElementById('imp-csv').files[0];
  if (!f) { toast('Выберите файл', 'error'); return; }
  const skuId = parseInt(document.getElementById('imp-sku').value);
  const warehouseId = parseInt(document.getElementById('imp-loc').value);
  const status = parseInt(document.getElementById('imp-status').value);
  const fd = new FormData();
  fd.append('file', f); fd.append('sku_id', skuId); fd.append('warehouse_id', warehouseId); fd.append('status', status);
  try {
    const r = await fetch('/api/import/csv', { method: 'POST', body: fd });
    const data = await r.json();
    showImportLog(`CSV: ${data.added} добавлено | ${data.duplicates} дубликатов | ${data.errors || 0} ошибок`);
    render(); toast(`+${data.added}`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

function showImportLog(text) {
  const log = document.getElementById('import-log');
  log.classList.remove('d-none');
  log.textContent = text;
}

function stripHash(s) {
  return s.replace(/^#+/g, '').trim();
}

async function downloadLabels() {
  const skuId = parseInt(document.getElementById('lbl-sku').value);
  const range = document.getElementById('lbl-range').value;
  const copies = parseInt(document.getElementById('lbl-total').value) || 1;
  const size = document.getElementById('lbl-size').value;
  const layout = document.getElementById('lbl-layout').value;
  const r = await fetch(`/api/labels/pdf?sku_id=${skuId}&range=${encodeURIComponent(range)}&copies=${copies}&size=${size}&layout=${layout}`);
  if (!r.ok) { toast(await r.text(), 'error'); return; }
  const blob = await r.blob();
  const cd = r.headers.get('Content-Disposition') || '';
  const match = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
  const fname = match ? decodeURIComponent(match[1]) : `labels_${skuId}.pdf`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  a.click();
  toast('PDF скачан', 'success');
}

async function printLabels() {
  const skuId = parseInt(document.getElementById('lbl-sku').value);
  const range = document.getElementById('lbl-range').value;
  const copies = parseInt(document.getElementById('lbl-total').value) || 1;
  const size = document.getElementById('lbl-size').value;
  const layout = document.getElementById('lbl-layout').value;
  const r = await fetch(`/api/labels/pdf?sku_id=${skuId}&range=${encodeURIComponent(range)}&copies=${copies}&size=${size}&layout=${layout}`);
  if (!r.ok) { toast(await r.text(), 'error'); return; }
  const blob = await r.blob();
  const blobUrl = URL.createObjectURL(blob);
  const win = window.open('', '_blank');
  if (!win) { toast('Блокировщик поп-ups воспрепятствовал открытию', 'error'); return; }
  win.document.write(`<!DOCTYPE html><html><head><style>body{margin:0}embed{width:100%;height:100vh}</style></head><body><embed id="pdf" src="${blobUrl}" type="application/pdf"></body></html>`);
  win.document.close();
  const embed = win.document.getElementById('pdf');
  embed.onload = function() { setTimeout(function() { win.print(); }, 300); };
}

async function downloadPrintLabels(format) {
  const skuId = parseInt(document.getElementById('lbl-sku').value);
  const range = document.getElementById('lbl-range').value;
  const copies = parseInt(document.getElementById('lbl-total').value) || 1;
  const size = document.getElementById('lbl-size').value;
  const layout = document.getElementById('lbl-layout').value;
  const r = await fetch(`/api/labels/print?sku_id=${skuId}&range=${encodeURIComponent(range)}&copies=${copies}&format=${format}&size=${size}&layout=${layout}`);
  if (!r.ok) { toast(await r.text(), 'error'); return; }
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `labels_${Date.now()}.${format === 'zpl' ? 'zprn' : 'tspl'}`;
  a.click();
  toast(`${format.toUpperCase()} скачан`, 'success');
}

function openLabelsForUnit(skuId, unitId) {
  document.querySelector('#nav .nav-link[data-tab="labels"]').click();
  setTimeout(() => {
    document.getElementById('lbl-sku').value = skuId;
    document.getElementById('lbl-range').value = '#' + unitId;
    document.getElementById('lbl-total').value = '1';
  }, 200);
}

let lblCzSearchTimer = null;
async function searchSkuByCz() {
  clearTimeout(lblCzSearchTimer);
  const code = document.getElementById('lbl-cz-search').value.trim();
  const statusEl = document.getElementById('lbl-cz-search-status');
  if (!code) { statusEl.textContent = ''; return; }
  statusEl.textContent = 'Поиск...';
  statusEl.className = 'text-muted';
  lblCzSearchTimer = setTimeout(async () => {
    try {
      const r = await api(`/api/units/find-by-code?code=${encodeURIComponent(code)}`);
      if (r.found) {
        const u = r.unit;
        document.getElementById('lbl-sku').value = u.sku_id;
        document.getElementById('lbl-range').value = '#' + u.id;
        const short = (normalizeCZ(u.cz_code || '').replace(/^\xe8/, '')).split('\u001d')[0];
        statusEl.innerHTML = `<span class="text-success"><i class="bi bi-check-circle"></i> #${u.id} · ${esc(u.sku_name || '')} · ${esc(short)}</span>`;
      } else {
        statusEl.innerHTML = '<span class="text-danger"><i class="bi bi-x-circle"></i> Не найден</span>';
      }
    } catch (e) {
      statusEl.innerHTML = `<span class="text-danger">${esc(e.message)}</span>`;
    }
  }, 400);
}

async function exportJSON() {
  const r = await fetch('/api/export/json');
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `inventory_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
}

async function importJSON(ev) {
  const f = ev.target.files[0]; if (!f) return;
  if (!confirm('Заменить текущие данные?')) return;
  const fd = new FormData(); fd.append('file', f);
  await fetch('/api/import/json', { method: 'POST', body: fd });
  location.reload();
}

async function exportCSVUnits() {
  const r = await fetch('/api/export/csv');
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `units_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
}

async function exportDisposalCSV() {
  const r = await fetch('/api/export/disposal-csv');
  const blob = await r.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `disposal_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  toast('CSV выбытия скачан', 'success');
}

function openDatePicker(textId) {
  const picker = document.getElementById(textId + '-pick');
  picker.showPicker();
  picker.onchange = function() {
    if (this.value) {
      const [y, m, d] = this.value.split('-');
      document.getElementById(textId).value = `${d}.${m}.${y}`;
    } else {
      document.getElementById(textId).value = '';
    }
    document.getElementById(textId).dispatchEvent(new Event('change'));
  };
}

// ============ PAGINATION ============
function parseRuDate(v) {
  if (!v) return '';
  const parts = v.split('.');
  if (parts.length !== 3) return v;
  return `${parts[2]}-${parts[1].padStart(2,'0')}-${parts[0].padStart(2,'0')}`;
}

function renderPagination(tab, page, totalPages, total, stateVar) {
  const container = document.getElementById(`${tab}-pagination`);
  if (!container) return;
  if (totalPages <= 1) { container.innerHTML = ''; return; }
  const from = (page - 1) * PER_PAGE + 1;
  const to = Math.min(page * PER_PAGE, total);
  let html = `<div class="d-flex justify-content-between align-items-center mt-2 flex-wrap gap-2">`;
  html += `<small class="text-muted">Показано ${from}–${to} из ${total}</small>`;
  html += `<nav><ul class="pagination pagination-sm mb-0">`;
  html += `<li class="page-item${page <= 1 ? ' disabled' : ''}"><a class="page-link" href="#" onclick="event.preventDefault();${stateVar}=${page-1};render${tab.charAt(0).toUpperCase()+tab.slice(1)}();">&laquo;</a></li>`;
  const start = Math.max(1, page - 3);
  const end = Math.min(totalPages, page + 3);
  if (start > 1) html += `<li class="page-item"><a class="page-link" href="#" onclick="event.preventDefault();${stateVar}=1;render${tab.charAt(0).toUpperCase()+tab.slice(1)}();">1</a></li>`;
  if (start > 2) html += `<li class="page-item disabled"><span class="page-link">…</span></li>`;
  for (let i = start; i <= end; i++) {
    html += `<li class="page-item${i === page ? ' active' : ''}"><a class="page-link" href="#" onclick="event.preventDefault();${stateVar}=${i};render${tab.charAt(0).toUpperCase()+tab.slice(1)}();">${i}</a></li>`;
  }
  if (end < totalPages - 1) html += `<li class="page-item disabled"><span class="page-link">…</span></li>`;
  if (end < totalPages) html += `<li class="page-item"><a class="page-link" href="#" onclick="event.preventDefault();${stateVar}=${totalPages};render${tab.charAt(0).toUpperCase()+tab.slice(1)}();">${totalPages}</a></li>`;
  html += `<li class="page-item${page >= totalPages ? ' disabled' : ''}"><a class="page-link" href="#" onclick="event.preventDefault();${stateVar}=${page+1};render${tab.charAt(0).toUpperCase()+tab.slice(1)}();">&raquo;</a></li>`;
  html += `</ul></nav></div>`;
  container.innerHTML = html;
}

// ============ CZ SETTINGS ============
async function loadCZSettings() {
  try {
    const s = await api('/api/settings/cz');
    document.getElementById('cz-default-address').value = s.default_disposal_address || '';
    document.getElementById('cz-default-fias').value = s.default_disposal_fias_id || '';
    cachedDefaultAddress = s.default_disposal_address || '';
    cachedDefaultFias = s.default_disposal_fias_id || '';
    document.getElementById('cz-cert-thumbprint').value = s.cz_cert_thumbprint || '';
    document.getElementById('cz-api-url').value = s.cz_api_url || '';
    document.getElementById('cz-inn').value = s.cz_inn || '';
    document.getElementById('cz-key-pin').value = s.cz_key_pin || '';
    const url = s.cz_api_url || '';
    const sel = document.getElementById('cz-api-url-select');
    if (sel) {
      for (const opt of sel.options) {
        if (opt.value === url) { sel.value = url; break; }
      }
    }
  } catch (e) {}
}

async function saveCZSettings() {
  const data = {
    default_disposal_address: document.getElementById('cz-default-address').value.trim(),
    default_disposal_fias_id: document.getElementById('cz-default-fias').value.trim(),
  };
  try {
    await api('/api/settings/cz', { method: 'POST', body: JSON.stringify(data) });
    document.getElementById('cz-settings-status').innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> Saved</span>';
    toast('Settings saved', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function saveCZAllSettings() {
  const data = {
    cz_api_url: document.getElementById('cz-api-url').value.trim(),
    cz_inn: document.getElementById('cz-inn').value.trim(),
    cz_cert_thumbprint: document.getElementById('cz-cert-thumbprint').value.trim(),
    cz_key_pin: document.getElementById('cz-key-pin').value,
  };
  try {
    await api('/api/settings/cz', { method: 'POST', body: JSON.stringify(data) });
    toast('All CZ settings saved', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function czFindCerts() {
  const listEl = document.getElementById('cz-certs-list');
  const container = document.getElementById('cz-certs-container');
  listEl.classList.add('d-none');
  container.innerHTML = '<div class="text-muted small"><i class="bi bi-hourglass-split"></i> Сканирование хранилищ сертификатов...</div>';
  listEl.classList.remove('d-none');
  try {
    const r = await api('/api/cz/list-certs');
    if (!r.ok) {
      container.innerHTML = `<div class="alert alert-danger py-2 mb-0 small">${esc(r.error)}</div>`;
      return;
    }
    if (!r.certs.length) {
      container.innerHTML = '<div class="alert alert-warning py-2 mb-0 small"><i class="bi bi-exclamation-triangle"></i> Сертификаты не найдены. Убедитесь, что токен подключён и КриптоПро установлен.</div>';
      return;
    }
    container.innerHTML = r.certs.map(c => {
      const selected = document.getElementById('cz-cert-thumbprint').value.trim() === c.thumbprint;
      const expired = c.valid_to && new Date(c.valid_to) < new Date();
      return `<div class="card card-body py-2 px-3 mb-1 ${selected ? 'border-success bg-success bg-opacity-10' : (expired ? 'border-warning' : '')}" style="cursor:pointer" onclick="selectCZCert('${esc(c.thumbprint)}')">
        <div class="d-flex justify-content-between align-items-start">
          <div>
            <div class="fw-semibold small">${esc(c.subject)}</div>
            <div class="text-muted" style="font-size:11px">Издатель: ${esc(c.issuer)} &middot; Хранилище: ${esc(c.store)}</div>
            <div style="font-size:11px">Отпечаток: <code>${esc(c.thumbprint)}</code></div>
          </div>
          <div class="text-end">
            ${c.has_private_key ? '<span class="badge bg-success">Есть ключ</span>' : '<span class="badge bg-danger">Нет ключа</span>'}
            ${expired ? '<span class="badge bg-warning text-dark">Истёк</span>' : ''}
            ${selected ? '<span class="badge bg-primary">Выбран</span>' : ''}
          </div>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    container.innerHTML = `<div class="alert alert-danger py-2 mb-0 small">${esc(e.message)}</div>`;
  }
}

function selectCZCert(thumbprint) {
  document.getElementById('cz-cert-thumbprint').value = thumbprint;
  toast('Отпечаток выбран. Нажмите «Сохранить».', 'info');
}

async function czTestAuth() {
  const thumbprint = document.getElementById('cz-cert-thumbprint').value.trim();
  const statusEl = document.getElementById('cz-auth-status');
  if (!thumbprint) { toast('Сначала введите или выберите отпечаток', 'error'); return; }
  statusEl.innerHTML = '<span class="text-warning"><i class="bi bi-hourglass-split"></i> Подключение к ЧЗ...</span>';
  statusEl.className = 'small mb-2';
  try {
    const r = await api('/api/cz/test-auth', { method: 'POST', body: JSON.stringify({ thumbprint }) });
    if (r.ok) {
      statusEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> ' + esc(r.message) + '</span>';
      toast('Авторизация успешна', 'success');
    } else {
      statusEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> ${esc(r.error)}</span>`;
      toast(r.error, 'error');
    }
  } catch (e) {
    statusEl.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle"></i> ${esc(e.message)}</span>`;
    toast(e.message, 'error');
  }
}

let czCheckPollTimer = null;

async function czCheckAll() {
  if (!confirm('Обновить статусы всех товаров из Честного Знака?\n\nЭто может занять время при большом количестве кодов.\nДля работы требуется установленный КриптоПро и настроенный отпечаток сертификата.')) return;
  const btn = document.getElementById('cz-check-all-btn');
  const badge = document.getElementById('cz-check-status-badge');
  const log = document.getElementById('cz-check-log');
  btn.disabled = true;
  badge.textContent = 'Запуск проверки...';
  badge.className = 'text-warning small';
  log.classList.remove('d-none');
  log.textContent = '► Запрос токена авторизации...\n';

  try {
    await api('/api/cz/check-all', { method: 'POST', body: JSON.stringify({}) });
    startCzCheckPoll();
  } catch (e) {
    btn.disabled = false;
    badge.textContent = 'Ошибка';
    badge.className = 'text-danger small';
    log.textContent += '✗ ' + e.message + '\n';
    toast('Ошибка: ' + e.message, 'error');
  }
}

function startCzCheckPoll() {
  const btn = document.getElementById('cz-check-all-btn');
  const badge = document.getElementById('cz-check-status-badge');
  const log = document.getElementById('cz-check-log');
  if (czCheckPollTimer) clearInterval(czCheckPollTimer);
  czCheckPollTimer = setInterval(async () => {
    try {
      const s = await api('/api/cz/check-status');
      if (s.running) {
        badge.textContent = `Проверено ${s.checked} из ${s.total}...`;
        badge.className = 'text-warning small';
        return;
      }
      clearInterval(czCheckPollTimer);
      czCheckPollTimer = null;
      btn.disabled = false;
      if (s.last_error) {
        badge.textContent = 'Ошибка';
        badge.className = 'text-danger small';
        log.textContent += '✗ Ошибка: ' + s.last_error + '\n';
        toast('Ошибка проверки: ' + s.last_error, 'error');
      } else {
        badge.textContent = s.last_result || 'Готово';
        badge.className = 'text-success small';
        log.textContent += '✓ ' + (s.last_result || 'Готово') + '\n';
        toast(s.last_result || 'Проверка завершена', 'success');
        renderStock();
        renderSold();
        renderDisposal();
      }
    } catch (e) {
      clearInterval(czCheckPollTimer);
      czCheckPollTimer = null;
      btn.disabled = false;
    }
  }, 1500);
}

async function czCheckSingle(unitId) {
  toast('Проверка статуса...', 'info');
  try {
    const r = await api('/api/cz/check-single', { method: 'POST', body: JSON.stringify({ unit_id: unitId }) });
    if (r.ok) {
      toast(`Статус ЧЗ: ${r.cz_status}`, 'success');
    } else {
      toast(r.error || 'Не удалось получить статус', 'error');
    }
    return r;
  } catch (e) { toast(e.message, 'error'); return null; }
}

async function czDebugUnit() {
  const unitId = parseInt(document.getElementById('cz-debug-unit-id').value);
  if (!unitId) { toast('Укажите ID единицы', 'error'); return; }
  const log = document.getElementById('cz-check-log');
  const badge = document.getElementById('cz-check-status-badge');
  log.classList.remove('d-none');
  log.textContent = 'Отладка запроса к API...\n';
  badge.textContent = 'Проверка...';
  badge.className = 'text-warning small';
  try {
    const r = await api('/api/cz/debug', { method: 'POST', body: JSON.stringify({ unit_id: unitId }) });
    log.textContent = '';
    const lines = [
      `unit_id: ${r.unit_id}`,
      `cz_code_raw: ${r.cz_code_raw}`,
      `cz_code_len: ${r.cz_code_len}`,
      `cz_code_hex: ${r.cz_code_hex}`,
      `thumbprint: ${r.thumbprint}`,
      `api_url: ${r.api_url}`,
      `token_prefix: ${r.token_prefix}`,
      `clean_code: ${r.clean_code}`,
      `clean_len: ${r.clean_len}`,
      ``,
      `raw_results: ${JSON.stringify(r.raw_results, null, 2)}`,
      ``,
      `cisStatus: ${r.cisStatus || '(пусто)'}`,
      `status: ${r.status || '(пусто)'}`,
      `all_keys: ${JSON.stringify(r.all_keys)}`,
    ];
    if (r.error) lines.push(`\nОШИБКА: ${r.error}`);
    log.textContent = lines.join('\n');
    badge.textContent = r.error ? 'Ошибка' : 'Готово';
    badge.className = r.error ? 'text-danger small' : 'text-success small';
  } catch (e) {
    log.textContent = `Ошибка: ${e.message}`;
    badge.textContent = 'Ошибка';
    badge.className = 'text-danger small';
  }
}

async function czDiagnose() {
  const log = document.getElementById('cz-check-log');
  const badge = document.getElementById('cz-check-status-badge');
  log.classList.remove('d-none');
  log.textContent = 'Диагностика...\n';
  badge.textContent = 'Проверка...';
  badge.className = 'text-warning small';

  try {
    saveCZAllSettings();
    const r = await api('/api/cz/diagnose', { method: 'POST', body: JSON.stringify({}) });
    log.textContent = '';
    for (const step of r.steps) {
      const icon = step.ok ? '✓' : '✗';
      const cls = step.ok ? 'text-success' : 'text-danger';
      log.innerHTML += `<span class="${cls}">${icon}</span> ${esc(step.step)}: ${esc(step.detail)}\n`;
    }
    if (r.ok) {
      badge.textContent = 'Все проверки пройдены';
      badge.className = 'text-success small';
      toast('Диагностика: всё OK', 'success');
    } else {
      const failed = r.steps.find(s => !s.ok);
      badge.textContent = `Ошибка: ${failed ? failed.step : '?'}`;
      badge.className = 'text-danger small';
      toast(`Диагностика: ошибка на шаге ${failed ? failed.step : '?'}`, 'error');
    }
  } catch (e) {
    badge.textContent = 'Ошибка';
    badge.className = 'text-danger small';
    log.textContent = 'Ошибка: ' + e.message;
    toast(e.message, 'error');
  }
}

// ============ BACKUPS ============
async function renderBackups() {
  try {
    const data = await api('/api/backups');
    document.getElementById('backup-remaining').textContent = `Осталось мест: ${data.remaining} из ${data.max}`;
    document.getElementById('backup-rotation').checked = data.rotation;
    const list = document.getElementById('backup-list');
    if (data.backups.length === 0) {
      list.innerHTML = '<p class="text-muted small mb-0">Бэкапов пока нет</p>';
      return;
    }
    list.innerHTML = `<div class="table-responsive"><table class="table table-sm table-hover align-middle mb-0">
      <thead class="table-light"><tr><th>Файл</th><th>Дата</th><th>Размер</th><th></th></tr></thead>
      <tbody>${data.backups.map(b => `
        <tr>
          <td class="font-monospace small">${esc(b.filename)}</td>
          <td>${esc(b.created)}</td>
          <td>${(b.size / 1024).toFixed(1)} КБ</td>
          <td class="text-nowrap">
            <button class="btn btn-outline-warning btn-sm" onclick="restoreBackup('${esc(b.filename)}')"><i class="bi bi-arrow-counterclockwise"></i> Восстановить</button>
            <button class="btn btn-outline-danger btn-sm" onclick="deleteBackup('${esc(b.filename)}')"><i class="bi bi-trash"></i></button>
          </td>
        </tr>
      `).join('')}</tbody>
    </table></div>`;
  } catch (e) { toast(e.message, 'error'); }
}

async function createBackup() {
  try {
    const r = await api('/api/backups/create', { method: 'POST' });
    toast(`Бэкап создан: ${r.filename}`, 'success');
    renderBackups();
  } catch (e) { toast(e.message, 'error'); }
}

async function restoreBackup(filename) {
  if (!confirm(`Восстановить базу из бэкапа ${filename}?\nТекущая база будет заменена. Приложение потребуется перезапустить.`)) return;
  try {
    const r = await api('/api/backups/restore', { method: 'POST', body: JSON.stringify({ filename }) });
    toast(r.message, 'warning');
    renderBackups();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteBackup(filename) {
  if (!confirm(`Удалить бэкап ${filename}?`)) return;
  try {
    await api('/api/backups/delete', { method: 'POST', body: JSON.stringify({ filename }) });
    toast('Бэкап удалён', 'success');
    renderBackups();
  } catch (e) { toast(e.message, 'error'); }
}

async function toggleRotation() {
  const enabled = document.getElementById('backup-rotation').checked;
  try {
    await api('/api/backups/rotation', { method: 'POST', body: JSON.stringify({ enabled }) });
    toast(enabled ? 'Ротация включена. Создан автобэкап.' : 'Ротация выключена', 'success');
    renderBackups();
  } catch (e) { toast(e.message, 'error'); }
}

// ===== SYNC =====

function togglePasswordVisibility(inputId, btn) {
  const inp = document.getElementById(inputId);
  const icon = btn.querySelector('i');
  if (inp.type === 'password') {
    inp.type = 'text';
    icon.className = 'bi bi-eye-slash';
  } else {
    inp.type = 'password';
    icon.className = 'bi bi-eye';
  }
}

let syncPollTimer = null;

async function loadSyncSettings() {
  try {
    const r = await api('/api/sync/settings');
    document.getElementById('sync-host').value = r.host || '';
    document.getElementById('sync-user').value = r.user || '';
    document.getElementById('sync-remote-dir').value = r.remote_dir || '';
    if (r.password) {
      document.getElementById('sync-password').value = r.password;
      document.getElementById('sync-status-badge').textContent = 'Пароль сохранён';
      document.getElementById('sync-status-badge').className = 'text-success small';
    } else {
      document.getElementById('sync-status-badge').textContent = 'Пароль не задан';
      document.getElementById('sync-status-badge').className = 'text-danger small';
    }
  } catch (e) { console.error(e); }
}

async function saveSyncSettings() {
  const data = {
    host: document.getElementById('sync-host').value.trim(),
    user: document.getElementById('sync-user').value.trim(),
    remote_dir: document.getElementById('sync-remote-dir').value.trim(),
  };
  const pw = document.getElementById('sync-password').value;
  if (pw) data.password = pw;
  try {
    await api('/api/sync/settings', { method: 'POST', body: JSON.stringify(data) });
    document.getElementById('sync-password').value = '';
    document.getElementById('sync-status-badge').textContent = 'Настройки сохранены';
    document.getElementById('sync-status-badge').className = 'text-success small';
    toast('Настройки синхронизации сохранены', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function syncPush() {
  if (!confirm('Загрузить текущую базу на сервер?\nНа сервере будет создан бэкап перед заменой.')) return;
  try {
    await api('/api/sync/push', { method: 'POST', body: JSON.stringify({}) });
    startSyncPoll('push');
  } catch (e) { toast(e.message, 'error'); }
}

async function syncPull() {
  if (!confirm('Скачать базу с сервера?\nЛокальная база будет заменена (предварительный бэкап создаётся автоматически).\n\nПосле скачивания потребуется перезапуск приложения.')) return;
  try {
    await api('/api/sync/pull', { method: 'POST', body: JSON.stringify({}) });
    startSyncPoll('pull');
  } catch (e) { toast(e.message, 'error'); }
}

function startSyncPoll(direction) {
  const btnPush = document.getElementById('sync-push-btn');
  const btnPull = document.getElementById('sync-pull-btn');
  const log = document.getElementById('sync-log');
  const badge = document.getElementById('sync-status-badge');

  btnPush.disabled = true;
  btnPull.disabled = true;
  badge.textContent = direction === 'push' ? 'Загрузка на сервер...' : 'Скачивание с сервера...';
  badge.className = 'text-warning small';
  log.classList.remove('d-none');
  log.textContent = (direction === 'push' ? '► Загрузка на сервер...\n' : '► Скачивание с сервера...\n');

  if (syncPollTimer) clearInterval(syncPollTimer);
  syncPollTimer = setInterval(async () => {
    try {
      const s = await api('/api/sync/status');
      if (s.running) return;
      clearInterval(syncPollTimer);
      syncPollTimer = null;
      btnPush.disabled = false;
      btnPull.disabled = false;
      if (s.last_error) {
        badge.textContent = 'Ошибка';
        badge.className = 'text-danger small';
        log.textContent += '✗ Ошибка: ' + s.last_error + '\n';
        toast('Ошибка синхронизации: ' + s.last_error, 'error');
      } else {
        badge.textContent = s.last_result || 'Готово';
        badge.className = 'text-success small';
        log.textContent += '✓ ' + (s.last_result || 'Готово') + '\n';
        if (s.direction === 'pull') {
          log.textContent += '\n⚠ Локальная база обновлена. Перезапустите приложение для применения изменений.\n';
          toast((s.last_result || 'Синхронизация завершена') + ' — перезапустите приложение!', 'warning');
        } else {
          log.textContent += '\n✓ Загрузка на сервер завершена успешно.\n';
          toast(s.last_result || 'Загрузка на сервер завершена', 'success');
        }
      }
    } catch (e) {
      clearInterval(syncPollTimer);
      syncPollTimer = null;
      btnPush.disabled = false;
      btnPull.disabled = false;
    }
  }, 1000);
}

// ===== Keyboard Shortcuts =====
const TAB_SHORTCUTS = {
  '1': 'dashboard', '2': 'quicksell', '3': 'warehouses',
  '4': 'skus', '5': 'stock', '6': 'sold',
  '7': 'disposal', '8': 'scan', '9': 'import',
  '0': 'labels',
};
const TAB_SHORTCUT_NAMES = {
  '1': 'Сводка', '2': 'Продажа', '3': 'Склады',
  '4': 'Товары', '5': 'Остатки', '6': 'Продано',
  '7': 'Вывод из оборота', '8': 'Сканирование', '9': 'Импорт',
  '0': 'Этикетки',
};

function isInputFocused() {
  const el = document.activeElement;
  return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
}

function isModalOpen() {
  return document.querySelector('.modal.show') !== null;
}

document.addEventListener('keydown', function(e) {
  if (isModalOpen()) return;

  // Escape — clear global search
  if (e.key === 'Escape' && !isInputFocused()) {
    const gs = document.getElementById('global-search');
    if (gs) { gs.value = ''; gs.blur(); }
    return;
  }

  // Don't intercept when typing in inputs (except Escape)
  if (isInputFocused()) return;

  // Number keys 1-9, 0 — switch tabs
  if (TAB_SHORTCUTS[e.key] && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault();
    const tabId = TAB_SHORTCUTS[e.key];
    const link = document.querySelector(`#nav .nav-link[data-tab="${tabId}"]`);
    if (link) link.click();
    return;
  }

  // / or Ctrl+F — focus global search
  if (e.key === '/' || (e.ctrlKey && e.key === 'f')) {
    e.preventDefault();
    const gs = document.getElementById('global-search');
    if (gs) gs.focus();
    return;
  }

  // S — quick sell tab (if not on it already, focus CZ input)
  if (e.key === 's' && !e.ctrlKey) {
    const activeTab = document.querySelector('.tab:not(.d-none)');
    if (activeTab && activeTab.id === 'tab-quicksell') {
      const cz = document.getElementById('qs-cz');
      if (cz) { cz.focus(); cz.select(); }
    }
    return;
  }

  // N — open new unit modal (from stock tab)
  if (e.key === 'n' && !e.ctrlKey) {
    const activeTab = document.querySelector('.tab:not(.d-none)');
    if (activeTab && activeTab.id === 'tab-stock') {
      e.preventDefault();
      openUnitModal();
    }
    return;
  }
});
