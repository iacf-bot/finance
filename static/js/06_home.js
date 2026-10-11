/* ---------- Home 首页: month cards + quick add + this month's transactions (infinite scroll into older months) ---------- */
S.homeMonth = null;
const monthLabel = m => { const [y, mo] = m.split('-').map(Number); return new Date(y, mo - 1, 1).toLocaleString(undefined, {month: 'short', year: 'numeric'}); };
const monthLong = m => { const [y, mo] = m.split('-').map(Number); return new Date(y, mo - 1, 1).toLocaleString(undefined, {month: 'long', year: 'numeric'}); };
function homeRowHtml(t) {
  const title = t.kind === 'transfer' ? `${esc(t.account_name || '?')} → ${esc(t.to_account_name || '?')}`
    : t.kind === 'adjustment' ? (t.adj_reason === 'opening' ? 'Opening balance 期初余额' : `Balance adj. 余额调整 · ${esc(t.adj_reason_label || '')}`)
    : ['buy', 'sell', 'dividend', 'invest_fee'].includes(t.kind) ? `${kindLabel[t.kind]} ${esc(t.ticker || '')}`
    : t.category_name ? `${esc(t.category_name)}${t.category_name_zh ? ` <span class="muted">${esc(t.category_name_zh)}</span>` : ''}` : 'Uncategorized 未分类';
  const what = [t.merchant_name, t.note].filter(Boolean).map(x => esc(String(x).slice(0, 60))).join(' · ');
  const where = t.kind === 'transfer' ? '' : esc(t.account_name || '');
  return `<div class="hrow" data-tx="${t.id}" role="button" tabindex="0">
    <div class="hico" aria-hidden="true">${catIcon(t)}</div>
    <div class="hmain"><div class="htop">${title}${t.status === 'review' ? ' <span class="pill review">review</span>' : ''}${t.reconciled_id ? ' <span class="rec-badge" title="Reconciled 已对账">✔</span>' : ''}</div>
      <div class="hsub">${what ? `<span class="hwhat">${what}</span>` : ''}<span class="muted">${esc(t.date.slice(5, 10))} ${esc(t.date.slice(11, 16))}${where ? ' · ' + where : ''}</span></div></div>
    <div class="hamt">${txAmount(t)}</div></div>`;
}
function bindHomeRows(root, after) {
  $$('.hrow[data-tx]', root).forEach(r => { if (r._b) return; r._b = 1; r.onclick = () => openTx(+r.dataset.tx, after); r.onkeydown = e => { if (e.key === 'Enter') openTx(+r.dataset.tx, after); }; });
}
VIEWS.home = async (v) => {
  const month = S.homeMonth || thisMonth();
  const isNow = month === thisMonth();
  const lq = S.ledger ? '&ledger_id=' + S.ledger : '';
  v.innerHTML = `${demoBanner()}<div class="home">
    <div class="month-sw"><button class="btn sm" id="hmPrev" aria-label="Previous month">‹</button><b>${esc(monthLabel(month))}</b><button class="btn sm" id="hmNext" aria-label="Next month">›</button>${isNow ? '' : '<button class="btn sm" id="hmNow">This month 本月</button>'}</div>
    <div class="home-cards" id="hmCards"><div class="card stat"><div class="lbl">Income 收入</div><div class="val">…</div></div><div class="card stat"><div class="lbl">Expense 支出</div><div class="val">…</div></div><div class="card stat"><div class="lbl">Net 结余</div><div class="val">…</div></div><div class="card stat"><div class="lbl">Investment income 投资收益</div><div class="val">…</div></div></div>
    <div class="card home-add">
      <form class="qa" id="hqForm" autocomplete="off"><input id="hqInput" enterkeyhint="send" placeholder="记一笔 Quick add: lunch 25.5 chase · 午饭 35 支付宝"><button type="button" class="btn" id="hqFormBtn">✎<span class="hide-s"> Review</span></button><button class="btn pri" id="hqAdd">Add</button></form>
      <button type="button" class="btn" id="hqDetail">+ Detailed 详细记账</button></div>
    <div class="card home-list"><div id="hmList" class="hlist"></div></div></div>`;
  const go = m => { S.homeMonth = m === thisMonth() ? null : m; route(); };
  api(`/api/summary/month?month=${month}${lq}`).then(s => {
    $('#hmPrev').onclick = () => go(s.prev); $('#hmNext').onclick = () => go(s.next);
    const card = (lbl, val, cls, extra = '') => `<div class="card stat"><div class="lbl">${lbl}</div><div class="val ${cls}">${money(val, s.currency)}</div>${extra}</div>`;
    $('#hmCards').innerHTML = card('Income 收入', s.income, 'pos') + card('Expense 支出', s.expense, 'neg')
      + card('Net 结余', s.net, s.net >= 0 ? 'pos' : 'neg', `<div class="small muted">${s.count} transactions 笔</div>`)
      + card('Investment income 投资收益', s.investment_income, s.investment_income >= 0 ? 'pos' : 'neg', '<div class="small muted">not part of income/expense</div>');
  }).catch(e => toast(e.message, true));
  const ym = month.split('-').map(Number);
  $('#hmPrev').onclick = () => go(ym[1] > 1 ? `${ym[0]}-${String(ym[1] - 1).padStart(2, '0')}` : `${ym[0] - 1}-12`);
  $('#hmNext').onclick = () => go(ym[1] < 12 ? `${ym[0]}-${String(ym[1] + 1).padStart(2, '0')}` : `${ym[0] + 1}-01`);
  if ($('#hmNow')) $('#hmNow').onclick = () => go(thisMonth());
  const after = () => route();  // cards + list refresh after any save
  wireQuickAdd($('#hqForm'), $('#hqInput'), $('#hqFormBtn'), async () => { const r = await doQuickAdd($('#hqInput').value); if (r) { $('#hqInput').value = ''; after(); } });
  $('#hqDetail').onclick = () => openTx(null, after, isNow ? {} : {date: `${month}-01 12:00`});
  const lastDay = new Date(ym[0], ym[1], 0).getDate();
  const end = `${month}-${String(lastDay).padStart(2, '0')}`;
  let curMonth = null;
  const list = $('#hmList');
  infiniteList(list, {
    load: cursor => api(`/api/transactions?end=${end}&limit=50${lq}${cursor ? '&before=' + encodeURIComponent(cursor) : ''}`),
    append: (r, first) => {
      if (first && !r.items.length) list.innerHTML = `<div class="empty">No transactions in ${esc(monthLabel(month))} or earlier. Use the box above to add one.</div>`;
      let html = '';
      for (const t of r.items) {
        const m = t.date.slice(0, 7);
        if (m !== curMonth) { curMonth = m; html += `<div class="hmonth">${esc(monthLong(m))}${m === month ? '' : ' <span class="muted small">· earlier 更早</span>'}</div>`; }
        html += homeRowHtml(t);
      }
      list.insertAdjacentHTML('beforeend', html);
      bindHomeRows(list, after);
    },
    endHtml: 'That\'s everything 已经到底了',
  });
};

/* ---------- Account detail / history 账户明细 (#/account/<id>) ---------- */
S.acctHist = {};
const PERIODS = [['1M', '1M'], ['3M', '3M'], ['6M', '6M'], ['1Y', '1Y'], ['YTD', 'YTD'], ['ALL', 'All 全部'], ['CUSTOM', 'Custom 自定义']];
VIEWS.account = async (v, rest) => {
  const id = parseInt(rest[0]);
  if (!id) { location.hash = '#/accounts'; return; }
  const st = S.acctHist[id] = S.acctHist[id] || {period: '1M'};
  const meta = acctById(id);
  if (meta) $('#title').textContent = meta.name;
  v.innerHTML = `<div class="acct-head card" id="ahHead"><div class="empty">Loading…</div></div>
   <div class="card" style="margin-top:12px"><form id="ahFlt" class="ah-flt" autocomplete="off">
     <div class="chips" role="group" aria-label="Period">${PERIODS.map(([k, l]) => `<button type="button" class="chip${st.period === k ? ' on' : ''}" data-p="${k}">${l}</button>`).join('')}</div>
     <span class="ah-custom"${st.period === 'CUSTOM' ? '' : ' hidden'}><input type="date" name="start" value="${esc(st.start || '')}" aria-label="From"> – <input type="date" name="end" value="${esc(st.end || '')}" aria-label="To"></span>
     <input type="search" name="q" placeholder="Search 搜索" value="${esc(st.q || '')}">
     <select name="category_id">${catOptions('', st.category_id || '').replace('— Category 分类 —', 'All categories 全部分类')}</select>
   </form><div id="ahSum" class="ah-sum small"></div><div id="ahList"></div></div>`;
  const f = $('#ahFlt');
  const apply = () => { st.q = f.q.value.trim(); st.category_id = f.category_id.value; st.start = f.start.value; st.end = f.end.value; route(); };
  $$('[data-p]', f).forEach(b => b.onclick = () => { st.period = b.dataset.p; if (st.period !== 'CUSTOM') { route(); return; }
    $$('[data-p]', f).forEach(x => x.classList.toggle('on', x === b)); $('.ah-custom', f).hidden = false; f.start.focus(); });
  f.start.onchange = f.end.onchange = () => { if (f.start.value || f.end.value) apply(); };
  f.category_id.onchange = apply;
  let tmr; f.q.oninput = () => { clearTimeout(tmr); tmr = setTimeout(apply, 450); };
  f.onsubmit = e => { e.preventDefault(); apply(); };
  if (st.q) setTimeout(() => { f.q.focus(); f.q.setSelectionRange(f.q.value.length, f.q.value.length); }, 0);
  const params = () => { const p = new URLSearchParams({period: st.period, limit: '50'}); if (st.period === 'CUSTOM') { if (st.start) p.set('start', st.start); if (st.end) p.set('end', st.end); }
    if (st.q) p.set('q', st.q); if (st.category_id) p.set('category_id', st.category_id); return p; };
  let A = null;
  const after = () => route();
  const head = (h) => {
    const a = h.account; A = a; $('#title').textContent = a.name;
    const owed = a.is_liability, b = h.balances;
    $('#ahHead').innerHTML = `<div class="ah-top"><a href="#/accounts" class="small">← Accounts 账户</a></div>
      <div class="ah-main"><div><h2>${isDemoName(a.name) ? '<span class="pill demo">DEMO</span> ' : ''}${esc(a.name)} ${a.last4 ? `<span class="muted small">••${esc(a.last4)}</span>` : ''}</h2>
        <div class="small muted">${esc(a.type_label)} · ${a.currency}${a.institution ? ' · ' + esc(a.institution) : ''}${a.reconciled_at ? ` · <a href="javascript:void 0" class="rec-link" id="ahRec">✔ Reconciled 已对账 ${esc(a.reconciled_at.slice(0, 10))}</a>` : ''}</div></div>
        <div class="ah-bal"><div class="lbl small muted">${owed ? 'Owed 欠款' : 'Balance 余额'}</div><div class="val ${owed ? (a.owed > 0 ? 'neg' : '') : a.balance < 0 ? 'neg' : ''}">${money(owed ? a.owed : a.balance, a.currency)}</div>
        ${a.is_investment ? `<div class="small muted">Cash ${money(a.cash_balance, a.currency)} · Holdings ${money(a.holdings_value, a.currency)}</div>` : ''}</div></div>
      <div class="row ah-acts"><button class="btn pri" id="ahAdd">+ Add 记一笔</button><button class="btn" id="ahSet">⚖️ Set balance 调整余额</button><button class="btn" id="ahReconcile">✔ Reconcile 对账</button><button class="btn" id="ahEdit">Edit 编辑</button>
        <a class="btn" href="#/transactions/?account_id=${a.id}">🧾 <span class="hide-s">In Transactions</span></a></div>`;
    $('#ahAdd').onclick = () => openTx(null, after, {account_id: a.id, currency: a.currency});
    $('#ahSet').onclick = () => adjustForm(a);
    $('#ahReconcile').onclick = () => reconcileDialog(a);
    $('#ahEdit').onclick = () => acctForm(a);
    if ($('#ahRec')) $('#ahRec').onclick = () => reconcileHistory(a);
    const lbl = h.start ? `${h.start}${h.end ? ' – ' + h.end : ' – today'}` : (h.end ? `… – ${h.end}` : 'All time 全部');
    $('#ahSum').innerHTML = `<span>${esc(lbl)}</span><span>${owed ? 'Owed' : 'Balance'} at start 期初 <b>${money(b.opening, a.currency)}</b> → end 期末 <b>${money(b.closing, a.currency)}</b></span>
      <span>In 流入 <b class="pos">+${num(b.inflow)}</b></span><span>Out 流出 <b class="neg">−${num(b.outflow)}</b></span><span>${h.total} transactions 笔${st.q || st.category_id ? ' (filtered)' : ''}</span>
      ${h.cash_only ? '<span class="muted">Running balance = cash part (holdings excluded)</span>' : ''}`;
  };
  const list = $('#ahList');
  const balHdr = () => A && A.is_liability ? 'Owed 欠款' : A && A.is_investment ? 'Cash 现金' : 'Balance 余额';
  const rowHtml = t => `<tr data-tx="${t.id}" style="cursor:pointer"><td class="nowrap small">${esc(t.date.slice(0, 10))}<div class="muted hide-m">${esc(t.date.slice(11, 16))}</div></td>
    <td><div class="ah-det"><span class="hico-s" aria-hidden="true">${catIcon(t)}</span><div>${t.reconciled_id ? '<span class="rec-badge" title="Reconciled 已对账">✔🔒</span> ' : ''}${txDesc(t)}</div></div></td>
    <td class="right nowrap ${t.account_effect < 0 ? 'neg' : t.account_effect > 0 ? 'pos' : ''}">${t.account_effect > 0 ? '+' : t.account_effect < 0 ? '−' : ''}${money(Math.abs(t.account_effect), A ? A.currency : undefined)}</td>
    <td class="right nowrap rb">${t.running_shown === null || t.running_shown === undefined ? '—' : money(t.running_shown, A ? A.currency : undefined)}</td></tr>`;
  const NEXT = {'1M': '3M', '3M': '6M', '6M': '1Y', '1Y': 'ALL', 'YTD': 'ALL', 'CUSTOM': 'ALL'};
  const inf = infiniteList(list, {
    load: cursor => { const p = params(); if (cursor) p.set('before', cursor); return api(`/api/accounts/${id}/history?` + p); },
    append: (h, first) => {
      if (first) {
        head(h);
        list.innerHTML = h.items.length ? `<div class="scroll-x"><table class="t ah-table"><thead><tr><th>Date</th><th>Details</th><th class="right">Amount</th><th class="right">${balHdr()}</th></tr></thead><tbody id="ahBody"></tbody></table></div>`
          : `<div class="empty">No transactions in this period${st.q || st.category_id ? ' matching the filter' : ''}.</div>`;
      }
      if (h.items.length) { $('#ahBody').insertAdjacentHTML('beforeend', h.items.map(rowHtml).join('')); bindTxRows(list, after); }
    },
    endHtml: st.period === 'ALL' ? 'Start of the account 已到最早' : `Start of the selected period. <button type="button" class="btn sm" data-older>Show older 更早 (${NEXT[st.period] === 'ALL' ? 'All' : NEXT[st.period]})</button>`,
  });
  inf.foot.addEventListener('click', e => { if (e.target.closest('[data-older]')) { st.period = NEXT[st.period] || 'ALL'; route(); } });
};
