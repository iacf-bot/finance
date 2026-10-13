/* ---------- import: bank CSV + Suishouji ---------- */
SET.import = async (el) => {
  el.innerHTML = `<div class="grid g2">
   <div class="card"><h3>📒 From Suishouji</h3><p class="small muted">Upload any of: sui.com web export (新功能 → 导入导出 → 数据导出 → 随手记 web版, <code>.xls</code>); the app/神象云账本 export (账本设置 → 数据导出, <code>.xlsx</code>); or the app CSV (设置 → 高级功能 → 备份与同步 → 备份与恢复 → 本地备份与恢复 → 导出Excel文件(CSV)). Sheets/columns are detected by header name; you'll map accounts and categories before anything is written. A backup is taken automatically first.</p>
    <div class="row"><input type="file" id="suiFile" accept=".xls,.xlsx,.csv"><button class="btn pri" id="suiGo">Preview</button></div></div>
   <div class="card"><h3>🏦 Bank / card CSV</h3><p class="small muted">Chase, Amex, Bank of America, Capital One, Citi, Discover, Alipay and WeChat CSV exports are recognised; anything else can be mapped by column.</p>
    <div class="row"><input type="file" id="csvFile" accept=".csv,.txt"><button class="btn pri" id="csvGo">Preview</button></div></div></div><div id="impOut" style="margin-top:16px"></div>`;
  $('#csvGo').onclick = async () => { const f = $('#csvFile').files[0]; if (!f) return toast('Choose a CSV file', true); const fd = new FormData(); fd.append('file', f); csvMapping(await guard(() => api('/api/import/csv/preview', {method: 'POST', body: fd}))); };
  $('#suiGo').onclick = async () => { const f = $('#suiFile').files[0]; if (!f) return toast('Choose a file', true); const fd = new FormData(); fd.append('file', f); suiMapping(await guard(() => api('/api/import/sui/preview', {method: 'POST', body: fd}))); };
};
function csvMapping(p) {
  const out = $('#impOut');
  const fields = [['date', 'Date *'], ['amount', 'Amount (signed)'], ['debit', 'Debit / withdrawal'], ['credit', 'Credit / deposit'], ['description', 'Description / payee'], ['category', 'Bank category'], ['note', 'Memo / note'], ['direction', 'Direction (收/支)']];
  const sel = (f) => `<select data-map="${f}"><option value="">—</option>${p.header.map(h => `<option ${p.mapping[f] === h ? 'selected' : ''}>${esc(h)}</option>`).join('')}</select>`;
  out.innerHTML = `<div class="card"><h3>CSV preview — detected format: <span class="pill transfer">${esc(p.format)}</span> · ${p.row_count} rows</h3>
   <div class="form"><label class="f"><span>Import into account *</span><select id="cAcct">${acctOptions('', {blank: '— choose —'})}</select></label>
   <label class="f"><span>Amount sign</span><select id="cSign">${[['negative_is_expense', 'Negative = spending (Chase, BofA, Capital One…)'], ['positive_is_expense', 'Positive = spending (Amex, Discover)'], ['direction_column', 'Use the 收/支 direction column (Alipay/WeChat)']].map(([k, l]) => `<option value="${k}" ${p.sign === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
   ${fields.map(([f, l]) => `<label class="f"><span>${l}</span>${sel(f)}</label>`).join('')}
   <label class="f"><span>Date format (blank = auto, e.g. %m/%d/%Y)</span><input id="cDf"></label>
   <label class="chk"><input type="checkbox" id="cRev"> Put everything in To-review</label><label class="chk"><input type="checkbox" id="cDup" checked> Skip duplicates (re-import safe)</label></div>
   <div class="scroll-x" style="margin-top:12px"><table class="t small"><thead><tr>${p.header.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${p.rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
   <div class="row end" style="margin-top:12px"><button class="btn pri" id="cGo">Import ${p.row_count} rows</button></div></div>`;
  $('#cGo').onclick = async () => {
    const mapping = {}; $$('[data-map]', out).forEach(s => mapping[s.dataset.map] = s.value || null);
    if (!$('#cAcct').value) return toast('Choose the account', true);
    const r = await guard(() => api('/api/import/csv/commit', {method: 'POST', body: {token: p.token, account_id: +$('#cAcct').value, mapping, sign: $('#cSign').value, date_format: $('#cDf').value, as_review: $('#cRev').checked, skip_duplicates: $('#cDup').checked, ledger_id: S.ledger || 1}}));
    out.innerHTML = `<div class="card"><h3>✅ CSV import finished</h3><div class="kv"><div>Imported</div><div><b>${r.imported}</b> ${esc(JSON.stringify(r.by_kind))}</div><div>Sent to review</div><div>${r.review}</div><div>Duplicates skipped</div><div>${r.duplicates}</div><div>Rows skipped</div><div>${r.skipped.length}</div></div>
     ${r.skipped.length ? `<pre>${esc(r.skipped.map(s => `row ${s.row}: ${s.reason}`).join('\n'))}</pre>` : ''}<div class="row"><a class="btn" href="#/transactions">View transactions</a></div></div>`;
    loadMeta();
  };
}
function suiMapping(p) {
  const out = $('#impOut');
  const acctTypes = p.account_types;
  const existingSubs = kind => (S.meta.categories || []).filter(c => c.kind === kind).flatMap(r => r.children.map(c => [c.id, `${r.name} › ${c.name} ${c.name_zh || ''}`]));
  const badSheets = p.sheets.filter(s => s.missing && s.missing.length);
  out.innerHTML = `<div class="card"><h3>Suishouji preview 随手记导入预览 · ${p.row_count} rows</h3>
   <div class="kv small"><div>Types found</div><div>${Object.entries(p.type_counts).map(([k, n]) => `<span class="pill">${esc(k)} ${n}</span>`).join(' ')}</div>
   ${p.unknown_types.length ? `<div>Unknown types</div><div class="neg">${p.unknown_types.map(esc).join(', ')} (will be skipped)</div>` : ''}
   ${(p.missing_fx || []).length ? `<div>Exchange rates</div><div class="neg">No rate set for ${p.missing_fx.map(esc).join(', ')}: add it in Settings → Currencies so net worth converts correctly.</div>` : ''}
   <div>Already imported (duplicates)</div><div><b>${p.duplicates}</b></div>
   <div>Members / merchants / projects</div><div>${p.members.length} / ${p.merchants.length} / ${p.projects.length} (created automatically)</div></div>
   <h3 style="margin-top:16px">Sheets</h3><table class="t small"><thead><tr><th>Sheet</th><th>Rows</th><th>Detected columns</th><th>Unrecognised</th><th>Status</th></tr></thead><tbody>
    ${p.sheets.map(s => `<tr><td><b>${esc(s.name)}</b></td><td>${s.rows}</td><td>${esc(Object.entries(s.columns || {}).map(([k, v]) => `${k}=${v}`).join(', '))}</td><td>${esc((s.unrecognized || []).join(', '))}</td><td>${s.skipped ? `<span class="neg">${esc(s.reason)}</span>` : '✓'}</td></tr>`).join('')}</tbody></table>
   ${badSheets.length ? `<div class="demo-banner" style="margin-top:10px;background:#fff8e6;border-color:#e4a11b;color:#7a5200;display:block"><b>Map missing columns:</b>${badSheets.map(s => `<div class="row" style="margin-top:6px"><b>${esc(s.name)}</b>${s.missing.map(m => `<label class="small">${m} → <select data-ov="${esc(s.name)}" data-field="${m}"><option value="">—</option>${s.header.filter(Boolean).map(h => `<option>${esc(h)}</option>`).join('')}</select></label>`).join(' ')}</div>`).join('')}<button class="btn sm" id="reprev" style="margin-top:6px">Re-run preview</button></div>` : ''}
   <h3 style="margin-top:16px">Accounts (${p.accounts.length})</h3><div class="scroll-x"><table class="t small"><thead><tr><th>随手记 account</th><th>Rows</th><th>Map to</th><th>Type if new</th><th>Currency</th><th title="Sum of this file's rows for the account">Net from file</th><th title="Optional. The balance 随手记 shows today (for credit cards/loans: the amount owed). The opening balance is set so the imported balance matches.">Balance in 随手记 today</th></tr></thead><tbody>
    ${p.accounts.map((a, i) => `<tr><td><b>${esc(a.name)}</b></td><td>${a.rows}</td><td><select data-acc="${i}"><option value="">➕ Create new</option>${acctOptions(a.existing_account_id, {blank: null})}</select></td>
     <td><select data-atype="${i}">${Object.entries(acctTypes).map(([k, l]) => `<option value="${k}" ${k === a.guessed_type ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></td><td><select data-acur="${i}">${curOptions(a.currency)}</select></td>
     <td class="${a.flags && a.flags.length ? 'neg' : ''}" title="${esc((a.flags || []).join('; '))}">${num(a.net_from_file)}${a.flags && a.flags.length ? ' ⚠️' : ''}<div class="small muted">${esc(a.first_date || '')} → ${esc(a.last_date || '')}</div></td>
     <td><input data-abal="${i}" inputmode="decimal" placeholder="optional" style="width:110px"></td></tr>`).join('')}</tbody></table></div>
   ${p.accounts.some(a => a.flags && a.flags.length) ? `<p class="small neg">⚠️ ${p.accounts.filter(a => a.flags && a.flags.length).length} account(s) look off from the file alone (hover the ⚠️). 随手记's exports don't include accounts' initial balances; type the balance 随手记 shows today to fix them.</p>` : ''}
   <h3 style="margin-top:16px">Categories (${p.categories.length})</h3><div class="scroll-x" style="max-height:420px;overflow:auto"><table class="t small"><thead><tr><th>Kind</th><th>分类 › 子分类</th><th>Rows</th><th>Map to</th></tr></thead><tbody>
    ${p.categories.map((c, i) => `<tr><td>${c.kind}</td><td>${esc(c.category)} › ${esc(c.subcategory)}</td><td>${c.rows}</td><td><select data-cat="${i}"><option value="">➕ Create "${esc(c.category)} › ${esc(c.subcategory || c.category)}"</option>${existingSubs(c.kind).map(([id, l]) => `<option value="${id}" ${id == c.existing_category_id ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></td></tr>`).join('')}</tbody></table></div>
   <div class="row" style="margin-top:14px"><label class="chk"><input type="checkbox" id="sDup" checked> Skip duplicates (${p.duplicates} already imported)</label><span class="sp"></span><button class="btn pri" id="sGo">Import ${p.row_count} rows</button></div>
   <details class="small muted" style="margin-top:8px"><summary>Sample rows</summary><pre>${esc(JSON.stringify(p.sample, null, 1))}</pre></details></div>`;
  const rp = $('#reprev'); if (rp) rp.onclick = async () => { const ov = {}; $$('[data-ov]', out).forEach(s => { if (s.value) (ov[s.dataset.ov] = ov[s.dataset.ov] || {})[s.dataset.field] = s.value; }); const fd = new FormData(); fd.append('token', p.token); fd.append('overrides', JSON.stringify(ov)); const np = await guard(() => api('/api/import/sui/preview', {method: 'POST', body: fd})); np.overrides = ov; suiMapping(np); };
  $('#sGo').onclick = async () => {
    const account_map = {}; p.accounts.forEach((a, i) => { const id = $(`[data-acc="${i}"]`, out).value; const bal = $(`[data-abal="${i}"]`, out).value.trim(); account_map[a.name] = id ? {account_id: +id} : {type: $(`[data-atype="${i}"]`, out).value, currency: $(`[data-acur="${i}"]`, out).value, balance: bal || null}; });
    const category_map = {}; p.categories.forEach((c, i) => { category_map[`${c.kind}|${c.category || ''}|${c.subcategory || ''}`] = $(`[data-cat="${i}"]`, out).value || null; });
    $('#sGo').disabled = true; $('#sGo').textContent = 'Importing…';
    const r = await guard(() => api('/api/import/sui/commit', {method: 'POST', body: {token: p.token, options: {account_map, category_map, skip_duplicates: $('#sDup').checked, ledger_id: S.ledger || 1, column_overrides: p.overrides || null}}})).finally(() => { const b = $('#sGo'); if (b) b.disabled = false; });
    out.innerHTML = `<div class="card"><h3>✅ Suishouji import finished</h3><div class="kv"><div>Imported</div><div><b>${r.imported}</b></div>
     <div>By type</div><div>${Object.entries(r.by_type).map(([k, n]) => `<span class="pill">${esc(k)} ${n} · ${num(r.totals[k])}</span>`).join(' ')}</div>
     <div>Duplicates skipped</div><div>${r.duplicates}</div><div>Rows skipped</div><div>${r.skipped_count}</div>
     <div>Accounts created</div><div>${r.created_accounts.map(a => esc(`${a.name} (${a.type}, ${a.currency})`)).join(', ') || '—'}</div><div>Categories created</div><div>${r.created_categories}</div>
     ${(r.missing_fx || []).length ? `<div>Exchange rates</div><div class="neg">Set a rate for ${r.missing_fx.map(esc).join(', ')} in Settings → Currencies</div>` : ''}</div>
     ${(r.accounts || []).length ? `<h3 style="margin-top:12px">Resulting balances</h3><div class="scroll-x"><table class="t small"><thead><tr><th>Account</th><th>Type</th><th>Opening</th><th>Balance</th></tr></thead><tbody>${r.accounts.map(a => `<tr><td>${esc(a.name)}${a.created ? '' : ' <span class="muted">(existing)</span>'}</td><td>${esc(a.type)}</td><td>${money(a.opening_balance, a.currency)}</td><td class="${a.balance < 0 ? 'neg' : ''}">${money(a.balance, a.currency)}</td></tr>`).join('')}</tbody></table></div>` : ''}
     ${r.skipped.length ? `<h3 style="margin-top:12px">Skipped rows</h3><pre>${esc(r.skipped.map(s => `${s.where}: ${s.reason}`).join('\n'))}</pre>` : ''}
     <p class="small muted">Tip: imported accounts start at 0 — if your 随手记 export doesn't include opening balances (余额变更), use ⚖️ Adjust balance on the Accounts page.</p>
     <div class="row"><a class="btn" href="#/accounts">Accounts</a><a class="btn" href="#/transactions">Transactions</a></div></div>`;
    await loadMeta();
  };
}

/* ---------- boot ---------- */
/* quick-add boxes: "Add" posts right away (unchanged), "✎ Review" opens the pre-filled form.
   Enter does whichever Settings → General chooses; Shift+Enter does the other one. */
function wireQuickAdd(form, input, formBtn, add) {
  const toForm = async () => { const v = input.value; const bg = await openQuickForm(v); if (bg) input.value = ''; };
  const labelAdd = () => { const f = txPrefs().quickadd_enter === 'form'; formBtn.title = `Open in the pre-filled form to review before saving (${f ? 'Enter' : 'Shift+Enter'})`; };
  labelAdd(); input.addEventListener('focus', labelAdd);
  formBtn.onclick = toForm;
  input.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    const wantForm = (txPrefs().quickadd_enter === 'form') !== e.shiftKey;
    wantForm ? toForm() : add();
  });
  form.onsubmit = e => { e.preventDefault(); add(); };
}
wireQuickAdd($('#qaForm'), $('#qaInput'), $('#qaFormBtn'), async () => { const r = await doQuickAdd($('#qaInput').value); if (r) { $('#qaInput').value = ''; route(); } });
$('#fab').onclick = () => openTx();
$('#logout').onclick = async e => { e.preventDefault(); await api('/api/auth/logout', {method: 'POST'}); location.href = '/login'; };
$('#ledgerSel').onchange = e => { S.ledger = e.target.value; localStorage.setItem('ledger', S.ledger); route(); };
window.addEventListener('hashchange', route);
i18nObserve();
loadMeta().then(route);
