/* i18n: UI strings are written in English in the source; locale files (static/locales/<code>.js) map English → translation.
   Order: saved choice (localStorage / server pref) > browser languages (navigator.languages) > English.
   Rendered HTML is translated by a DOM pass (text nodes + placeholder/title/aria-label); code can call t('…'). */
const I18N = {dicts: {en: {}}, names: {en: 'English'}, lang: 'en', meta: {}, rx: []};
function i18nAdd(code, name, dict) { I18N.dicts[code] = Object.assign(I18N.dicts[code] || {}, dict); I18N.names[code] = name; }
function i18nSupported() { return Object.keys(I18N.names); }
/* browser language list -> best supported code (exact, then script/region aliases, then base language) */
function i18nMatch(list) {
  const sup = i18nSupported();
  for (const raw of list || []) {
    const l = String(raw || '').replace('_', '-'); if (!l) continue;
    const low = l.toLowerCase();
    const exact = sup.find(s => s.toLowerCase() === low); if (exact) return exact;
    if (low.startsWith('zh')) {
      const trad = /hant|tw|hk|mo/.test(low);
      if (trad && sup.includes('zh-TW')) return 'zh-TW';
      if (sup.includes('zh-CN')) return 'zh-CN';
    }
    const base = sup.find(s => s.split('-')[0].toLowerCase() === low.split('-')[0]); if (base) return base;
  }
  return 'en';
}
function i18nDetect(saved, browser) {
  if (saved && i18nSupported().includes(saved)) return saved;
  return i18nMatch(browser || (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language]));
}
function i18nSet(code, {persist = true} = {}) {
  const prev = I18N.lang;
  I18N.lang = i18nSupported().includes(code) ? code : 'en';
  if (persist) try { localStorage.setItem('lang', I18N.lang); } catch (e) { /* private mode */ }
  document.documentElement.lang = I18N.lang;
  i18nCompile();
  if (prev !== I18N.lang && I18N.observing) i18nApply(document.body);  // static shell rendered before the switch
}
function i18nSaved() { try { return localStorage.getItem('lang'); } catch (e) { return null; } }
const i18nLocale = () => I18N.lang;  // for Intl
/* keys may contain {placeholders}: "{n} transactions" -> regex */
function i18nCompile() {
  const d = I18N.dicts[I18N.lang] || {};
  for (const k of Object.keys(d)) {                       // "▾ Expand all" is also reachable as "Expand all"
    const core = k.replace(/^[^A-Za-z0-9\u3400-\u9fff"'(]+\s*/, '').replace(/\s*[:：]?(?:\s*[→ⓘ…›»]+)?\s*$/, '');
    if (core && core !== k && d[core] === undefined) d[core] = d[k].replace(/^[^A-Za-z0-9\u3400-\u9fff"'(（]+\s*/, '').replace(/\s*[:：]?(?:\s*[→ⓘ…›»]+)?\s*$/, '');
  }
  I18N.rx = Object.keys(d).filter(k => k.includes('{')).map(k => {
    const names = []; const src = k.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\\?\{(\w+)\\?\}/g, (m, n) => { names.push(n); return '(.+?)'; });
    return {rx: new RegExp('^' + src + '$'), names, val: d[k]};
  });
}
/* t(key, vars): translation of an English string; missing key -> the English itself (with vars filled in) */
function t(key, vars) {
  const d = I18N.dicts[I18N.lang] || {};
  let s = (d[key] !== undefined && d[key] !== '') ? d[key] : key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, n) => vars[n] !== undefined ? vars[n] : m);
  return s;
}
const BILINGUAL = /^(.*?[A-Za-z0-9).?!%…])\s+([\u3400-\u9fff][^A-Za-z]*)$/;
/* translate one rendered string (keeps leading/trailing whitespace, emoji/symbol prefixes and a trailing colon) */
function i18nText(s) {
  if (!s || !/[A-Za-z\u3400-\u9fff]/.test(s)) return s;
  const m = s.match(/^(\s*[^A-Za-z0-9\u3400-\u9fff"'(]*\s*)([\s\S]*?)(\s*[:：]?(?:\s*[→ⓘ…›»]+)?\s*)$/);
  const pre = m[1], core = m[2], post = m[3];
  if (!core) return s;
  const zh = I18N.lang.startsWith('zh');
  const d = I18N.dicts[I18N.lang] || {};
  if (I18N.meta[core] !== undefined) return pre + I18N.meta[core] + post;           // categories / types from the server
  if (I18N.lang !== 'en' && d[core]) return pre + d[core] + post;
  if (I18N.lang !== 'en') for (const r of I18N.rx) { const mm = core.match(r.rx); if (mm) { const v = {}; r.names.forEach((n, i) => v[n] = mm[i + 1]); return pre + t(r.val, v) + post; } }
  if (I18N.lang !== 'en') {
    if (/ · | → /.test(core)) {                                                       // "Owed $1 · Limit $2" -> per segment
      const parts = core.split(/( · | → )/); const tr = parts.map(x => / · | → /.test(x) ? x : i18nText(x));
      if (tr.join('') !== core) return pre + tr.join('') + post;
    }
    const pm = core.match(/^([A-Za-z][A-Za-z &'/-]*?)(\s+)([-+−]?[\d$¥€£(#]|[A-Z]{1,3}\$|\d)([\s\S]*)$/);  // "Left US$12 …"
    if (pm && d[pm[1]]) return pre + d[pm[1]] + pm[2] + pm[3] + pm[4] + post;
    const sm = core.match(/^([-+]?[\d.,]+%?)\s+([A-Za-z][A-Za-z ()]*)$/);         // "23 transactions"
    if (sm && d['{n} ' + sm[2]]) return pre + t(d['{n} ' + sm[2]], {n: sm[1]}) + post;
  }
  const b = core.match(BILINGUAL);                                                    // leftover "English 中文" labels
  if (b && (d[b[1]] !== undefined || (I18N.dicts['zh-CN'] || {})[b[1]] !== undefined)) return pre + (zh ? (d[b[1]] || b[2]) : b[1]) + post;
  return s;
}
/* server meta: bilingual labels -> chosen language (no translation of user data: we only pick the field it already has) */
function i18nMeta(meta) {
  const zh = I18N.lang.startsWith('zh'), out = {};
  const both = (en, z) => { if (en && z) out[`${en} ${z}`] = zh ? z : en; };
  const walk = (cats, parent) => (cats || []).forEach(c => { both(c.name, c.name_zh); if (zh && c.name_zh && !out[c.name]) out[c.name] = c.name_zh; if (c.label) out[c.label] = (parent ? pick(parent) + ' › ' : '') + pick(c); walk(c.children, c); });
  const pick = c => (zh && c.name_zh) ? c.name_zh : c.name;
  walk(meta.categories);
  (meta.adj_reasons || []).forEach(r => both(r.name, r.name_zh));
  Object.values(meta.account_types || {}).forEach(v => [v.label, v.group].forEach(l => { const b = (l || '').match(BILINGUAL); if (b) out[l] = zh ? b[2] : b[1]; }));
  I18N.meta = out;
}
const I18N_ATTRS = ['placeholder', 'title', 'aria-label'];
function i18nApply(root) {
  if (!root) return;
  if (root.nodeType === 3) { const v = i18nText(root.nodeValue); if (v !== root.nodeValue) root.nodeValue = v; return; }
  if (root.nodeType !== 1 || root.closest && root.closest('[data-notr],script,style,textarea')) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
    acceptNode: n => n.nodeType === 1 && (n.matches('[data-notr],script,style,textarea')) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  const nodes = [root]; let n; while ((n = w.nextNode())) nodes.push(n);
  for (const x of nodes) {
    if (x.nodeType === 3) { const v = i18nText(x.nodeValue); if (v !== x.nodeValue) x.nodeValue = v; continue; }
    for (const a of I18N_ATTRS) if (x.hasAttribute && x.hasAttribute(a)) { const o = x.getAttribute(a), v = i18nText(o); if (v !== o) x.setAttribute(a, v); }
    if (x.tagName === 'INPUT' && /^(button|submit)$/i.test(x.type) && x.value) { const v = i18nText(x.value); if (v !== x.value) x.value = v; }
  }
}
function i18nObserve() {
  I18N.observing = true;
  i18nApply(document.body);
  new MutationObserver(ms => { for (const m of ms) { if (m.type === 'characterData') i18nApply(m.target); else m.addedNodes.forEach(i18nApply); } })
    .observe(document.body, {childList: true, subtree: true, characterData: true});
}
/* Intl helpers */
function fmtDate(s, opts = {year: 'numeric', month: 'short', day: 'numeric'}) {
  if (!s) return '';
  const d = new Date(String(s).length <= 10 ? s + 'T00:00:00' : String(s).replace(' ', 'T'));
  return isNaN(d) ? s : new Intl.DateTimeFormat(i18nLocale(), opts).format(d);
}
function fmtMonth(ym) { return fmtDate(ym + '-01', {year: 'numeric', month: 'long'}); }
// detection runs again in loadMeta() once the locale files (loaded after this one) and server prefs are known
document.addEventListener('DOMContentLoaded', () => i18nSet(i18nDetect(i18nSaved()), {persist: false}));
