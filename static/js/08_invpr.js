/* ---------- Investments → Principal & returns 本金与收益 (per-account table + combined trend) ---------- */
S.invPR = S.invPR || {period: 'ALL', start: '', end: ''};
function prRange(st) {
  const t = new Date(), iso = d => d.toISOString().slice(0, 10), back = (m, y = 0) => { const d = new Date(t); d.setMonth(d.getMonth() - m); d.setFullYear(d.getFullYear() - y); return iso(d); };
  return ({'1M': [back(1), iso(t)], '3M': [back(3), iso(t)], '6M': [back(6), iso(t)], 'YTD': [t.getFullYear() + '-01-01', iso(t)],
    '1Y': [back(0, 1), iso(t)], '3Y': [back(0, 3), iso(t)], 'ALL': ['', ''], 'CUSTOM': [st.start, st.end]})[st.period] || ['', ''];
}
const prPct = p => p === null || p === undefined ? '<span class="muted">—</span>' : `<span class="${p >= 0 ? 'pos' : 'neg'}">${p >= 0 ? '+' : ''}${p.toFixed(1)}%</span>`;
const prAmt = (v, c) => `<span class="${v < 0 ? 'neg' : v > 0 ? 'pos' : ''}">${v > 0 ? '+' : v < 0 ? '−' : ''}${money(Math.abs(v), c)}</span>`;
const prUnexp = (v, c) => Math.abs(v || 0) >= 0.01 ? `<span class="unexp-txt">${money(v, c)}</span>` : '<span class="muted">—</span>';
async function renderPR() {
  const el = $('#prCard'); if (!el) return;
  const st = S.invPR, [from, to] = prRange(st);
  const qs = new URLSearchParams(); if (from) qs.set('from', from); if (to) qs.set('to', to);
  let d;
  try { d = await api('/api/investments/principal-returns?' + qs); } catch (e) { el.innerHTML = `<h3>Principal &amp; returns 本金与收益</h3><div class="neg">${esc(e.message)}</div>`; return; }
  const B = d.currency, T = d.total;
  const accRow = a => `<tr class="pr-acc" data-acc="${a.id}" title="Open account detail 查看账户">
      <td><span class="ind"></span>${isDemoName(a.name) ? '<span class="pill demo">DEMO</span> ' : ''}${esc(a.name)}${a.archived ? ' <span class="small muted">(archived)</span>' : ''} <span class="small muted">${a.currency}</span></td>
      <td class="right">${money(a.value, a.currency)}</td><td class="right">${money(a.principal, a.currency)}</td>
      <td class="right">${prAmt(a.returns, a.currency)}</td><td class="right">${prPct(a.returns_pct)}</td>
      <td class="right" ${a.correction_count ? `title="${a.correction_count} unclassified balance adjustment(s); reclassify them on the account page"` : ''}>${prUnexp(a.unexplained, a.currency)}</td>
      <td class="right">${prAmt(a.period.returns, a.currency)}<div class="small muted">${prPct(a.period.md_return)}</div></td></tr>`;
  const sumRow = (g, cls, label) => `<tr class="${cls}"><td><b>${label}</b></td><td class="right"><b>${money(g.value, g.currency)}</b></td><td class="right"><b>${money(g.principal, g.currency)}</b></td>
      <td class="right"><b>${prAmt(g.returns, g.currency)}</b></td><td class="right">${prPct(g.returns_pct)}</td><td class="right">${prUnexp(g.unexplained, g.currency)}</td>
      <td class="right">${g.period ? prAmt(g.period.returns, g.currency) + `<div class="small muted">${prPct(g.period.md_return)}</div>` : '<span class="muted">—</span>'}</td></tr>`;
  const body = d.groups.map(g => d.accounts.filter(a => a.group_id === g.id).map(accRow).join('') + sumRow(g, 'ret-group', `${esc(g.name)} <span class="small muted">subtotal 小计${d.by_currency.length > 1 ? ' · ' + B : ''}</span>`)).join('')
    + (d.by_currency.length > 1 ? d.by_currency.map(c => sumRow(c, 'ret-cur', `Total ${c.currency} 合计`)).join('') : '')
    + sumRow(T, 'ret-total', `All investments 全部投资${d.by_currency.length > 1 ? ' ≈ ' + B : ''}`);
  el.innerHTML = `<h3>Principal &amp; returns 本金与收益<span class="sp"></span>
      <span class="chips" role="group" aria-label="Period">${PERIODS.map(([k, l]) => `<button type="button" class="chip${st.period === k ? ' on' : ''}" data-pp="${k}">${l}</button>`).join('')}</span>
      <span id="prCustom"${st.period === 'CUSTOM' ? '' : ' hidden'}><input type="date" id="prS" value="${esc(st.start)}"> – <input type="date" id="prE" value="${esc(st.end)}"></span></h3>
    <div class="small muted" style="margin:-4px 0 10px">${esc(d.from)} → ${esc(d.to)} · figures as of ${esc(d.to)} · value = principal + returns + unexplained · return % = returns ÷ principal · same logic as the account pages</div>
    ${!d.accounts.length ? '<div class="empty">No investment-type accounts yet (Brokerage / Retirement / Crypto).</div>' : `
    <div class="grid g4 inv-cards">
      <div class="stat"><div class="lbl">Value 市值</div><div class="val">${money(T.value, B)}</div></div>
      <div class="stat"><div class="lbl">Principal 本金投入</div><div class="val">${money(T.principal, B)}</div><div class="small muted">Period 期间 ${prAmt(T.principal_change, B)}</div></div>
      <div class="stat"><div class="lbl">Returns 投资收益</div><div class="val">${prAmt(T.returns, B)} <span class="small">${prPct(T.returns_pct)}</span></div><div class="small muted">Period 期间 ${prAmt(T.period.returns, B)} · ${prPct(T.period.md_return)}</div></div>
      <div class="stat"><div class="lbl" title="Unclassified balance adjustments + reconciliation gaps">Unexplained 未分类 ⓘ</div><div class="val">${prUnexp(T.unexplained, B)}</div></div></div>
    <div class="scroll-x" style="margin-top:12px"><table class="t ret-table pr-table"><thead><tr><th>Account 账户</th><th class="right">Value 市值</th><th class="right">Principal 本金投入</th><th class="right">Returns 投资收益</th><th class="right">%</th><th class="right">Unexplained 未分类</th><th class="right" title="Returns earned in the selected range; money-weighted %">Period returns 期间收益</th></tr></thead>
    <tbody>${body}</tbody></table></div>
    <div class="chart-box" style="margin-top:14px;height:260px"><canvas id="prChart"></canvas></div>
    <div class="small muted">Combined in ${esc(B)} (${esc(d.interval)} points). Rows open the account detail page.</div>`}`;
  $$('[data-pp]', el).forEach(b => b.onclick = () => { st.period = b.dataset.pp; if (st.period !== 'CUSTOM') renderPR(); else { $$('[data-pp]', el).forEach(x => x.classList.toggle('on', x === b)); $('#prCustom').hidden = false; } });
  const ce = () => { st.start = $('#prS').value; st.end = $('#prE').value; if (st.start || st.end) renderPR(); };
  if ($('#prS')) { $('#prS').onchange = ce; $('#prE').onchange = ce; }
  $$('[data-acc]', el).forEach(tr => tr.onclick = () => { location.hash = '#/account/' + tr.dataset.acc; });
  const P = d.series || [];
  if (P.length && $('#prChart')) chart('prChart', {type: 'line', data: {labels: P.map(q => q.date), datasets: [
    {label: 'Principal 本金', data: P.map(q => q.principal), borderColor: '#4a7bd0', backgroundColor: '#4a7bd0', pointRadius: 0, tension: .2},
    {label: 'Returns 收益', data: P.map(q => q.returns), borderColor: '#14a06f', backgroundColor: '#14a06f', pointRadius: 0, tension: .2},
    {label: 'Value 市值', data: P.map(q => q.value), borderColor: '#8a5cd6', backgroundColor: '#8a5cd6', borderDash: [5, 4], pointRadius: 0, tension: .2},
    {label: 'Unexplained 未分类', data: P.map(q => q.unexplained), borderColor: '#c9a227', backgroundColor: '#c9a227', pointRadius: 0, tension: .2, hidden: P.every(q => Math.abs(q.unexplained) < 0.01)}]},
    options: {interaction: {mode: 'index', intersect: false}, scales: {x: {ticks: {maxTicksLimit: 8}}},
      plugins: {tooltip: {callbacks: {label: c => `${c.dataset.label}: ${money(c.parsed.y, B)}`}}}}});
}
