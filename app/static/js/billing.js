/* === Billing：账户概览 / 套餐与席位 / 优惠券 / 发票预览（admin 写操作）=== */
const Billing = (() => {

  const card = (title, ...kids) => el('div', {class: 'card'}, el('h3', {}, title), ...kids);
  const kv = (k, v) => el('div', {class: 'kv'}, k, el('b', {}, v ?? '—'));
  const notAvail = title => card(title, el('p', {class: 'dim'}, '该功能未启用或接口不可用'));

  async function render() {
    const c = $('cfgContent');
    c.innerHTML = '';
    const grid = el('div', {class: 'billing-grid'});
    c.appendChild(grid);
    await Promise.all([account(grid), plans(grid), coupon(grid), invoices(grid)]);
  }

  async function account(grid) {
    try {
      const a = await api('/billing');
      const seats = await api('/billing/seats').catch(() => ({}));
      grid.appendChild(card('账户概览',
        kv('套餐', a.plan),
        kv('计费周期', a.interval === 'year' ? '年付' : '月付'),
        kv('席位', seats.used != null
          ? `${seats.used} / ${seats.capacity ?? seats.included ?? '?'}` : '—'),
        kv('当前周期', a.current_period_end ? `至 ${fmtTime(a.current_period_end)}` : '—'),
        kv('状态', a.status),
        isAdmin() ? el('button', {class: 'ghost', onclick: async () => {
          try {
            const p = await api('/billing/portal', {method: 'POST'});
            if (p.portal_url) window.open(p.portal_url);
          } catch (e) { toast(e.message, 'error'); }
        }}, '管理订阅（Stripe Portal）') : null));
    } catch (_) { grid.appendChild(notAvail('账户概览')); }
  }

  async function plans(grid) {
    if (!isAdmin()) return;
    const intervalSel = el('select', {},
      el('option', {value: 'month'}, '月付（metered 用量）'),
      el('option', {value: 'year'}, '年付（底价 + 席位）'));
    const seatsIn = el('input', {type: 'number', min: 0, value: 0});
    grid.appendChild(card('套餐变更',
      field('计费周期', intervalSel),
      field('额外席位（年付）', seatsIn),
      el('p', {class: 'dim'}, '将跳转 Stripe Checkout 完成支付'),
      el('button', {onclick: async () => {
        if (!await confirmDlg(`发起 ${intervalSel.value === 'year' ? '年付' : '月付'} checkout？将跳转 Stripe。`)) return;
        try {
          const r = await api('/billing/checkout', {method: 'POST', body: JSON.stringify({
            interval: intervalSel.value,
            seats: +seatsIn.value || 0,
          })});
          const url = r.checkout_url || r.url;
          if (url) window.location.href = url;
          else toast('checkout 已创建', 'success');
        } catch (e) { toast(e.message, 'error'); }
      }}, '前往支付')));
  }

  async function coupon(grid) {
    if (!isAdmin()) return;
    const codeIn = el('input', {placeholder: '促销码'});
    grid.appendChild(card('优惠券',
      field('促销码', codeIn),
      el('button', {class: 'ghost', onclick: async () => {
        if (!codeIn.value.trim()) return;
        try {
          const r = await api('/billing/coupon', {method: 'POST',
            body: JSON.stringify({promo_code: codeIn.value.trim()})});
          toast(r.description || r.message || '优惠券已应用', 'success');
          codeIn.value = '';
        } catch (e) { toast(e.message, 'error'); }
      }}, '应用'),
      el('button', {class: 'mini ghost', onclick: async () => {
        if (!codeIn.value.trim()) return;
        try {
          const r = await api(`/billing/promo/${encodeURIComponent(codeIn.value.trim())}/validate`);
          toast(r.valid ? `有效${r.percent_off ? `（${r.percent_off}% off）` : ''}` : '无效促销码',
            r.valid ? 'success' : 'error');
        } catch (e) { toast(e.message, 'error'); }
      }}, '验证')));
  }

  async function invoices(grid) {
    try {
      const r = await api('/billing/preview');
      const lines = r.lines || r.items || [];
      grid.appendChild(card('账单预览',
        r.amount_due != null ? kv('应付金额',
          `${(r.amount_due / 100).toFixed(2)} ${(r.currency || 'usd').toUpperCase()}`) : null,
        lines.length ? dataTable({columns: [
          {key: 'description', label: '项目'},
          {key: 'amount', label: '金额', render: i =>
            `${((i.amount ?? 0) / 100).toFixed(2)} ${(r.currency || 'usd').toUpperCase()}`},
        ], rows: lines}) : el('p', {class: 'dim'}, '暂无账单明细')));
    } catch (_) { grid.appendChild(notAvail('账单预览')); }
  }

  return {render};
})();
