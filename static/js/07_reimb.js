/* ---------- Reimbursement center 报销中心 (#/reimb) ---------- */
S.rb = S.rb || {status: 'open', tab: 'items'};
const RB_ST = {pending: 'Pending', partial: 'Partial', settled: 'Settled', written_off: 'Written off'};
VIEWS.reimb = async (v) => {
  const st = S.rb;
  const q = new URLSearchParams({status: st.status === 'open' ? 'pending,partial' : st.status});
  for (const k of ['start', 'end', 'payer', 'account_id']) if (st[k]) q.set(k, st[k]);
  const [d, rc] = await Promise.all([api('/api/reimbursements?' + q), api('/api/reimbursements/receipts?limit=100')]);
  const o = d.outstanding;
  const sel = new Set();
  v.innerHTML = `<div class="card"><div class="rb-cards">
      <div class="ic"><div class="lbl">Outstanding</div><div class="val">${o.by_currency.length ? o.by_currency.map(c => money(c.amount, c.currency)).join('<br>') : money(0)}</div>${o.by_currency.length > 1 ? `<div class="small muted">≈ ${money(o.total_base, o.base_currency)}</div>` : ''}</div>
      ${o.by_payer.slice(0, 5).map(p => `<div class="ic"><div class="lbl">${esc(p.payer === '(none)' ? 'No payer' : p.payer)}</div><div class="val">${p.by_currency.map(c => money(c.amount, c.currency)).join('<br>')}</div></div>`).join('')}
    </div>
    <div class="chips" style="margin-top:12px">${[['items', '🧾 Expenses'], ['receipts', `💼 Receipts (${rc.items.length})`]].map(([k, l]) => `<button class="chip${st.tab === k ? ' on' : ''}" data-tab="${k}">${l}</button>`).join('')}</div>
    <div id="rbBody"></div></div>`;
  $$('[data-tab]', v).forEach(b => b.onclick = () => { st.tab = b.dataset.tab; route(); });
  const body = $('#rbBody');
  if (st.tab === 'receipts') {
    body.innerHTML = rc.items.length ? `<div class="scroll-x"><table class="t"><thead><tr><th>Date</th><th>Receipt</th><th class="right">Amount</th><th>Settles</th><th></th></tr></thead><tbody>
      ${rc.items.map(r => `<tr><td class="nowrap small">${esc(r.date.slice(0, 10))}</td><td><b>${esc(r.reimb_payer || 'Reimbursement')}</b><div class="small muted">${esc(r.account_name || '')}${r.note ? ' · ' + esc(r.note) : ''}</div></td>
        <td class="right nowrap pos">+${money(r.amount, r.currency)}</td>
        <td class="rb-links">${r.links.map(l => `${esc(l.date.slice(0, 10))} ${esc(l.merchant_name || l.category_name_zh || l.category_name || l.note || '#' + l.expense_id)} <b>${money(l.amount, l.currency)}</b>${Math.abs(l.amount - l.expense_amount) > 0.005 ? ` <span class="muted">of ${money(l.expense_amount, l.currency)}</span>` : ''}`).join('<br>') || '<span class="muted">—</span>'}</td>
        <td><button class="btn sm danger" data-delr="${r.id}" title="Delete the receipt: the expenses become outstanding again">Delete</button></td></tr>`).join('')}</tbody></table></div>`
      : '<div class="empty">No reimbursements recorded yet</div>';
    $$('[data-delr]', body).forEach(b => b.onclick = async () => {
      if (!await confirmBox('Delete this reimbursement receipt? The linked expenses become outstanding again (the account balance changes back).')) return;
      await guard(() => api('/api/transactions/' + b.dataset.delr, {method: 'DELETE'})); toast('Receipt deleted'); route();
    });
    return;
  }
  body.innerHTML = `<form class="rb-flt" id="rbFlt" autocomplete="off">
      <select name="status">${[['open', 'Open'], ['pending', RB_ST.pending], ['partial', RB_ST.partial], ['settled', RB_ST.settled], ['written_off', RB_ST.written_off], ['all', 'All']].map(([k, l]) => `<option value="${k}" ${st.status === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <input type="date" name="start" value="${esc(st.start || '')}" aria-label="From"> – <input type="date" name="end" value="${esc(st.end || '')}" aria-label="To">
      <select name="payer"><option value="">All payers</option><option value="(none)" ${st.payer === '(none)' ? 'selected' : ''}>(no payer)</option>${d.payers.map(p => `<option ${st.payer === p ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select>
      <select name="account_id">${acctOptions(st.account_id || '', {blank: 'All accounts'})}</select>
      <span class="sp"></span><button type="button" class="btn sm" id="rbFlag">＋ Flag expenses…</button></form>
    ${d.items.length ? `<div class="scroll-x"><table class="t"><thead><tr><th><input type="checkbox" id="rbAll" aria-label="Select all"></th><th>Date</th><th>Expense</th><th>Payer</th><th class="right">Expected</th><th class="right">Received</th><th class="right">Outstanding</th><th>Status</th><th></th></tr></thead><tbody>
      ${d.items.map(t => `<tr data-id="${t.id}"><td>${t.reimb_outstanding > 0.005 ? `<input type="checkbox" data-sel="${t.id}">` : ''}</td><td class="nowrap small">${esc(t.date.slice(0, 10))}</td>
        <td><div class="ah-det"><span class="hico-s" aria-hidden="true">${catIcon(t)}</span><div>${txDesc(t)}${t.reimb_note ? `<div class="small muted">📝 ${esc(t.reimb_note)}</div>` : ''}${t.reimb_links.length ? `<div class="rb-links">${t.reimb_links.map(l => `↩ ${esc(l.date.slice(0, 10))} ${money(l.amount, t.currency)}`).join(' · ')}</div>` : ''}</div></div></td>
        <td>${esc(t.reimb_payer || '—')}</td><td class="right nowrap">${money(t.reimb_expected_amount, t.currency)}${Math.abs(t.reimb_expected_amount - t.amount) > 0.005 ? `<div class="small muted">paid ${money(t.amount, t.currency)}</div>` : ''}</td>
        <td class="right nowrap">${t.reimb_settled ? money(t.reimb_settled, t.currency) : '—'}</td><td class="right nowrap ${t.reimb_outstanding > 0 ? 'neg' : ''}"><b>${money(t.reimb_outstanding, t.currency)}</b></td>
        <td><span class="rb-st rb-${t.reimb_status}">${RB_ST[t.reimb_status]}</span></td>
        <td class="nowrap">${t.reimb_status === 'written_off' ? `<button class="btn sm" data-wo="${t.id}" data-undo="1">Undo write-off</button>` : t.reimb_status !== 'settled' ? `<button class="btn sm" data-wo="${t.id}" title="Won't be reimbursed: the unreimbursed part counts as a normal expense again">Write off</button>` : ''}
          ${t.reimb_links.length ? '' : `<button class="btn sm" data-unmark="${t.id}" title="Not reimbursable: counts as a normal expense">Unmark</button>`}</td></tr>`).join('')}</tbody></table></div>
      <div class="rb-bar"><span id="rbSelInfo" class="small muted">Select expenses to record a reimbursement</span><span class="sp"></span><button class="btn pri" id="rbRecord" disabled>💼 Record reimbursement</button></div>`
      : `<div class="empty">${st.status === 'open' ? 'Nothing outstanding 🎉 Flag an expense as reimbursable in its form, with “reimb …” / “#reimb” in quick add, or with the button above.' : 'No matching expenses'}</div>`}`;
  const f = $('#rbFlt');
  f.onchange = () => { for (const k of ['status', 'start', 'end', 'payer', 'account_id']) st[k] = f[k].value; route(); };
  $('#rbFlag').onclick = () => flagDialog();
  const items = Object.fromEntries(d.items.map(t => [t.id, t]));
  const upd = () => {
    const ch = [...sel].map(i => items[i]); const by = {};
    ch.forEach(t => by[t.currency] = (by[t.currency] || 0) + t.reimb_outstanding);
    const info = $('#rbSelInfo'); if (!info) return;
    info.innerHTML = ch.length ? `<b>${ch.length}</b> selected · ${Object.entries(by).map(([c, a]) => money(a, c)).join(' + ')}` : 'Select expenses to record a reimbursement';
    $('#rbRecord').disabled = !ch.length;
  };
  $$('[data-sel]', body).forEach(c => c.onchange = () => { c.checked ? sel.add(+c.dataset.sel) : sel.delete(+c.dataset.sel); upd(); });
  if ($('#rbAll')) $('#rbAll').onchange = e => { $$('[data-sel]', body).forEach(c => { c.checked = e.target.checked; c.onchange(); }); };
  $$('[data-wo]', body).forEach(b => b.onclick = async () => {
    const undo = !!b.dataset.undo;
    if (!undo && !await confirmBox('Write off 核销? It will not be reimbursed: the unreimbursed part counts as a normal expense again (you can undo this).')) return;
    await guard(() => api(`/api/reimbursements/${b.dataset.wo}/write-off`, {method: 'POST', body: {undo}})); toast(undo ? 'Write-off undone' : 'Written off'); route();
  });
  $$('[data-unmark]', body).forEach(b => b.onclick = async () => {
    await guard(() => api('/api/reimbursements/mark', {method: 'POST', body: {ids: [+b.dataset.unmark], reimbursable: false}})); toast('No longer reimbursable — counts as a normal expense'); route();
  });
  $$('tr[data-id] td:nth-child(3)', body).forEach(td => { td.style.cursor = 'pointer'; td.onclick = () => openTx(+td.parentElement.dataset.id, () => route()); });
  if ($('#rbRecord')) $('#rbRecord').onclick = () => recordDialog([...sel].map(i => items[i]));
};

function recordDialog(list) {
  const first = list[0];
  const payers = [...new Set(list.map(t => t.reimb_payer).filter(Boolean))];
  const curs = [...new Set(list.map(t => t.currency))];
  const bg = modal('Record reimbursement', `<form id="rbF" class="form" autocomplete="off">
    <label class="f"><span>Received into</span><select name="account_id">${acctOptions(first.account_id)}</select></label>
    <label class="f"><span>Date</span><input type="date" name="date" value="${today()}"></label>
    <label class="f"><span>Amount received <small class="muted" id="rbCur"></small></span><input name="amount" type="number" step="0.01" inputmode="decimal" placeholder="default: the sum below"></label>
    <label class="f"><span>From</span><input name="payer" list="dlPayer" value="${esc(payers.length === 1 ? payers[0] : '')}"></label>
    <label class="f full"><span>Note</span><input name="note" placeholder="e.g. expense report #12"></label>
    <div class="full"><div class="small muted" style="margin-bottom:4px">Amount settled per expense (in the expense's currency; lower it for a partial reimbursement):</div>
      <table class="t"><tbody>${list.map(t => `<tr><td class="small nowrap">${esc(t.date.slice(0, 10))}</td><td class="small">${esc(t.merchant_name || t.category_name_zh || t.category_name || t.note || '')}</td>
        <td class="right nowrap small muted">of ${money(t.reimb_outstanding, t.currency)}</td><td style="width:130px"><input type="number" step="0.01" data-amt="${t.id}" value="${t.reimb_outstanding}" max="${t.reimb_outstanding}" style="text-align:right"></td><td class="small">${t.currency}</td></tr>`).join('')}</tbody></table>
      ${curs.length > 1 ? '<div class="small muted">Several currencies: the default amount received is converted with Settings → Currencies rates; enter the real amount if it differs.</div>' : ''}</div>
  </form>`, {wide: true, buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save</button>'});
  const f = $('#rbF', bg);
  const cur = () => { const a = acctById(f.account_id.value); $('#rbCur', bg).textContent = a ? '(' + a.currency + ')' : ''; };
  f.account_id.onchange = cur; cur();
  $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    const items = $$('[data-amt]', bg).map(i => ({expense_id: +i.dataset.amt, amount: i.value === '' ? null : +i.value})).filter(x => x.amount === null || x.amount > 0);
    const r = await guard(() => api('/api/reimbursements/receipts', {method: 'POST', body: {account_id: +f.account_id.value, date: f.date.value, amount: f.amount.value || null, payer: f.payer.value || null, note: f.note.value || null, items}}));
    bg.close(); toast(`Recorded ${money(r.receipt.amount, r.receipt.currency)} · ${r.settled.filter(x => x.reimb_status === 'settled').length}/${r.settled.length} fully settled`); await loadMeta(); route();
  };
}

async function flagDialog() {
  const d = await guard(() => api('/api/transactions?kind=expense&limit=100'));
  const rows = d.items.filter(t => !t.reimbursable);
  const bg = modal('Flag expenses as reimbursable', `<div class="row" style="margin-bottom:8px"><label class="f" style="flex:1"><span>Reimburse from (optional)</span><input id="flPayer" list="dlPayer"></label></div>
    ${rows.length ? `<div class="scroll-x" style="max-height:50vh;overflow:auto"><table class="t"><tbody>${rows.map(t => `<tr><td><input type="checkbox" data-fl="${t.id}"></td><td class="small nowrap">${esc(t.date.slice(0, 10))}</td><td>${txDesc(t)}</td><td class="right nowrap">${money(t.amount, t.currency)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No recent expenses</div>'}
    <div class="small muted" style="margin-top:6px">The 100 most recent expenses. Older ones: open the transaction and tick 可报销.</div>`,
    {wide: true, buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Flag selected</button>'});
  $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    const ids = $$('[data-fl]:checked', bg).map(c => +c.dataset.fl); if (!ids.length) return;
    const r = await guard(() => api('/api/reimbursements/mark', {method: 'POST', body: {ids, reimbursable: true, payer: $('#flPayer', bg).value || null}}));
    bg.close(); toast(`Flagged ${r.updated} as`); route();
  };
}
