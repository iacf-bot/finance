// Node test for static/js/00_i18n.js + locale files (run by tests/test_i18n.py). No DOM needed for the logic.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.join(__dirname, '..', '..');
const store = {};
const ctx = {console, Intl, navigator: {languages: ['fr-FR'], language: 'fr-FR'},
  localStorage: {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }},
  document: {documentElement: {}, addEventListener() {}}};
vm.createContext(ctx);
const load = f => vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8').replace(/^const (\w+)/gm, 'var $1'), ctx, {filename: f});
['static/js/00_i18n.js', 'static/locales/en.js', 'static/locales/zh-CN.js', 'static/locales/zh-TW.js'].forEach(load);
const r = s => vm.runInContext(s, ctx);

// detection: saved choice > browser languages > English
assert.strictEqual(r("i18nDetect(null, ['fr-FR','de'])"), 'en');
assert.strictEqual(r("i18nDetect(null, ['zh-CN'])"), 'zh-CN');
assert.strictEqual(r("i18nDetect(null, ['zh'])"), 'zh-CN');
assert.strictEqual(r("i18nDetect(null, ['zh-Hant-TW'])"), 'zh-TW');
assert.strictEqual(r("i18nDetect(null, ['zh-HK'])"), 'zh-TW');
assert.strictEqual(r("i18nDetect(null, ['fr','en-GB','zh-CN'])"), 'en');
assert.strictEqual(r("i18nDetect('zh-TW', ['en-US'])"), 'zh-TW');
assert.strictEqual(r("i18nDetect('xx-YY', ['zh-CN'])"), 'zh-CN');  // unknown saved value ignored
assert.strictEqual(r("i18nDetect(null)"), 'en');                  // navigator.languages = fr-FR

// lookup, missing-key fallback, {vars}, pattern keys
r("i18nSet('zh-CN')");
assert.strictEqual(store.lang, 'zh-CN');
assert.strictEqual(r("t('Settings')"), '设置');
assert.strictEqual(r("t('A key nobody translated')"), 'A key nobody translated');
assert.strictEqual(r("i18nText('Language')"), '语言');
assert.strictEqual(r("i18nText('All →')"), r("t('All')") + ' →');
r("i18nSet('zh-TW')");
assert.strictEqual(r("t('Settings')"), '設定');
r("i18nSet('en')");
assert.strictEqual(r("t('Settings')"), 'Settings');
assert.strictEqual(r("i18nText('Investments')"), 'Investments');
r("i18nSet('de')");  // unsupported -> English
assert.strictEqual(r("I18N.lang"), 'en');
// Intl formatting follows the locale
r("i18nSet('zh-CN')");
assert.ok(/2026/.test(r("fmtMonth('2026-10')")) && /月/.test(r("fmtMonth('2026-10')")));
console.log('ok');
