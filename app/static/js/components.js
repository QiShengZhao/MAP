/* === el：声明式建 DOM === */
const el = (tag, attrs = {}, ...children) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (k === 'html') e.innerHTML = v;
    else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(9)) {
    if (c != null) e.appendChild(typeof c === 'object' ? c : document.createTextNode(c));
  }
  return e;
};

function toast(msg, type = 'info') {
  const t = el('div', {class: `toast ${type}`}, String(msg));
  $('toastRoot').appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, 3000);
}

function openModal(title, body, footerBtns = []) {
  closeModal();
  const foot = el('div', {class: 'row right'}, ...footerBtns);
  const box = el('div', {class: 'modal-box'},
    el('div', {class: 'modal-head'},
      el('h3', {}, title),
      el('button', {class: 'ghost mini', onclick: closeModal}, '✕')),
    el('div', {class: 'modal-body'}, body),
    foot);
  const overlay = el('div', {class: 'modal-overlay', id: 'curModal',
    onclick: e => { if (e.target.id === 'curModal') closeModal(); }}, box);
  $('modalRoot').appendChild(overlay);
}

function closeModal() { $('curModal')?.remove(); }

function confirmDlg(msg) {
  return new Promise(resolve => {
    openModal('确认操作', el('p', {}, msg), [
      el('button', {class: 'ghost', onclick: () => { closeModal(); resolve(false); }}, '取消'),
      el('button', {class: 'danger', onclick: () => { closeModal(); resolve(true); }}, '确认'),
    ]);
  });
}

function dataTable({columns, rows, empty = '暂无数据'}) {
  const wrap = el('div', {class: 'table-wrap'});
  if (rows === null) {
    wrap.append(...[1, 2, 3].map(() => el('div', {class: 'skeleton'})));
    return wrap;
  }
  if (!rows.length) {
    wrap.appendChild(el('div', {class: 'empty'}, empty));
    return wrap;
  }
  let sortKey = null, sortAsc = true;
  const render = () => {
    const sorted = sortKey == null ? rows : [...rows].sort((a, b) => {
      const x = a[sortKey] ?? '', y = b[sortKey] ?? '';
      return (x > y ? 1 : x < y ? -1 : 0) * (sortAsc ? 1 : -1);
    });
    const tbl = el('table', {},
      el('thead', {}, el('tr', {}, ...columns.map(c =>
        el('th', {
          class: c.sortable !== false ? 'sortable' : '',
          onclick: () => {
            if (c.sortable === false) return;
            sortAsc = sortKey === c.key ? !sortAsc : true;
            sortKey = c.key;
            render();
          },
        }, c.label, sortKey === c.key ? (sortAsc ? ' ▲' : ' ▼') : '')))),
      el('tbody', {}, ...sorted.map(r => el('tr', {},
        ...columns.map(c => el('td', {}, c.render ? c.render(r) : String(r[c.key] ?? '—')))))));
    wrap.innerHTML = '';
    wrap.appendChild(tbl);
  };
  render();
  return wrap;
}

function field(label, input, hint = '') {
  return el('label', {class: 'field'},
    el('span', {class: 'field-label'}, label),
    input,
    el('span', {class: 'field-err'}),
    hint ? el('span', {class: 'field-hint'}, hint) : null);
}

function setFieldError(input, msg) {
  const e = input.closest('.field')?.querySelector('.field-err');
  if (e) e.textContent = msg || '';
}

function statusBadge(s) {
  const val = s && typeof s === 'object' && s.value ? s.value : s;
  return el('span', {class: `badge ${val}`}, String(val).replace('_', ' '));
}
