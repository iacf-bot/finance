/* Projects 项目: list, add/edit, archive, detail (totals, budget plan vs actual, breakdowns, monthly chart, transactions) */
const prjQS = o => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== '' && v != null)).toString();
function prjBar(pct, over) { const w = Math.max(0, Math.min(100, pct || 0)); return `<div class="bar" style="min-width:90px"><i class="${over ? 'over' : pct > 80 ? 'warn' : ''}" style="width:${w}%"></i></div>`; }
function prjBadge(p) { return `<span class="prj-dot" style="background:${esc(p.color || '#8e5cd9')}"></span>${p.icon ? esc(p.icon) + ' ' : ''}`; }

function assignProjectDialog(ids, after) {
  const act = (S.meta.projects || []).filter(p => !p.archived);
  const bg = modal(`Assign project 归入项目 (${ids.length})`, `<form class="form" id="apF"><label class="f full"><span>Project 项目</span>
    <select name="project_id"><option value="">— none (remove) 无 —</option>${act.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label></form>`,
    {buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Assign 归入</button>'});
  $('[data-x2]', bg).onclick = bg.close;
  $('[data-ok]', bg).onclick = async () => {
    const v = $('#apF', bg).project_id.value;
    const r = await guard(() => api('/api/projects/assign', {method: 'POST', body: {ids, project_id: v ? +v : null}}));
    bg.close(); toast(`${r.updated} updated`); after && after();
  };
}

function projectForm(p = {}, after) {
  const isNew = !p.id;
  const bg = modal(isNew ? 'New project 新建项目' : 'Edit project 编辑项目', `<form class="form" id="pjF" autocomplete="off">
    <label class="f"><span>Name 名称</span><input name="name" value="${esc(p.name || '')}" required></label>
    <label class="f"><span>Icon / color 图标/颜色</span><span class="row"><input name="icon" maxlength="4" style="width:70px" value="${esc(p.icon || '')}" placeholder="✈️"><input name="color" type="color" value="${esc(p.color || '#8e5cd9')}" style="width:60px"></span></label>
    <label class="f"><span>Start 开始</span><input name="start_date" type="date" value="${esc(p.start_date || '')}"></label>
    <label class="f"><span>End 结束</span><input name="end_date" type="date" value="${esc(p.end_date || '')}"></label>
    <label class="f"><span>Overall budget 总预算 (optional; blank = sum of the plan)</span><input name="budget" type="number" step="0.01" value="${p.budget ?? ''}"></label>
    <label class="f"><span>Currency 币种</span><select name="currency"><option value="">Base ${esc(S.meta.settings.base_currency || '')}</option>${(S.meta.currencies || []).map(c => c.currency || c.code || c).filter(c => c !== S.meta.settings.base_currency).map(c => `<option ${c === p.currency ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
    <label class="f full"><span>Description 描述</span><input name="description" value="${esc(p.description || '')}"></label>
    <label class="f full"><span>Notes 备注</span><textarea name="note" rows="2">${esc(p.note || '')}</textarea></label>
    ${isNew ? '' : `<label class="f"><span><input type="checkbox" name="archived" ${p.archived ? 'checked' : ''}> Archived 已归档</span></label>`}</form>`,
    {wide: true, buttons: `${isNew ? '' : '<button class="btn danger" data-del>Delete</button>'}<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save 保存</button>`});
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
  v.innerHTML = `<div class="page-h"><h2>📁 Projects 项目</h2><div class="row"><label class="small"><input type="checkbox" id="pjArch" ${showArch ? 'checked' : ''}> Show archived 显示已归档 (${d.archived_count})</label><button class="btn pri" id="pjNew">+ New project</button></div></div>
   <div class="card"><table class="t"><thead><tr><th>Project 项目</th><th>Dates 日期</th><th class="right">Expense 支出</th><th class="right">Income 收入</th><th class="right">Net 结余</th><th>Budget 预算</th><th class="right">Txns 笔</th><th></th></tr></thead><tbody>
   ${d.items.map(p => { const s = p.summary; return `<tr data-p="${p.id}" style="cursor:pointer" class="${p.archived ? 'paused' : ''}">
     <td>${prjBadge(p)}<b>${esc(p.name)}</b>${p.archived ? ' <span class="pill">archived 已归档</span>' : ''}${p.description ? `<div class="small muted">${esc(p.description)}</div>` : ''}</td>
     <td class="small nowrap">${esc(p.start_date || s.first_date || '')}${(p.end_date || s.last_date) ? ' → ' + esc(p.end_date || s.last_date) : ''}</td>
     <td class="right neg">${money(s.expense, s.currency)}${s.reimbursable.count ? `<div class="small muted">+ ${money(s.reimbursable.amount, s.currency)} reimbursable 可报销</div>` : ''}</td>
     <td class="right pos">${money(s.income, s.currency)}</td><td class="right">${money(s.net, s.currency)}</td>
     <td>${s.budget != null ? `${prjBar(s.budget_pct, s.budget_remaining < 0)}<div class="small ${s.budget_remaining < 0 ? 'neg' : 'muted'}">${s.budget_pct}% of ${money(s.budget, s.currency)}</div>` : '<span class="muted small">—</span>'}</td>
     <td class="right">${s.count}</td>
     <td class="right nowrap" onclick="event.stopPropagation()"><button class="btn sm" data-ed="${p.id}">✎</button> <button class="btn sm" data-ar="${p.id}" data-un="${p.archived ? 1 : ''}">${p.archived ? 'Unarchive' : 'Archive'}</button></td></tr>`; }).join('') || '<tr><td colspan="8" class="muted">No projects yet. Create one, then add <code>#p:Name</code> in quick add or use “Assign project” on the transaction list.</td></tr>'}
   </tbody></table></div>
   <div class="small muted">Quick add: <code>#项目:日本旅行</code>, <code>#project:"Kitchen reno"</code> or <code>#p:kitchen</code> (unique prefix). Totals follow Reports: reimbursable 可报销 spending is shown separately, reimbursements aren't income, transfers don't count.</div>`;
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
  const p = d.project, s = d.summary, cur = s.currency, pl = d.plan;
  const tbl = (rows, title) => `<div class="card"><h3>${title}</h3><table class="t"><tbody>${rows.length ? rows.map(r => `<tr><td>${esc(r.name)}</td><td class="right">${money(r.amount, cur)}</td><td class="right small muted">${r.pct}%</td></tr>`).join('') : '<tr><td class="muted">—</td></tr>'}</tbody></table></div>`;
  v.innerHTML = `<div class="page-h"><h2><a href="#/projects" class="btn sm">‹ Projects</a> ${prjBadge(p)}${esc(p.name)}${p.archived ? ' <span class="pill">archived 已归档</span>' : ''}</h2>
     <div class="row"><button class="btn" id="pdEdit">✎ Edit</button><button class="btn" id="pdPlan">🎯 Budget plan 预算计划</button></div></div>
   ${p.description ? `<div class="muted" style="margin-bottom:6px">${esc(p.description)}</div>` : ''}
   <form class="row" id="pdRange" style="gap:6px;margin-bottom:8px;flex-wrap:wrap;align-items:center"><span class="small">Range 时间:</span><input type="date" name="start" value="${esc(rg.start)}" style="width:auto"><span>→</span><input type="date" name="end" value="${esc(rg.end)}" style="width:auto">
     <button class="btn sm">Apply</button>${p.start_date ? '<button type="button" class="btn sm" id="pdProj">Project dates 项目日期</button>' : ''}<button type="button" class="btn sm" id="pdAll">All time 全部</button></form>
   <div class="home-cards">
     <div class="card stat"><div class="lbl">Expense 支出</div><div class="val neg">${money(s.expense, cur)}</div><div class="small muted">${s.count} transactions 笔</div></div>
     <div class="card stat"><div class="lbl">Income 收入</div><div class="val pos">${money(s.income, cur)}</div></div>
     <div class="card stat"><div class="lbl">Net 结余</div><div class="val ${s.net >= 0 ? 'pos' : 'neg'}">${money(s.net, cur)}</div></div>
     <div class="card stat"><div class="lbl">Budget 预算</div>${s.budget != null ? `<div class="val ${s.budget_remaining < 0 ? 'neg' : ''}">${money(s.budget_remaining, cur)} <span class="small muted">left 剩余</span></div>${prjBar(s.budget_pct, s.budget_remaining < 0)}<div class="small muted">${s.budget_pct}% of ${money(s.budget, cur)}</div>` : '<div class="val muted">—</div>'}</div>
     ${s.reimbursable.count ? `<div class="card stat"><div class="lbl">Reimbursable 可报销 (not in expense)</div><div class="val">${money(s.reimbursable.amount, cur)}</div><div class="small muted">outstanding 待报销 ${money(s.reimbursable.outstanding, cur)}</div></div>` : ''}
   </div>
   ${pl ? `<div class="card"><h3>Plan vs actual 预算 vs 实际 <span class="small muted">(${pl.budget_source === 'explicit' ? 'overall budget set on the project' : 'total = sum of plan'})</span></h3>
     <table class="t"><thead><tr><th>Category 分类</th><th class="right">Planned 计划</th><th class="right">Actual 实际</th><th class="right">Remaining 剩余</th><th style="width:180px">Used 已用</th></tr></thead><tbody>
     ${pl.rows.map(r => `<tr class="${r.over ? 'over-row' : ''}"><td style="padding-left:${r.level ? 22 : 6}px">${r.level ? '' : '<b>'}${esc(r.name)}${r.level ? '' : '</b>'}${r.rollup ? ' <span class="small muted">(sum of sub-categories)</span>' : ''}${r.note ? `<div class="small muted">${esc(r.note)}</div>` : ''}</td>
       <td class="right">${r.unplanned ? '<span class="muted">—</span>' : money(r.planned, cur)}</td><td class="right">${money(r.actual, cur)}</td>
       <td class="right ${r.remaining < 0 ? 'neg' : ''}">${r.unplanned ? '' : money(r.remaining, cur)}</td><td>${r.pct != null ? `${prjBar(r.pct, r.over)}<span class="small ${r.over ? 'neg' : 'muted'}">${r.pct}%${r.over ? ' over 超支' : ''}</span>` : ''}</td></tr>`).join('')}
     <tr><td><b>Total 合计</b></td><td class="right"><b>${money(pl.budget, cur)}</b></td><td class="right"><b>${money(pl.actual, cur)}</b></td><td class="right ${pl.remaining < 0 ? 'neg' : ''}"><b>${money(pl.remaining, cur)}</b></td><td>${prjBar(pl.pct, pl.over)}<span class="small ${pl.over ? 'neg' : 'muted'}">${pl.pct ?? ''}%</span></td></tr></tbody></table>
     ${pl.unplanned.length ? `<div class="small" style="margin-top:6px"><b>Spending without a plan 无计划的支出:</b> ${pl.unplanned.map(u => `${esc(u.name)} ${money(u.actual, cur)}`).join(' · ')} <span class="muted">(${pl.rows.some(r => r.other) ? 'counted in Other' : 'add an Other line or plan these categories'})</span></div>` : ''}</div>`
     : `<div class="card small muted">No budget plan yet. <button class="btn sm" id="pdPlan2">🎯 Plan by category 按分类做预算</button></div>`}
   <div class="grid2">
     <div class="card"><h3>Spending by category 分类支出 ${d.drill ? `<button class="btn sm" id="pdUp">‹ all categories</button>` : '<span class="small muted">(click to drill down)</span>'}</h3><div style="height:240px"><canvas id="pdCat"></canvas></div>
       <table class="t"><tbody>${(d.drill ? d.drill.rows : d.by_category).map(r => `<tr ${d.drill ? '' : `data-drill="${r.key ?? ''}" style="cursor:pointer"`}><td>${esc(r.name)}</td><td class="right">${money(r.amount, cur)}</td><td class="right small muted">${r.pct}%</td></tr>`).join('') || '<tr><td class="muted">—</td></tr>'}</tbody></table></div>
     <div class="card"><h3>Over time 按月</h3><div style="height:300px"><canvas id="pdMon"></canvas></div></div>
   </div>
   <div class="grid3">${tbl(d.by_account, 'By account 按账户')}${tbl(d.by_member, 'By person 按成员')}${tbl(d.by_merchant, 'By merchant 按商家')}</div>
   <div class="card"><h3>Transactions 流水 <span class="small muted" id="pdCnt"></span></h3><div id="pdTx"></div></div>`;
  const rows = d.drill ? d.drill.rows : d.by_category;
  chart('pdCat', {type: 'doughnut', data: {labels: rows.map(r => r.name), datasets: [{data: rows.map(r => r.amount), backgroundColor: PALETTE}]}, options: {onClick: (e, el) => { if (!d.drill && el.length) { const k = rows[el[0].index].key; if (k) { rg.parent_id = k; route(); } } }}});
  chart('pdMon', {type: 'bar', data: {labels: d.monthly.map(m => m.month), datasets: [{label: 'Expense 支出', data: d.monthly.map(m => m.expense), backgroundColor: '#e8553e'}, {label: 'Income 收入', data: d.monthly.map(m => m.income), backgroundColor: '#14a06f'}]}});
  $$('[data-drill]', v).forEach(tr => tr.onclick = () => { if (tr.dataset.drill) { rg.parent_id = tr.dataset.drill; route(); } });
  if ($('#pdUp')) $('#pdUp').onclick = () => { rg.parent_id = ''; route(); };
  $('#pdRange').onsubmit = e => { e.preventDefault(); rg.start = e.target.start.value; rg.end = e.target.end.value; route(); };
  $('#pdAll').onclick = () => { rg.start = rg.end = ''; route(); };
  if ($('#pdProj')) $('#pdProj').onclick = () => { rg.start = p.start_date || ''; rg.end = p.end_date || ''; route(); };
  $('#pdEdit').onclick = () => projectForm(p);
  [$('#pdPlan'), $('#pdPlan2')].filter(Boolean).forEach(b => b.onclick = () => planEditor(p));
  const base = [['project_id', id], ...(rg.start ? [['start', rg.start]] : []), ...(rg.end ? [['end', rg.end]] : []), ...(S.ledger ? [['ledger_id', S.ledger]] : [])];
  $('#pdTx').innerHTML = txTable([], {tbodyId: 'pdBody'});
  infiniteList($('#pdTx'), {
    load: c => api('/api/transactions?' + new URLSearchParams([...base, ['limit', '50'], ...(c ? [['before', c]] : [])])),
    append: (r, first) => { if (first) $('#pdCnt').textContent = `${r.total} 笔`; $('#pdBody').insertAdjacentHTML('beforeend', txRowsHtml(r.items)); bindTxRows($('#pdTx'), () => route()); },
  });
}

async function planEditor(p) {
  const [cur, all] = await Promise.all([api(`/api/projects/${p.id}/plan`), api('/api/projects?include_archived=true&summary=false')]);
  const have = Object.fromEntries(cur.lines.map(l => [l.category_id ?? 'other', l]));
  const exp = (S.meta.categories || []).filter(c => c.kind === 'expense');
  const inp = (key, label, lvl) => `<tr><td style="padding-left:${lvl ? 22 : 6}px">${lvl ? '' : '<b>'}${esc(label)}${lvl ? '' : '</b>'}</td><td style="width:140px"><input type="number" step="0.01" min="0" data-pl="${key}" value="${have[key] ? have[key].amount : ''}" style="text-align:right"></td></tr>`;
  const bg = modal(`Budget plan 预算计划 · ${esc(p.name)} (${esc(p.currency || S.meta.settings.base_currency)})`, `
    <div class="row" style="gap:6px;margin-bottom:6px"><span class="small">Copy from 复制自</span><select id="plFrom"><option value="">—</option>${all.filter(x => x.id !== p.id).map(x => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select>
      <span class="small">×</span><input id="plScale" type="number" step="0.1" value="1" style="width:70px"><button class="btn sm" id="plCopy">Copy 复制</button></div>
    <div class="small muted">Plan a parent category, its sub-categories, or both (parents roll up their sub-categories). Leave blank for no plan.</div>
    <div style="max-height:55vh;overflow:auto"><table class="t"><tbody>${exp.map(c => inp(c.id, c.label || c.name, 0) + (c.children || []).map(ch => inp(ch.id, ch.name + ' ' + (ch.name_zh || ''), 1)).join('')).join('')}
      ${inp('other', 'Other / uncategorized 其他/未分类', 0)}</tbody></table></div>
    <div class="right"><b>Total 合计: <span id="plTot"></span></b></div>`,
    {wide: true, buttons: '<button class="btn" data-x2>Cancel</button><button class="btn pri" data-ok>Save plan 保存</button>'});
  const tot = () => { $('#plTot', bg).textContent = money($$('[data-pl]', bg).reduce((a, i) => a + (+i.value || 0), 0), p.currency || S.meta.settings.base_currency); };
  $$('[data-pl]', bg).forEach(i => i.oninput = tot); tot();
  $('[data-x2]', bg).onclick = bg.close;
  $('#plCopy', bg).onclick = async () => { const f = $('#plFrom', bg).value; if (!f) return toast('Choose a project', true);
    await guard(() => api(`/api/projects/${p.id}/plan/copy`, {method: 'POST', body: {from_project_id: +f, scale: +$('#plScale', bg).value || 1}})); bg.close(); toast('Plan copied'); route(); };
  $('[data-ok]', bg).onclick = async () => {
    const lines = $$('[data-pl]', bg).filter(i => i.value !== '').map(i => ({category_id: i.dataset.pl === 'other' ? null : +i.dataset.pl, amount: +i.value}));
    await guard(() => api(`/api/projects/${p.id}/plan`, {method: 'PUT', body: {lines}})); bg.close(); toast('Plan saved'); route();
  };
}
