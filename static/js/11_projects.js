/* Projects 项目: list, add/edit, archive, detail (totals, budget plan vs actual, breakdowns, monthly chart, transactions) */
const prjQS = o => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== '' && v != null)).toString();
function prjBar(pct, over) { const w = Math.max(0, Math.min(100, pct || 0)); return `<div class="bar" style="min-width:90px"><i class="${over ? 'over' : pct > 80 ? 'warn' : ''}" style="width:${w}%"></i></div>`; }
function prjBadge(p) { return `<span class="prj-dot" style="background:${esc(p.color || '#8e5cd9')}"></span>${p.icon ? esc(p.icon) + ' ' : ''}`; }

function assignProjectDialog(ids, after) {
  const act = (S.meta.projects || []).filter(p => !p.archived);
  const bg = modal(`Assign project (${ids.length})`, `<form class="form" id="apF"><label class="f full"><span>Project</span>
    <select name="project_id"><option value="">— none (remove) —</option>${act.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label></form>`,
    {buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Assign</button>'});
  $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    const v = $('#apF', bg).project_id.value;
    const r = await guard(() => api('/api/projects/assign', {method: 'POST', body: {ids, project_id: v ? +v : null}}));
    bg.close(); toast(`${r.updated} updated`); after && after();
  };
}

function projectForm(p = {}, after) {
  const isNew = !p.id;
  const bg = modal(isNew ? 'New project' : 'Edit project', `<form class="form" id="pjF" autocomplete="off">
    <label class="f"><span>Name</span><input name="name" value="${esc(p.name || '')}" required></label>
    <label class="f"><span>Icon / color</span><span class="row"><input name="icon" maxlength="4" style="width:70px" value="${esc(p.icon || '')}" placeholder="✈️"><input name="color" type="color" value="${esc(p.color || '#8e5cd9')}" style="width:60px"></span></label>
    <label class="f"><span>Start</span><input name="start_date" type="date" value="${esc(p.start_date || '')}"></label>
    <label class="f"><span>End</span><input name="end_date" type="date" value="${esc(p.end_date || '')}"></label>
    <label class="f"><span>Overall budget (optional; blank = sum of the plan)</span><input name="budget" type="number" step="0.01" value="${p.budget ?? ''}"></label>
    <label class="f"><span>Currency</span><select name="currency"><option value="">Base ${esc(S.meta.settings.base_currency || '')}</option>${(S.meta.currencies || []).map(c => c.currency || c.code || c).filter(c => c !== S.meta.settings.base_currency).map(c => `<option ${c === p.currency ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
    <label class="f full"><span>Description</span><input name="description" value="${esc(p.description || '')}"></label>
    <label class="f full"><span>Notes</span><textarea name="note" rows="2">${esc(p.note || '')}</textarea></label>
    ${isNew ? '' : `<label class="f"><span><input type="checkbox" name="archived" ${p.archived ? 'checked' : ''}> Archived 已归档</span></label>`}</form>`,
    {wide: true, buttons: `${isNew ? '' : '<button class="btn danger" data-del>Delete</button>'}<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save</button>`});
  const f = $('#pjF', bg); $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    const o = Object.fromEntries(new FormData(f)); if (!isNew) o.archived = f.archived.checked ? 1 : 0;
    if (o.budget === '') o.budget = null;
    const r = await guard(() => api(isNew ? '/api/projects' : '/api/projects/' + p.id, {method: isNew ? 'POST' : 'PUT', body: o}));
    bg.close(); toast('Saved'); await loadMeta(); after ? after(r) : route();
  };
  const del = $('[data-del]', bg); if (del) del.onclick = async () => {
    if (!await confirmBox('Delete this project? Its transactions are kept (just unassigned).')) return;
    await guard(() => api('/api/projects/' + p.id, {method: 'DELETE'})); bg.close(); await loadMeta(); location.hash = '#/projects';
  };
}

VIEWS.projects = async (v, rest = []) => {
  if (rest[0] && /^\d+$/.test(rest[0])) return projectDetail(v, +rest[0]);
  const showArch = !!S.prjArch;
  const d = await api('/api/projects?' + prjQS({include_archived: showArch ? 'true' : '', ledger_id: S.ledger || ''}));
  v.innerHTML = `<div class="page-h"><h2>📁 Projects</h2><div class="row"><label class="small"><input type="checkbox" id="pjArch" ${showArch ? 'checked' : ''}> Show archived 显示已归档 (${d.archived_count})</label><button class="btn pri" id="pjNew">+ New project</button></div></div>
   <div class="card"><table class="t"><thead><tr><th>Project</th><th>Dates</th><th class="right">Expense</th><th class="right">Income</th><th class="right">Net</th><th>Budget</th><th class="right">Txns</th><th></th></tr></thead><tbody>
   ${d.items.map(p => { const s = p.summary; return `<tr data-p="${p.id}" style="cursor:pointer" class="${p.archived ? 'paused' : ''}">
     <td>${prjBadge(p)}<b>${esc(p.name)}</b>${p.archived ? ' <span class="pill">archived</span>' : ''}${p.description ? `<div class="small muted">${esc(p.description)}</div>` : ''}</td>
     <td class="small nowrap">${esc(p.start_date || s.first_date || '')}${(p.end_date || s.last_date) ? ' → ' + esc(p.end_date || s.last_date) : ''}</td>
     <td class="right neg">${money(s.expense, s.currency)}${s.reimbursable.count ? `<div class="small muted">+ ${money(s.reimbursable.amount, s.currency)} reimbursable</div>` : ''}</td>
     <td class="right pos">${money(s.income, s.currency)}</td><td class="right">${money(s.net, s.currency)}</td>
     <td>${s.budget != null ? `${prjBar(s.budget_pct, s.budget_remaining < 0)}<div class="small ${s.budget_remaining < 0 ? 'neg' : 'muted'}">${s.budget_pct}% of ${money(s.budget, s.currency)}</div>` : '<span class="muted small">—</span>'}</td>
     <td class="right">${s.count}</td>
     <td class="right nowrap" onclick="event.stopPropagation()"><button class="btn sm" data-ed="${p.id}">✎</button> <button class="btn sm" data-ar="${p.id}" data-un="${p.archived ? 1 : ''}">${p.archived ? 'Unarchive' : 'Archive'}</button></td></tr>`; }).join('') || '<tr><td colspan="8" class="muted">No projects yet. Create one, then add <code>#p:Name</code> in quick add or use “Assign project” on the transaction list.</td></tr>'}
   </tbody></table></div>
   <div class="small muted">Quick add: <code>#项目:日本旅行</code>, <code>#project:"Kitchen reno"</code> or <code>#p:kitchen</code> (unique prefix). Totals follow Reports: reimbursable spending is shown separately, reimbursements aren't income, transfers don't count.</div>`;
  $('#pjArch').onchange = e => { S.prjArch = e.target.checked; route(); };
  $('#pjNew').onclick = () => projectForm({}, r => { location.hash = '#/projects/' + r.id; });
  const byId = id => d.items.find(p => p.id == id);
  $$('[data-p]', v).forEach(tr => tr.onclick = () => { location.hash = '#/projects/' + tr.dataset.p; });
  $$('[data-ed]', v).forEach(b => b.onclick = () => projectForm(byId(b.dataset.ed)));
  $$('[data-ar]', v).forEach(b => b.onclick = async () => { await guard(() => api(`/api/projects/${b.dataset.ar}/archive`, {method: 'POST', body: {undo: !!b.dataset.un}})); await loadMeta(); route(); });
};

async function projectDetail(v, id) {
  const rg = S.prjRange = S.prjRange && S.prjRange.id === id ? S.prjRange : {id, start: '', end: '', parent_id: ''};
  const d = await api(`/api/projects/${id}?` + prjQS({start: rg.start, end: rg.end, ledger_id: S.ledger || '', parent_id: rg.parent_id}));
  const p = d.project, s = d.summary, cur = s.currency;
  const tbl = (rows, title) => `<div class="card"><h3>${title}</h3><table class="t"><tbody>${rows.length ? rows.map(r => `<tr><td>${esc(r.name)}</td><td class="right">${money(r.amount, cur)}</td><td class="right small muted">${r.pct}%</td></tr>`).join('') : '<tr><td class="muted">—</td></tr>'}</tbody></table></div>`;
  v.innerHTML = `<div class="page-h"><h2><a href="#/projects" class="btn sm">‹ Projects</a> ${prjBadge(p)}${esc(p.name)}${p.archived ? ' <span class="pill">archived</span>' : ''}</h2>
     <div class="row"><button class="btn" id="pdEdit">✎ Edit</button></div></div>
   ${p.description ? `<div class="muted" style="margin-bottom:6px">${esc(p.description)}</div>` : ''}
   <form class="row" id="pdRange" style="gap:6px;margin-bottom:8px;flex-wrap:wrap;align-items:center"><span class="small">Range:</span><input type="date" name="start" value="${esc(rg.start)}" style="width:auto"><span>→</span><input type="date" name="end" value="${esc(rg.end)}" style="width:auto">
     <button class="btn sm">Apply</button>${p.start_date ? '<button type="button" class="btn sm" id="pdProj">Project dates</button>' : ''}<button type="button" class="btn sm" id="pdAll">All time</button></form>
   <div class="home-cards">
     <div class="card stat"><div class="lbl">Expense</div><div class="val neg">${money(s.expense, cur)}</div><div class="small muted">${s.count} transactions</div></div>
     <div class="card stat"><div class="lbl">Income</div><div class="val pos">${money(s.income, cur)}</div></div>
     <div class="card stat"><div class="lbl">Net</div><div class="val ${s.net >= 0 ? 'pos' : 'neg'}">${money(s.net, cur)}</div></div>
     <div class="card stat"><div class="lbl">Budget</div>${s.budget != null ? `<div class="val ${s.budget_remaining < 0 ? 'neg' : ''}">${money(s.budget_remaining, cur)} <span class="small muted">left</span></div>${prjBar(s.budget_pct, s.budget_remaining < 0)}<div class="small muted">${s.budget_pct}% of ${money(s.budget, cur)}</div>` : '<div class="val muted">—</div>'}</div>
     ${s.reimbursable.count ? `<div class="card stat"><div class="lbl">Reimbursable (not in expense)</div><div class="val">${money(s.reimbursable.amount, cur)}</div><div class="small muted">outstanding ${money(s.reimbursable.outstanding, cur)}</div></div>` : ''}
   </div>
   <div id="pdPlanCard"></div>
   <div class="grid2">
     <div class="card"><h3>Spending by category ${d.drill ? `<button class="btn sm" id="pdUp">‹ all categories</button>` : '<span class="small muted">(click to drill down)</span>'}</h3><div style="height:240px"><canvas id="pdCat"></canvas></div>
       <table class="t"><tbody>${(d.drill ? d.drill.rows : d.by_category).map(r => `<tr ${d.drill ? '' : `data-drill="${r.key ?? ''}" style="cursor:pointer"`}><td>${esc(r.name)}</td><td class="right">${money(r.amount, cur)}</td><td class="right small muted">${r.pct}%</td></tr>`).join('') || '<tr><td class="muted">—</td></tr>'}</tbody></table></div>
     <div class="card"><h3>Over time</h3><div style="height:300px"><canvas id="pdMon"></canvas></div></div>
   </div>
   <div class="grid3">${tbl(d.by_account, 'By account')}${tbl(d.by_member, 'By person')}${tbl(d.by_merchant, 'By merchant')}</div>
   <div class="card"><h3>Transactions <span class="small muted" id="pdCnt"></span></h3><div id="pdTx"></div></div>`;
  const rows = d.drill ? d.drill.rows : d.by_category;
  chart('pdCat', {type: 'doughnut', data: {labels: rows.map(r => r.name), datasets: [{data: rows.map(r => r.amount), backgroundColor: PALETTE}]}, options: {onClick: (e, el) => { if (!d.drill && el.length) { const k = rows[el[0].index].key; if (k) { rg.parent_id = k; route(); } } }}});
  chart('pdMon', {type: 'bar', data: {labels: d.monthly.map(m => m.month), datasets: [{label: 'Expense', data: d.monthly.map(m => m.expense), backgroundColor: '#e8553e'}, {label: 'Income', data: d.monthly.map(m => m.income), backgroundColor: '#14a06f'}]}});
  $$('[data-drill]', v).forEach(tr => tr.onclick = () => { if (tr.dataset.drill) { rg.parent_id = tr.dataset.drill; route(); } });
  if ($('#pdUp')) $('#pdUp').onclick = () => { rg.parent_id = ''; route(); };
  $('#pdRange').onsubmit = e => { e.preventDefault(); rg.start = e.target.start.value; rg.end = e.target.end.value; route(); };
  $('#pdAll').onclick = () => { rg.start = rg.end = ''; route(); };
  if ($('#pdProj')) $('#pdProj').onclick = () => { rg.start = p.start_date || ''; rg.end = p.end_date || ''; route(); };
  $('#pdEdit').onclick = () => projectForm(p);
  planCard($('#pdPlanCard', v), p, d.plan_lines, cur, rg);
  const base = [['project_id', id], ...(rg.start ? [['start', rg.start]] : []), ...(rg.end ? [['end', rg.end]] : []), ...(S.ledger ? [['ledger_id', S.ledger]] : [])];
  $('#pdTx').innerHTML = txTable([], {tbodyId: 'pdBody'});
  infiniteList($('#pdTx'), {
    load: c => api('/api/transactions?' + new URLSearchParams([...base, ['limit', '50'], ...(c ? [['before', c]] : [])])),
    append: (r, first) => { if (first) $('#pdCnt').textContent = `${r.total} 笔`; $('#pdBody').insertAdjacentHTML('beforeend', txRowsHtml(r.items)); bindTxRows($('#pdTx'), () => route()); },
  });
}

/* ---- plan lines 预算明细: plan vs actual per line, expand to transactions, inline add/edit/reorder, templates, copy */
const expCats = () => (S.meta.categories || []).filter(c => c.kind === 'expense');
const catPick = c => (I18N.lang.startsWith('zh') && c.name_zh) ? c.name_zh : c.name;  // category names in the UI language
const catName = id => { if (id == null) return t('Uncategorized'); for (const c of expCats()) { if (c.id === id) return catPick(c); for (const k of c.children || []) if (k.id === id) return catPick(c) + ' › ' + catPick(k); } return '#' + id; };
function catMultiOptions(sel = []) {
  return expCats().map(c => `<option value="${c.id}" ${sel.includes(c.id) ? 'selected' : ''}>${esc(c.label || c.name)}</option>` +
    (c.children || []).map(k => `<option value="${k.id}" ${sel.includes(k.id) ? 'selected' : ''}>\u00a0\u00a0\u00a0${esc(k.label || k.name)}</option>`).join('')).join('');
}
function curOptions(sel, projCur) {
  const base = S.meta.settings.base_currency, all = [...new Set([projCur || base, base, ...(S.meta.currencies || []).map(c => c.currency || c.code || c)])];
  return all.map(c => `<option ${c === (sel || projCur || base) ? 'selected' : ''}>${esc(c)}</option>`).join('');
}

function planCard(el, p, pl, cur, rg) {
  const pcur = p.currency || S.meta.settings.base_currency;
  const head = `<h3>Budget plan <span class="small muted">${pl ? (pl.budget_source === 'explicit' ? '(overall budget set on the project)' : '(total = sum of lines)') : ''}</span>
    <span class="sp"></span><button class="btn sm" id="plAdd">+ Plan line</button> <button class="btn sm" id="plTpl">📋 Template</button> <button class="btn sm" id="plCopy">⧉ Copy plan</button></h3>`;
  if (!pl) {
    el.innerHTML = `<div class="card">${head}<div class="small muted">No budget plan yet. Add lines such as Flights, Hotel and Meals, start from a template, or copy another project's plan. Each line can be matched by categories and/or by assigning transactions to it.</div><table class="t"><tbody id="plBody"></tbody></table></div>`;
  } else {
    const row = (l, i, n) => `<tr class="pl-row ${l.over ? 'over-row' : ''}" data-l="${l.id}" style="cursor:pointer">
      <td class="nowrap" onclick="event.stopPropagation()"><button class="btn sm" data-up="${l.id}" ${i ? '' : 'disabled'} title="Move up">↑</button><button class="btn sm" data-dn="${l.id}" ${i < n - 1 ? '' : 'disabled'} title="Move down">↓</button></td>
      <td><span class="pl-caret">▸</span> <b data-notr>${esc(l.name)}</b>${l.catch_all ? ' <span class="pill">catches the rest</span>' : ''}
        ${l.category_ids.length ? `<div class="small muted" data-notr>${l.category_ids.map(c => esc(catName(c))).join(', ')}</div>` : ''}${l.note ? `<div class="small muted" data-notr>${esc(l.note)}</div>` : ''}</td>
      <td class="right">${money(l.amount, l.currency)}${l.currency !== cur ? `<div class="small muted">≈ ${money(l.planned_base, cur)}</div>` : ''}</td>
      <td class="right">${money(l.actual, cur)}<div class="small muted">${l.count} txns</div></td>
      <td class="right ${l.remaining < 0 ? 'neg' : ''}">${money(l.remaining, cur)}</td>
      <td>${l.pct != null ? `${prjBar(l.pct, l.over)}<span class="small ${l.over ? 'neg' : 'muted'}">${l.pct}%${l.over ? ' · over' : ''}</span>` : '<span class="small muted">—</span>'}</td>
      <td class="right nowrap" onclick="event.stopPropagation()"><button class="btn sm" data-ed="${l.id}" title="Edit">✎</button><button class="btn sm danger" data-rm="${l.id}" title="Delete">🗑</button></td></tr>`;
    const un = pl.unplanned;
    el.innerHTML = `<div class="card">${head}<table class="t pl-table"><thead><tr><th style="width:64px"></th><th>Plan line</th><th class="right">Planned</th><th class="right">Actual</th><th class="right">Left</th><th style="width:170px">Used</th><th></th></tr></thead><tbody id="plBody">
      ${pl.lines.map((l, i) => row(l, i, pl.lines.length)).join('')}
      ${un.actual || un.count ? `<tr class="pl-row" data-l="unplanned" style="cursor:pointer"><td></td><td><span class="pl-caret">▸</span> <i>Unplanned</i><div class="small muted">${un.by_category.map(u => `<span data-notr>${esc(catName(u.category_id))}</span> ${money(u.actual, cur)}`).join(' · ')}</div></td><td class="right muted">—</td><td class="right">${money(un.actual, cur)}<div class="small muted">${un.count} txns</div></td><td></td><td></td><td></td></tr>` : ''}
      <tr><td></td><td><b>Total</b></td><td class="right"><b>${money(pl.budget, cur)}</b>${pl.budget_source === 'explicit' ? `<div class="small muted">lines ${money(pl.plan_total, cur)}</div>` : ''}</td><td class="right"><b>${money(pl.actual, cur)}</b></td>
        <td class="right ${pl.remaining < 0 ? 'neg' : ''}"><b>${money(pl.remaining, cur)}</b></td><td>${pl.pct != null ? `${prjBar(pl.pct, pl.over)}<span class="small ${pl.over ? 'neg' : 'muted'}">${pl.pct}%</span>` : ''}</td><td></td></tr></tbody></table>
      <div class="small muted">Click a line to see its transactions. A transaction assigned to a line counts there; otherwise it goes to the first line mapped to its category, then to a “catches the rest” line, else Unplanned. Quick add: <code>#p:Project/Line</code>.</div></div>`;
  }
  const reload = () => route();
  $('#plAdd', el).onclick = () => planLineEditor(el, p, pcur, null, reload);
  $('#plTpl', el).onclick = () => planTemplateDialog(p, !!pl, reload);
  $('#plCopy', el).onclick = () => planCopyDialog(p, !!pl, reload);
  if (!pl) return;
  const ids = pl.lines.map(l => l.id);
  const move = async (id, d) => { const i = ids.indexOf(id), j = i + d; [ids[i], ids[j]] = [ids[j], ids[i]];
    await guard(() => api(`/api/projects/${p.id}/plan-lines/reorder`, {method: 'POST', body: {ids}})); reload(); };
  $$('[data-up]', el).forEach(b => b.onclick = () => move(+b.dataset.up, -1));
  $$('[data-dn]', el).forEach(b => b.onclick = () => move(+b.dataset.dn, 1));
  $$('[data-ed]', el).forEach(b => b.onclick = () => planLineEditor(el, p, pcur, pl.lines.find(l => l.id === +b.dataset.ed), reload));
  $$('[data-rm]', el).forEach(b => b.onclick = async () => {
    const l = pl.lines.find(x => x.id === +b.dataset.rm);
    if (!await confirmBox(`Delete plan line “${l.name}”? Transactions assigned to it are kept (they fall back to category matching).`)) return;
    await guard(() => api(`/api/projects/${p.id}/plan-lines/${l.id}`, {method: 'DELETE'})); await loadMeta(); reload();
  });
  $$('tr.pl-row', el).forEach(tr => tr.onclick = async () => {
    const nx = tr.nextElementSibling;
    if (nx && nx.classList.contains('pl-tx')) { nx.remove(); $('.pl-caret', tr).textContent = '▸'; return; }
    $('.pl-caret', tr).textContent = '▾';
    const r = await guard(() => api(`/api/projects/${p.id}/plan-lines/${tr.dataset.l}/transactions?` + prjQS({start: rg.start, end: rg.end, ledger_id: S.ledger || ''})));
    tr.insertAdjacentHTML('afterend', `<tr class="pl-tx"><td></td><td colspan="6">${r.transactions.length ? txTable(r.transactions, {tbodyId: 'plTx' + tr.dataset.l}) : '<div class="small muted">No transactions yet.</div>'}</td></tr>`);
    const box = tr.nextElementSibling; if (r.transactions.length) bindTxRows(box, () => reload());
  });
}

function planLineEditor(el, p, pcur, l, after) {
  l = l || {name: '', amount: '', currency: pcur, note: '', category_ids: [], catch_all: false};
  const bg = modal(l.id ? 'Edit plan line' : 'New plan line', `<form class="form" id="plF" autocomplete="off">
    <label class="f"><span>Name</span><input name="name" value="${esc(l.name)}" placeholder="Flights" required maxlength="80"></label>
    <label class="f"><span>Planned amount</span><span class="row"><input name="amount" type="number" step="0.01" min="0" value="${l.amount ?? ''}" style="flex:1"><select name="currency" style="width:90px">${curOptions(l.currency, pcur)}</select></span></label>
    <label class="f full"><span>Matched categories (optional; Ctrl/⌘-click for several)</span><select name="category_ids" multiple size="8">${catMultiOptions(l.category_ids)}</select></label>
    <label class="f full"><span>Notes</span><input name="note" value="${esc(l.note || '')}"></label>
    <label class="f full"><span><input type="checkbox" name="catch_all" ${l.catch_all ? 'checked' : ''}> Catches the rest: project spending no other line matches counts here</span></label></form>`,
    {wide: true, buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save</button>'});
  const f = $('#plF', bg); $('[data-x2]', bg).onclick = bg.close; f.name.focus();
  $('[data-ok]', bg).onclick = async () => {
    const body = {name: f.name.value, amount: f.amount.value === '' ? 0 : +f.amount.value, currency: f.currency.value, note: f.note.value,
      catch_all: f.catch_all.checked, category_ids: [...f.category_ids.selectedOptions].map(o => +o.value)};
    await guard(() => api(`/api/projects/${p.id}/plan-lines` + (l.id ? '/' + l.id : ''), {method: l.id ? 'PUT' : 'POST', body}));
    bg.close(); toast('Saved'); await loadMeta(); after();
  };
}

const tplName = n => { const k = 'plan template: ' + n, v = t(k); return v === k ? n : v; };  // template names in the UI language
async function planTemplateDialog(p, hasPlan, after) {
  const {templates} = await api('/api/plan-templates');
  const pcur = p.currency || S.meta.settings.base_currency;
  const bg = modal('Plan from a template', `<div class="row" style="gap:6px;margin-bottom:8px"><select id="tpSel" data-notr>${templates.map(t => `<option value="${t.key}">${esc(tplName(t.name))}</option>`).join('')}</select>
      ${hasPlan ? '<label class="small"><input type="checkbox" id="tpRep"> Replace the current lines</label>' : ''}</div>
    <div class="small muted">Suggested category matches come from your own categories (by name). Adjust amounts now and edit the lines afterwards.</div>
    <div style="max-height:55vh;overflow:auto"><table class="t"><thead><tr><th>Plan line</th><th>Matched categories</th><th class="right">Planned (${esc(pcur)})</th></tr></thead><tbody id="tpBody"></tbody></table></div>`,
    {wide: true, buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Add lines</button>'});
  const show = () => { const t = templates.find(x => x.key === $('#tpSel', bg).value);
    $('#tpBody', bg).innerHTML = t.lines.map(l => `<tr><td><b data-notr>${esc(tplName(l.name))}</b></td><td class="small muted">${l.catch_all ? 'catches the rest' : l.category_ids.length ? `<span data-notr>${l.category_ids.map(c => esc(catName(c))).join(', ')}</span>` : '—'}</td>
      <td class="right"><input type="number" step="0.01" min="0" data-amt="${esc(l.name)}" style="width:110px;text-align:right"></td></tr>`).join(''); };
  $('#tpSel', bg).onchange = show; show();
  $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    const amounts = Object.fromEntries($$('[data-amt]', bg).filter(i => i.value !== '').map(i => [i.dataset.amt, +i.value]));
    const names = Object.fromEntries($$('[data-amt]', bg).map(i => [i.dataset.amt, tplName(i.dataset.amt)]));
    await guard(() => api(`/api/projects/${p.id}/plan-lines/template`, {method: 'POST', body: {template: $('#tpSel', bg).value, amounts, names, replace: !!($('#tpRep', bg) || {}).checked}}));
    bg.close(); toast('Plan lines added'); await loadMeta(); after();
  };
}

async function planCopyDialog(p, hasPlan, after) {
  const all = (await api('/api/projects?include_archived=true&summary=false')).filter(x => x.id !== p.id);
  const bg = modal('Copy plan from another project', `<form class="form" id="cpF"><label class="f full"><span>Project</span><select name="from">${all.map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select></label>
    <label class="f"><span>Scale amounts ×</span><input name="scale" type="number" step="0.1" value="1"></label>
    ${hasPlan ? '<label class="f"><span><input type="checkbox" name="replace" checked> Replace the current lines</span></label>' : ''}</form>`,
    {buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Copy</button>'});
  const f = $('#cpF', bg); $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    if (!f.from.value) return toast('Choose a project', true);
    await guard(() => api(`/api/projects/${p.id}/plan-lines/copy`, {method: 'POST', body: {from_project_id: +f.from.value, scale: +f.scale.value || 1, replace: f.replace ? f.replace.checked : false}}));
    bg.close(); toast('Plan copied'); await loadMeta(); after();
  };
}
