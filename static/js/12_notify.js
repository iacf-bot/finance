/* ---------- off-site backup (Google Drive), SMTP + emailed reports/reminders. All OFF by default. ---------- */
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
function weekdayBoxes(name, csv) {
  const on = new Set(String(csv || '').split(',').filter(x => x !== '').map(Number));
  return `<span class="wd-boxes">${WEEKDAYS.map((d, i) => `<label class="chk small"><input type="checkbox" data-wd="${name}" value="${i}" ${on.has(i) ? 'checked' : ''}> ${d}</label>`).join(' ')}</span>`;
}
function readWeekdays(form, name) { return [...form.querySelectorAll(`[data-wd="${name}"]:checked`)].map(x => x.value).join(','); }
const onOff = v => v ? '<span class="pill income">Enabled</span>' : '<span class="pill">Disabled</span>';

async function offsiteCard(el) {
  const o = await api('/api/offsite');
  el.insertAdjacentHTML('beforeend', `<div class="card" style="margin-top:16px" id="offCard"><h3>☁️ Off-site backup to Google Drive ${onOff(o.enabled)}</h3>
    <p class="small muted">Incremental: each run adds one database snapshot and copies only new/changed receipt photos. Use a Google Drive for desktop folder on this computer, or an rclone remote.</p>
    <form class="form" id="offF">
      <label class="chk full"><input type="checkbox" name="offsite_enabled" ${o.enabled ? 'checked' : ''}> Enabled</label>
      <label class="f"><span>Method</span><select name="offsite_method"><option value="folder" ${o.offsite_method === 'folder' ? 'selected' : ''}>Synced folder (Google Drive for desktop)</option><option value="rclone" ${o.offsite_method === 'rclone' ? 'selected' : ''}>rclone remote</option></select></label>
      <label class="f"><span>Keep last N snapshots</span><input name="offsite_keep" type="number" min="1" value="${esc(o.offsite_keep)}"></label>
      <label class="f full" data-m="folder"><span>Drive folder</span><input name="offsite_folder" value="${esc(o.offsite_folder || '')}" placeholder="G:\\My Drive\\FinanceBackups  ·  ~/Google Drive/My Drive/FinanceBackups"></label>
      <label class="f full" data-m="rclone"><span>rclone target</span><input name="offsite_rclone_target" value="${esc(o.offsite_rclone_target || '')}" placeholder="gdrive:FinanceAppBackups"></label>
      <label class="f"><span>Schedule</span><select name="offsite_mode"><option value="fixed" ${o.offsite_mode !== 'interval' ? 'selected' : ''}>At fixed times</option><option value="interval" ${o.offsite_mode === 'interval' ? 'selected' : ''}>Every N hours</option></select></label>
      <label class="f" data-s="interval"><span>Every (hours)</span><input name="offsite_interval_hours" type="number" step="0.25" min="0.25" value="${esc(o.offsite_interval_hours)}"></label>
      <label class="f" data-s="fixed"><span>Times (HH:MM, comma separated)</span><input name="offsite_times" value="${esc(o.offsite_times || '')}" placeholder="02:30"></label>
      <div class="f full" data-s="fixed"><span class="small">Weekdays <span class="muted">(none = every day)</span></span> ${weekdayBoxes('off', o.offsite_weekdays)}</div>
      <label class="chk full"><input type="checkbox" name="offsite_receipts" ${o.offsite_receipts !== '0' ? 'checked' : ''}> Include receipt photos</label>
      <div class="full kv small"><div>Schedule</div><div>${esc(o.schedule)}</div><div>Last run</div><div>${esc(o.last_run || '—')} ${o.last_status ? `<b class="${o.last_status === 'ok' ? 'pos' : 'neg'}">${esc(o.last_status)}</b>` : ''}</div><div></div><div class="muted" data-notr>${esc(o.last_detail || '')}</div><div>Next run</div><div>${esc(o.next_run || '—')}</div></div>
      <div class="full row end"><button type="button" class="btn" id="offRun">Run now</button><button class="btn pri">Save</button></div></form>
    <details class="small" style="margin-top:8px"><summary>How to restore from Drive</summary><ol>
      <li>Download the newest <code>db/finance-….db</code> from the Drive folder (or <code>rclone copy gdrive:FinanceAppBackups/db/&lt;file&gt; .</code>).</li>
      <li>Settings → Backup → Restore: upload that file. The current database is backed up first.</li>
      <li>Copy the Drive <code>receipts/</code> folder back into your receipts folder (data/receipts, or the folder set in Settings → General).</li></ol></details></div>`);
  const f = $('#offF');
  const vis = () => { f.querySelectorAll('[data-m]').forEach(x => x.hidden = x.dataset.m !== f.offsite_method.value); f.querySelectorAll('[data-s]').forEach(x => x.hidden = x.dataset.s !== f.offsite_mode.value); };
  f.onchange = vis; vis();
  f.onsubmit = async e => {
    e.preventDefault();
    const b = Object.fromEntries(new FormData(f));
    b.offsite_enabled = f.offsite_enabled.checked; b.offsite_receipts = f.offsite_receipts.checked; b.offsite_weekdays = readWeekdays(f, 'off');
    await guard(() => api('/api/offsite', {method: 'PUT', body: b})); toast(i18nText('Saved')); route();
  };
  $('#offRun').onclick = async () => { const r = await guard(() => api('/api/offsite/run', {method: 'POST'})); toast((r.ok ? '✓ ' : '✗ ') + r.detail, !r.ok); route(); };
}

SET.notify = async (el) => {
  const d = await api('/api/email/settings'); const m = d.smtp; const J = d.jobs;
  const job = (k, title, extra) => `<div class="card"><h3>${title} ${onOff(J[k].enabled)}</h3><form class="form job-f" data-job="${k}">
      <label class="chk full"><input type="checkbox" name="email_${k}_enabled" ${J[k].enabled ? 'checked' : ''}> Send by email</label>
      <label class="f"><span>Times (HH:MM)</span><input name="email_${k}_times" value="${esc(d[`email_${k}_times`] || '')}"></label>
      ${extra}
      <div class="full kv small"><div>Schedule</div><div>${esc(J[k].schedule)}</div><div>Last sent</div><div>${esc(J[k].last_run || '—')} ${J[k].last_status ? `<b class="${J[k].last_status === 'ok' ? 'pos' : 'muted'}" data-notr>${esc(J[k].last_status)}</b>` : ''}</div><div>Next</div><div>${esc(J[k].next_run || '—')}</div></div>
      <div class="full row end">${k === 'reminders' ? '' : `<a class="btn sm" target="_blank" href="/api/email/preview?kind=${k}&lang=${encodeURIComponent(d.email_lang)}">👁 Preview</a>`}<button type="button" class="btn sm" data-send="${k}">Send now</button><button class="btn pri sm">Save</button></div></form></div>`;
  el.innerHTML = `<div class="grid g2"><div class="card"><h3>✉️ SMTP (outgoing mail) ${m.configured ? '<span class="pill income">configured</span>' : '<span class="pill">not configured</span>'}</h3>
    <p class="small muted">Everything here is off until you turn it on. For Gmail use smtp.gmail.com, port 587 STARTTLS and an <b>app password</b> (Google Account → Security → App passwords). The password is stored encrypted.</p>
    <form class="form" id="smF">
      <label class="f"><span>Host</span><input name="smtp_host" value="${esc(m.smtp_host || '')}" placeholder="smtp.gmail.com"></label>
      <label class="f"><span>Port</span><input name="smtp_port" type="number" value="${esc(m.smtp_port || '587')}"></label>
      <label class="f"><span>Security</span><select name="smtp_security">${[['starttls', 'STARTTLS (587)'], ['ssl', 'SSL/TLS (465)'], ['none', 'None']].map(([k, l]) => `<option value="${k}" ${m.smtp_security === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="f"><span>User</span><input name="smtp_user" value="${esc(m.smtp_user || '')}" autocomplete="off"></label>
      <label class="f"><span>App password</span><input name="smtp_password" type="password" autocomplete="new-password" placeholder="${m.has_password ? '•••••••• (saved, leave blank to keep)' : ''}"></label>
      <label class="f"><span>From address</span><input name="smtp_from" value="${esc(m.smtp_from || '')}" placeholder="Finance <me@example.com>"></label>
      <label class="f full"><span>To (comma separated)</span><input name="smtp_to" value="${esc(m.smtp_to || '')}" placeholder="me@example.com, partner@example.com"></label>
      <label class="f"><span>Email language</span><select name="email_lang">${[['en', 'English'], ['zh-CN', '简体中文'], ['zh-TW', '繁體中文']].map(([k, l]) => `<option value="${k}" ${d.email_lang === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <div class="full row end"><button type="button" class="btn" id="smTest">Send test email</button><button class="btn pri">Save</button></div></form></div>
    ${job('reminders', '⏰ Reminder emails', '<div class="f full"><span class="small">Weekdays <span class="muted">(none = every day)</span></span> ' + weekdayBoxes('reminders', d.email_reminders_weekdays) + '</div><div class="small muted full">Lists reminders inside their lead window (Reminders → lead days) and overdue ones. Nothing due → no email.</div>')}
    ${job('daily', '📊 Daily report', '<div class="f full"><span class="small">Weekdays <span class="muted">(none = every day)</span></span> ' + weekdayBoxes('daily', d.email_daily_weekdays) + '</div><div class="small muted full">Covers yesterday.</div>')}
    ${job('weekly', '📊 Weekly report', '<div class="f full"><span class="small">Send on</span> ' + weekdayBoxes('weekly', d.email_weekly_weekdays) + '</div><div class="small muted full">Covers the 7 days up to yesterday.</div>')}
    ${job('monthly', '📊 Monthly report', `<label class="f"><span>Day of month</span><input name="email_monthly_monthday" type="number" min="1" max="31" value="${esc(d.email_monthly_monthday || '1')}"></label><div class="small muted full">Covers the previous calendar month.</div>`)}
  </div><p class="small muted" style="margin-top:10px">Reports include income/expense, top categories, tax &amp; tips, investments (principal vs returns), reminders due, reimbursements outstanding and projects vs budget.</p>`;
  $('#smF').onsubmit = async e => { e.preventDefault(); await guard(() => api('/api/email/settings', {method: 'PUT', body: Object.fromEntries(new FormData(e.target))})); toast(i18nText('Saved')); route(); };
  $('#smTest').onclick = async () => { const r = await guard(() => api('/api/email/test', {method: 'POST', body: {}})); toast('✓ ' + i18nText('Test email sent to') + ' ' + r.to.join(', ')); };
  el.querySelectorAll('.job-f').forEach(f => {
    const k = f.dataset.job;
    f.onsubmit = async e => {
      e.preventDefault();
      const b = Object.fromEntries(new FormData(f)); b[`email_${k}_enabled`] = f[`email_${k}_enabled`].checked;
      if (k !== 'monthly') b[`email_${k}_weekdays`] = readWeekdays(f, k);
      await guard(() => api('/api/email/settings', {method: 'PUT', body: b})); toast(i18nText('Saved')); route();
    };
  });
  el.querySelectorAll('[data-send]').forEach(b => b.onclick = async () => {
    const r = await guard(() => api(`/api/email/send?kind=${b.dataset.send}`, {method: 'POST'}));
    toast(r.skipped ? i18nText('Nothing to send') + ' (' + r.reason + ')' : r.ok ? '✓ ' + i18nText('Sent') : '✗ ' + r.error, !r.ok); route();
  });
};
