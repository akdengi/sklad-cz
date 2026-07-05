# SKU без маркировки — План реализации

> **For agentic workers:** Use compose:subagent or compose:execute to implement this plan task-by-task.

**Goal:** Добавить поддержку SKU без маркировки — товаров, которые продаются по GTIN/EAN13 без кода ЧЗ и без вывода из оборота.

**Architecture:** Добавляем булево поле `has_marking` в модель SKU. Фронтенд адаптирует форму SKU, таблицу, быструю продажу и поиск для работы с товарами без маркировки. Бэкенд обрабатывает продажу/возврат без заполнения полей выбытия.

**Tech Stack:** Python/Flask/SQLAlchemy, vanilla JS, Bootstrap 5

---

## Задача 1: Модель данных — поле `has_marking`

**Covers:** [S1]

**Files:**
- Modify: `app/models.py:51-96`
- Modify: `app/__init__.py` (миграция)

**Interfaces:**
- Consumes: nothing
- Produces: `SKU.has_marking` (Boolean, default True), отображается в `SKU.to_dict()`

- [ ] **Step 1: Добавить поле в модель SKU**

```python
# app/models.py, строка 61, после total_quantity:
has_marking = db.Column(db.Boolean, default=True)
```

- [ ] **Step 2: Добавить в to_dict()**

```python
# app/models.py, в методе to_dict(), добавить в return dict:
"has_marking": bool(self.has_marking),
```

- [ ] **Step 3: Миграция БД**

```python
# app/__init__.py, в функции init_db, после db.create_all() добавить:
with app.app_context():
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    columns = [c['name'] for c in inspector.get_columns('sku')]
    if 'has_marking' not in columns:
        db.session.execute(db.text('ALTER TABLE sku ADD COLUMN has_marking BOOLEAN DEFAULT 1'))
        db.session.commit()
```

- [ ] **Step 4: Commit**

```bash
git add app/models.py app/__init__.py
git commit -m "feat: add has_marking field to SKU model"
```

---

## Задача 2: API SKU — поддержка has_marking

**Covers:** [S1]

**Files:**
- Modify: `app/routes/skus.py:14-51`

**Interfaces:**
- Consumes: `has_marking` from request JSON
- Produces: `has_marking` в ответе SKU

- [ ] **Step 1: create_sku — принять has_marking**

```python
# app/routes/skus.py, в create_sku(), после total_quantity:
sku = SKU(
    ...
    has_marking=bool(data.get("has_marking", True)),
)
```

- [ ] **Step 2: update_sku — обновлять has_marking**

```python
# app/routes/skus.py, в update_sku(), после total_quantity:
sku.has_marking = bool(data.get("has_marking", sku.has_marking))
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/skus.py
git commit -m "feat: SKU API supports has_marking flag"
```

---

## Задача 3: Продажа без маркировки — бэкенд

**Covers:** [S2, S3]

**Files:**
- Modify: `app/routes/units.py:318-403` (quick_sell)
- Modify: `app/routes/units.py:80-96` (find_by_code)

**Interfaces:**
- Consumes: `cz_code` или `ean13` + `sku_id` для поиска, `target_warehouse_id`, `order_number`, `disposal_price`
- Produces: unit dict, transferred flag, message

- [ ] **Step 1: Добавить endpoint для продажи без маркировки**

```python
# app/routes/units.py, после quick_sell:

@units_bp.route("/sell-no-marking", methods=["POST"])
def sell_no_marking():
    """Продажа товара без маркировки по GTIN/EAN13 или SKU ID."""
    data = request.json or {}
    ean13 = (data.get("ean13") or "").strip()
    sku_id = data.get("sku_id")
    target_warehouse_id = data.get("target_warehouse_id")
    order_number = (data.get("order_number") or "").strip() or None
    disposal_price = data.get("disposal_price")

    # Найти SKU без маркировки
    sku = None
    if sku_id:
        sku = SKU.query.get(int(sku_id))
    elif ean13:
        sku = SKU.query.filter(SKU.ean13 == ean13, SKU.has_marking == False).first()
        if not sku:
            sku = SKU.query.filter(SKU.gtin14.like(f"%{ean13}"), SKU.has_marking == False).first()

    if not sku:
        abort(404, "Товар без маркировки не найден")
    if sku.has_marking:
        abort(400, "Этот товар имеет маркировку — используйте стандартную продажу")

    # Найти первую доступную единицу
    unit = Unit.query.filter(
        Unit.sku_id == sku.id,
        Unit.status.notin_([4, 5]),
    ).first()

    if not unit:
        # Создать новую единицу
        unit = Unit(
            sku_id=sku.id,
            status=0,
            warehouse_id=target_warehouse_id or 1,
        )
        db.session.add(unit)
        db.session.flush()

    # Перемещение на виртуальный склад
    source_wh = Warehouse.query.get(unit.warehouse_id)
    transferred = False
    if source_wh and target_warehouse_id:
        target_wh = Warehouse.query.get(int(target_warehouse_id))
        if target_wh and target_wh.id != source_wh.id and target_wh.wh_type == "virtual":
            # Удалить существующую на виртуальном (если есть)
            existing = Unit.query.filter(
                Unit.sku_id == sku.id,
                Unit.warehouse_id == target_wh.id,
                Unit.status.notin_([4, 5]),
            ).first()
            if existing:
                db.session.delete(existing)
                db.session.flush()

            target_unit = Unit(
                sku_id=sku.id,
                status=0,
                warehouse_id=target_wh.id,
            )
            db.session.add(target_unit)
            unit.status = 0
            unit.updated_at = datetime.utcnow()
            transferred = True
            db.session.flush()
            unit = target_unit

    # Продажа
    unit.status = 4
    unit.sold_date = datetime.utcnow().strftime("%Y-%m-%d")
    unit.order_number = order_number
    if disposal_price:
        unit.disposal_price = float(disposal_price)
    unit.updated_at = datetime.utcnow()
    db.session.commit()

    final_wh = Warehouse.query.get(unit.warehouse_id)
    return jsonify({
        "unit": unit.to_dict(),
        "transferred": transferred,
        "message": "Товар продан" if not transferred
                   else f"Товар перемещён на «{final_wh.name}» и продан",
    })
```

- [ ] **Step 2: Добавить поиск по EAN13 для find_by_code**

```python
# app/routes/units.py, в find_by_code(), после поиска по cz_code:

# Поиск по EAN13 (для товаров без маркировки)
if not unit:
    ean = code.strip()
    if len(ean) == 13 and ean.isdigit():
        sku = SKU.query.filter(SKU.ean13 == ean, SKU.has_marking == False).first()
        if sku:
            unit = Unit.query.options(
                joinedload(Unit.sku), joinedload(Unit.warehouse)
            ).filter(
                Unit.sku_id == sku.id,
                Unit.status.notin_([4, 5]),
            ).first()
```

- [ ] **Step 3: Commit**

```bash
git add app/routes/units.py
git commit -m "feat: sell-no-marking endpoint and EAN13 lookup"
```

---

## Задача 4: Форма SKU — чек-бокс маркировки

**Covers:** [S1]

**Files:**
- Modify: `templates/index.html:523-546` (SKU modal)
- Modify: `static/js/app.js:442-521` (openSkuModal, saveSku)

**Interfaces:**
- Consumes: `has_marking` из SKU
- Produces: чекбокс в форме, значение отправляется в API

- [ ] **Step 1: Добавить чекбокс в модальное окно SKU**

```html
<!-- templates/index.html, в SKU modal, перед GTIN-14: -->
<div class="mb-3">
  <div class="form-check form-switch">
    <input class="form-check-input" type="checkbox" id="sku-has-marking" checked>
    <label class="form-check-label fw-semibold" for="sku-has-marking">С маркировкой (ЧЗ)</label>
  </div>
  <small class="text-muted">Если снят — товар продаётся по EAN-13 без кода маркировки</small>
</div>
```

- [ ] **Step 2: Загрузка/сохранение has_marking в JS**

```javascript
// static/js/app.js, в openSkuModal(), добавить:
document.getElementById('sku-has-marking').checked = s.has_marking !== false;

// В блоке else (новый SKU):
document.getElementById('sku-has-marking').checked = true;

// В saveSku(), добавить в data:
has_marking: document.getElementById('sku-has-marking').checked,
```

- [ ] **Step 3: Commit**

```bash
git add templates/index.html static/js/app.js
git commit -m "feat: SKU modal has_marking checkbox"
```

---

## Задача 5: Таблица SKU — индикатор маркировки

**Covers:** [S1]

**Files:**
- Modify: `static/js/app.js:405-440` (renderSkuTable)

**Interfaces:**
- Consumes: `has_marking` из SKU
- Produces: бейдж в колонке «Маркировка»

- [ ] **Step 1: Обновить колонку «Маркировка» в таблице**

```javascript
// static/js/app.js, в renderSkuTable(), заменить формирование progressHtml:

// Было:
// if (total > 0) { ... } else { ... }

// Стало:
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
```

И в шаблоне `<td>` для маркировки:

```javascript
`<td>${badge} ${progressHtml}</td>`
```

- [ ] **Step 2: Commit**

```bash
git add static/js/app.js
git commit -m "feat: SKU table shows marking badge"
```

---

## Задача 6: Быстрая продажа — поддержка EAN13

**Covers:** [S2]

**Files:**
- Modify: `static/js/app.js:931-1010` (handleQuickSellInput)
- Modify: `static/js/app.js:1107+` (processQuickSell)
- Modify: `templates/index.html:88-120` (quicksell tab)

**Interfaces:**
- Consumes: ввод EAN13 или КМ
- Produces: результат продажи

- [ ] **Step 1: Обновить handleQuickSellInput для EAN13**

```javascript
// static/js/app.js, в handleQuickSellInput(), добавить проверку EAN13:

// Если введён 13-значный код — поискать товар без маркировки
if (/^\d{13}$/.test(searchCode)) {
  try {
    const data = await api(`/api/units/find-by-code?code=${encodeURIComponent(searchCode)}`);
    if (data.found) {
      qsFoundUnit = data.unit;
      qsFoundUnit._is_no_marking = true;
      const u = data.unit;
      statusEl.textContent = `Найден (без ЧЗ): #${u.id} ${u.sku_name}`;
      statusEl.className = 'text-info';
      // Показать информацию о товаре без маркировки
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
      // Показать виртуальные склады для выбора
      const warehouses = await api('/api/warehouses');
      const virtuals = warehouses.filter(w => w.wh_type === 'virtual');
      if (virtuals.length > 0) {
        targetWhSel.innerHTML = virtuals.map(w => whOption(w)).join('');
      }
      validateQuickSell();
      return;
    }
  } catch(e) {}
}
```

- [ ] **Step 2: Обновить processQuickSell для без-ЧЗ**

```javascript
// static/js/app.js, в processQuickSell(), добавить проверку:

// Если товар без маркировки — другой endpoint
if (qsFoundUnit && qsFoundUnit._is_no_marking) {
  const data = await api('/api/units/sell-no-marking', {
    method: 'POST',
    body: JSON.stringify({
      sku_id: qsFoundUnit.sku_id,
      target_warehouse_id: parseInt(document.getElementById('qs-target-warehouse').value),
      order_number: document.getElementById('qs-order').value.trim(),
      disposal_price: parseFloat(document.getElementById('qs-price').value) || 0,
    })
  });
  document.getElementById('qs-result').innerHTML = `
    <div class="alert alert-success"><i class="bi bi-check-circle"></i> ${esc(data.message)}</div>
  `;
  qsFoundUnit = null;
  document.getElementById('qs-cz').value = '';
  document.getElementById('qs-find-result').innerHTML = '';
  document.getElementById('qs-order').value = '';
  document.getElementById('qs-price').value = '';
  validateQuickSell();
  return;
}
```

- [ ] **Step 3: Обновить подсказку в quicksell tab**

```html
<!-- templates/index.html, в quicksell tab, обновить описание: -->
<p class="text-muted small mb-3">Отсканируйте код ЧЗ или EAN-13 штрихкод для быстрой продажи. Для товаров с маркировкой потребуется отчёт о выбытии в ЧЗ. Для товаров без маркировки — только учёт продажи.</p>
```

- [ ] **Step 4: Commit**

```bash
git add static/js/app.js templates/index.html
git commit -m "feat: quick-sell supports EAN13 for no-marking SKUs"
```

---

## Задача 7: Возврат без маркировки

**Covers:** [S4]

**Files:**
- Modify: `app/routes/units.py:153` (return_unit)

**Interfaces:**
- Consumes: unit ID
- Produces: updated unit

- [ ] **Step 1: Адаптировать return_unit для без-ЧЗ**

```python
# app/routes/units.py, в return_unit(), изменить проверку статуса:

# Было:
# if u.status != 3:
#     abort(400, ...)

# Стало:
if u.status not in (3, 4):
    abort(400, f"Возврат возможен только для товаров в обороте или проданных. Текущий статус: {STATUSES[u.status]}")
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/units.py
git commit -m "feat: return_unit accepts sold units (status 4)"
```

---

## Задача 8: Фильтрация — исключить без-ЧЗ из проверок

**Covers:** [S5]

**Files:**
- Modify: `app/routes/settings.py:480-540` (_do_cz_check_all)

**Interfaces:**
- Consumes: SKU.has_marking
- Produces: пропуск SKU без маркировки

- [ ] **Step 1: Исключить SKU без маркировки из массовой проверки**

```python
# app/routes/settings.py, в _do_cz_check_all(), в фильтре единиц:

# Было:
# units = Unit.query.filter(
#     Unit.cz_code != None, Unit.cz_code != '',
# ).all()

# Стало:
units = Unit.query.join(SKU).filter(
    Unit.cz_code != None, Unit.cz_code != '',
    SKU.has_marking == True,
).all()
```

- [ ] **Step 2: Commit**

```bash
git add app/routes/settings.py
git commit -m "feat: skip no-marking SKUs in CZ status check"
```

---

## Задача 9: Документация

**Covers:** [S1-S5]

**Files:**
- Modify: `docs/02-sku-tovary.md`
- Modify: `docs/10-prodazha.md`

- [ ] **Step 1: Обновить документацию SKU**

Добавить в `docs/02-sku-tovary.md` раздел про чекбокс «С маркировкой» и разницу в поведении.

- [ ] **Step 2: Обновить документацию продажи**

Добавить в `docs/10-prodazha.md` раздел «Продажа товаров без маркировки» — сканирование EAN13, отсутствие выбытия.

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: document no-marking SKU feature"
```
