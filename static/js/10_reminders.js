/* Reminder Center 提醒中心: card statements + remind-only schedules */
const REM_STATE = {upcoming: 'Upcoming', overdue: 'Overdue', snoozed: 'Snoozed', later: 'Later', done: 'Done'};
function remWhen(r) {
  if (r.state === 'done') return `<span class="pill">${esc(r.done_reason || 'done')}</span>`;
  if (r.state === 'overdue') return `<span class="pill warn">逾期 ${-r.days_left}d overdue</span>`;
  return r.days_left === 0 ? '<span class="pill warn">today</span>' : `<span class="pill">${r.days_left}d</span>`;
}
function remLine(r) {
  const amt = r.remaining ?? r.statement_amount;
  return `<div class="row" style="justify-content:space-between;padding:4px 0"><span>${r.kind === 'card' ? '💳' : '🔁'} <b>${esc(r.title || '')}</b> <span class="small muted">${esc(r.due_date)}</span> ${remWhen(r)}</span><b>${amt ? money(amt, r.currency) : ''}</b></div>`;
}
VIEWS.reminders = async (v) => {
  const view = S.remView || 'open';
  const d = await api('/api/reminders?view=' + view);
  const tabs = [['open', 'Active'], ['all_upcoming', 'All upcoming'], ['overdue', REM_STATE.overdue], ['snoozed', REM_STATE.snoozed], ['done', REM_STATE.done], ['all', 'All']];
  const st = S.meta.settings;
  v.innerHTML = `<div class="page-h"><h2>⏰ Reminders</h2><div class="row"><button class="btn" id="rmRun">Refresh</button></div></div>
   <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:8px">${tabs.map(([k, l]) => `<button class="btn sm ${k === view ? 'pri' : ''}" data-v="${k}">${l}${d.counts[k] ? ` (${d.counts[k]})` : ''}</button>`).join('')}</div>
   <div class="card"><table class="t"><thead><tr><th>Due</th><th>Item</th><th class="right">Statement</th><th class="right">Remaining</th><th></th></tr></thead><tbody>
   ${d.items.map(r => `<tr><td class="nowrap">${esc(r.due_date)}<div>${remWhen(r)}</div></td>
     <td>${r.kind === 'card' ? '💳' : '🔁'} <b>${esc(r.title || '')}</b>${r.account_last4 ? ` <span class="small muted">…${esc(r.account_last4)}</span>` : ''}
       <div class="small muted">${r.kind === 'card' ? `Statement ${esc(r.statement_date)}` : 'Scheduled payment'}${r.autopay ? ' · autopay' : ''}${r.reference ? ' · #' + esc(r.reference) : ''}${r.minimum ? ' · min ' + money(r.minimum, r.currency) : ''}${r.note ? ' · ' + esc(r.note) : ''}</div></td>
     <td class="right">${r.statement_amount != null ? money(r.statement_amount, r.currency) : '—'}${r.amount_override != null ? ' <span class="small muted">(edited)</span>' : ''}</td>
     <td class="right">${r.remaining != null ? money(r.remaining, r.currency) : ''}${r.paid ? `<div class="small muted">paid ${money(r.paid, r.currency)}</div>` : ''}</td>
     <td class="right nowrap">${r.state === 'done' ? `<button class="btn sm" data-reopen="${r.id}">Reopen</button>` : `<button class="btn sm pri" data-paid="${r.id}">Paid</button> <button class="btn sm" data-done="${r.id}">✓</button> <button class="btn sm" data-snz="${r.id}">Snooze</button> <button class="btn sm" data-dis="${r.id}">✕</button>`} <button class="btn sm" data-ed="${r.id}">✎</button></td></tr>`).join('') || '<tr><td colspan="5" class="muted">Nothing here. Set a statement/due day on a credit card, or a schedule to “Remind only”.</td></tr>'}
   </tbody></table></div>
   <div class="card small"><b>Settings</b> · Show reminders <input id="rmLead" type="number" min="0" max="365" style="width:70px;display:inline-block" value="${esc(st.reminder_lead_days || '10')}"> days before due (default; cards/schedules can override) ·
     Notify <select id="rmNtf" style="width:auto;display:inline-block"><option value="off">Off</option><option value="telegram" ${st.reminder_notify === 'telegram' ? 'selected' : ''}>Telegram</option></select> <button class="btn sm" id="rmSave">Save</button>
     <div class="muted">iPhone: Shortcut → GET /api/reminders/notify (see README).</div></div>`;
  $$('[data-v]', v).forEach(b => b.onclick = () => { S.remView = b.dataset.v; route(); });
  const act = async (p, body) => { await guard(() => api('/api/reminders/' + p, {method: 'POST', body: body || {}})); await loadMeta(); route(); };
  $('#rmRun').onclick = async () => { const r = await guard(() => api('/api/reminders/run', {method: 'POST'})); toast(`${r.created} new · ${r.auto_completed} auto-completed`); await loadMeta(); route(); };
  $('#rmSave').onclick = async () => { await guard(() => api('/api/settings', {method: 'PUT', body: {reminder_lead_days: $('#rmLead').value, reminder_notify: $('#rmNtf').value}})); await loadMeta(); toast('Saved'); route(); };
  $$('[data-done]', v).forEach(b => b.onclick = () => act(b.dataset.done + '/done'));
  $$('[data-dis]', v).forEach(b => b.onclick = () => act(b.dataset.dis + '/dismiss'));
  $$('[data-reopen]', v).forEach(b => b.onclick = () => act(b.dataset.reopen + '/reopen'));
  $$('[data-snz]', v).forEach(b => b.onclick = async () => { const n = prompt('Snooze for how many days?', '3'); if (n) act(b.dataset.snz + '/snooze', {days: +n}); });
  const byId = id => d.items.find(r => r.id == id);
  $$('[data-paid]', v).forEach(b => b.onclick = () => remPaid(byId(b.dataset.paid)));
  $$('[data-ed]', v).forEach(b => b.onclick = () => remEdit(byId(b.dataset.ed)));
};
function remPaid(r) {
  const card = r.kind === 'card';
  const bg = modal('Mark paid', `<form class="form" id="rpF">
    <label class="f full"><span><input type="checkbox" name="create" checked> Record the payment 记录还款${card ? ' (transfer to the card 转账到信用卡)' : ''}</span></label>
    <label class="f"><span>From</span><select name="from">${acctOptions(null, {blank: card ? '— choose —' : '(schedule default)', filter: a => a.id !== r.account_id})}</select></label>
    <label class="f"><span>Amount (${esc(r.currency || '')})</span><input name="amount" type="number" step="0.01" value="${r.remaining ?? r.statement_amount ?? ''}"></label>
    <label class="f"><span>Date</span><input name="date" type="date" value="${today()}"></label></form>`,
    {buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save</button>'});
  const f = $('#rpF', bg); $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    await guard(() => api(`/api/reminders/${r.id}/paid`, {method: 'POST', body: {create_payment: f.create.checked, from_account_id: f.from.value ? +f.from.value : null, amount: f.amount.value, date: f.date.value}}));
    bg.close(); toast('Marked paid'); await loadMeta(); route();
  };
}
function remEdit(r) {
  const bg = modal('Edit reminder', `<form class="form" id="reF">
    <label class="f"><span>Statement amount</span><input name="amount_override" type="number" step="0.01" value="${r.amount_override ?? ''}" placeholder="computed ${r.amount ?? ''}"></label>
    <label class="f"><span>Minimum</span><input name="minimum" type="number" step="0.01" value="${r.minimum ?? ''}"></label>
    <label class="f"><span>Statement no.</span><input name="reference" value="${esc(r.reference || '')}"></label>
    <label class="f"><span>Due date</span><input name="due_date" type="date" value="${esc(r.due_date)}"></label>
    <label class="f full"><span>Note</span><input name="note" value="${esc(r.note || '')}"></label></form>`,
    {buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save</button>'});
  const f = $('#reF', bg); $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => { await guard(() => api('/api/reminders/' + r.id, {method: 'PUT', body: Object.fromEntries(new FormData(f))})); bg.close(); toast('Saved'); await loadMeta(); route(); };
}
