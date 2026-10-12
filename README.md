# IACF Finance

A self-hosted personal finance app modeled on Suishouji (随手记). It runs as a single FastAPI process with a SQLite
database and a responsive single-page UI, so it works on a phone too. UI and default categories are bilingual
(English/中文).

* **URL:** http://127.0.0.1:8088 or http://xxx.xxx.xxx.xxx:8088 (Tailscale). It binds only those two addresses,
  never 0.0.0.0.
* **Data:** `data/finance.db` (SQLite, WAL mode), with backups in `data/backups/`.
* **First run:** open the URL. You'll be sent to `/setup` to choose the single-user password.

Screenshots (made with clearly labeled `[DEMO]` data, which has since been removed) are in `screenshots/`.

## Features
* **Accounts**
  * Types: cash, checking, savings, credit card, e-wallet (Alipay/WeChat/PayPal), brokerage, retirement, crypto,
    loan, payable and receivable.
  * Accounts are organized into groups (现金 / 金融 / 信用卡 / 虚拟 / 投资 / 负债 / 债权), and each account can
    have its own currency.
  * Balance = opening balance + transactions. Liabilities show the amount owed. Credit cards also show the limit
    and available credit.
  * Net worth = assets − liabilities, converted to the base currency with manual FX rates
    (Settings → Currencies).
  * Each account can have aliases (for quick-add) and card last-4 digits (for email alerts).
  * **Accounts page tree:** each group is a collapsible node (click or tap the row).
    * The group row shows the number of accounts and the group total in USD (or whatever your base currency is).
      Liability groups show the amount owed, and mixed-currency groups also show per-currency subtotals.
    * There are *Expand all* and *Collapse all* buttons. Which groups are collapsed is remembered in the browser.
    * Accounts at 0 are dimmed, and you can hide them with *hide zero balances*.
    * The group totals come from `GET /api/accounts`, which now includes a `tree`, and they add up to net worth.
* **Set balance 调整余额**
  * Works like Suishouji's 余额变更. On Accounts, press **⚖️ Set balance** (or change *Current balance* / *Amount owed
    now* in the account's Edit form) and enter the real balance. For credit cards, loans and payables, enter the
    amount owed.
  * Optionally pick a date/time (default now) and add a note.
  * The app records a **balance adjustment** transaction for the difference against the computed balance on that
    date. Its source is `balance_set`, its category is *Balance adjustment 余额调整*, and its note reads
    "Balance set to X (was Y)". If the difference is 0, nothing is recorded.
  * Adjustments change balances and net worth but never count as income or expense in reports, budgets or the
    dashboard. They can be edited or deleted like any transaction.
  * A new account's opening balance is recorded as an *Opening balance 期初余额* adjustment on its start date.
* **Transactions**
  * Kinds: expense, income, transfer, refund and balance adjustment, plus investment buy, sell, dividend and fee.
  * Fields: date/time, amount (in any currency, converted to the account currency), two-level category,
    member, merchant, project, tags, note, source, and an attachment.
  * Search and filter by text, dates, type, account, category, member, merchant, project, tag and source.
  * Edit, delete, bulk delete, CSV export, and CSV import with column mapping.
* **New transaction form 记一笔**
  * **Quick add → form:** next to *Add* in the header box (and on the Review page) there is **✎ Review / Open in
    form**. It parses your text without saving and opens the form pre-filled: type, amount, currency, account
    (From/To for transfers), category, merchant, member, tags, date and note. Fields the parser was unsure about are
    highlighted in orange ("⚠ check"). *Add* still saves straight away as before.
  * In the quick-add box, **Enter** = Add and **Shift+Enter** = open the form. You can swap them in Settings →
    General (*Quick-add box: pressing Enter…*).
  * *To review* items (✎ Review) open in the same form, with missing amount/account/category highlighted.
  * **Save & add another 保存并继续** (or **Ctrl/⌘+Enter**) saves, shows a toast and clears amount, merchant,
    note, tags and attachment. It keeps type, account, date, currency, member and ledger, and puts the cursor back
    in Amount. **Enter in Amount** = Save.
  * **Remembers last used** account (From/To for transfers), category, currency and member per transaction type.
    These are stored on the server (`GET/PUT /api/prefs`, `POST /api/prefs/tx-last`, or `"remember": true` on
    `POST /api/transactions`), so phone and PC share them, with localStorage as a fallback. Choosing an account
    switches the currency to the account's currency.
  * Transfers show **From 转出账户 → To 转入账户**. *Amount received* only appears when the two currencies differ.
    Currency pickers show symbols ($ USD, ¥ CNY, ¥ JPY …).
  * On phones the form is full screen with large inputs (no iOS zoom) and a sticky button bar.
  * Dry-run quick-add (`{"dry_run": true}`) now also returns `prefill` and `uncertain`.
* **Reason for difference 差额原因** (on every balance adjustment: Set balance, the account Edit form, the
  *Balance adj.* type in the transaction form, quick-add `balance …` and the API `reason` / `adj_reason` field)

  | Reason | Reports (income / expense) | Investment income 投资收益 | Investment returns |
  |---|---|---|---|
  | Investment gain/loss 投资收益/亏损 (labelled gain or loss by sign) | excluded | **yes** (+/−) | part of the gain |
  | Interest 利息 | excluded | **yes** | part of the gain |
  | Fees 费用 | **expense** on normal accounts; on investment accounts it reduces investment income instead | investment accounts only (−) | part of the gain (net of fees) |
  | Unrecorded spending 未记支出 | **expense**, labelled "Unrecorded spending 未记支出 (balance adj.)" | – | money out |
  | Unrecorded income 未记收入 | **income**, labelled "Unrecorded income 未记收入 (balance adj.)" | – | money in |
  | Correction/other 校正/其他 (default; also every older adjustment) | excluded, as before | – | money in/out |
  | Opening balance 期初余额 | excluded | – | money in |

  * The default is Investment gain/loss on brokerage, retirement and crypto accounts, and Correction/other
    everywhere else. You can change the reason later on the transaction.
  * Reasons are stored as sub-categories of *Balance adjustment 余额调整* (built in, can't be deleted).
  * Investment income = gain/loss + interest + dividends − investment fees. It is shown on the dashboard
    (Balance card and trend chart) and in Reports, separately from income and expense.
  * Quick-add takes a reason word: `balance brokerage 52000 gain`, `余额 apple save 10250 利息`,
    `balance checking 830 未记支出`.
* **Investment returns 投资收益** (Investments page)
  * Covers each investment-type account (brokerage, retirement, crypto), each group, and *All investments*.
  * Periods: this month, year to date, last 12 months, all time, or custom. Values are in the base currency at
    today's FX rates.
  * **Net contributions** = money in − money out during the period. Money in/out means transfers from/to other
    accounts, income/expenses recorded on the account, and adjustments classified Correction, Opening balance or
    Unrecorded. Transfers between accounts of the same group are not counted in the group/total rows. Buys and
    sells only move money between cash and holdings, so they are not contributions.
  * **Gain** = end value − start value − net contributions. This equals investment gain/loss adjustments + interest
    + dividends − fees + market gains on tracked holdings (shown as a breakdown).
  * **Simple return** = gain ÷ (start value + net contributions).
  * **Money-weighted return (Modified Dietz)** = gain ÷ (start value + Σ flowᵢ × wᵢ), where wᵢ is the share of the
    period left after flowᵢ.
  * **Annualized** = (1 + Modified Dietz)^(365/days) − 1, for periods longer than a year.
  * **XIRR** is the yearly rate r that solves −start − Σ flowᵢ/(1+r)^tᵢ + end/(1+r)^T = 0.
  * The page also shows a cumulative-gain chart and per-year returns for the selected row (click a row).
  * API: `GET /api/investments/returns?period=ytd|month|1y|all|custom&start=&end=&scope=all|group:<id>|account:<id>`.
  * Past values of tracked holdings use the last trade price before that date. Today's value uses the latest quote.
* **Reclassify adjustments** (Investments → 🏷 Reclassify adjustments…), a one-time tool for imported 随手记
  余额变更:
  * It lists the unclassified adjustments on investment accounts (optionally also savings accounts → Interest) and
    proposes a reason. An account's *first* record is proposed as Opening balance, because exports have no
    starting balances; calling it a gain would inflate returns.
  * You can tick or untick rows and change any reason. **Nothing changes until you press Apply**, and a backup
    is taken first.
  * API: `GET /api/adjustments/reclassify[?include_savings=1]` (read-only preview);
    `POST /api/adjustments/reclassify {"items":[{"id":..,"reason":..}]}`.
* **Scheduled transactions 周期记账** (sidebar → *Scheduled 定时*)
  * Schedules can create any transaction type (expense, income, transfer with from/to and received amount,
    refund, balance adjustment with reason, investment trades). The editor is the same form as *New transaction*,
    plus a **Repeat** section.
  * Repeat options: once, every N days, weekly on chosen weekdays / every N weeks, monthly on day X (or *last
    day*; short months use their last day), monthly on the Nth weekday (1st–5th or last), every N months,
    quarterly, yearly (Feb 29 → Feb 28 in other years).
  * Each schedule has a start date, an optional end date *or* number of times, and a time of day.
  * *Post automatically* or *Create in To review*. **Variable amount** always goes to To review, with the
    amount as a suggestion.
  * The editor shows the next 5 dates. The page and the dashboard list everything due in the next 30 days.
  * **🔁 Make recurring** on any existing transaction creates a schedule from it. It starts one period later, so
    the original isn't duplicated.
  * Pausing skips the dates missed while paused. Deleting a schedule can keep or delete the transactions it
    already created. Editing a generated transaction never changes the schedule.
  * **Runner.** The app's built-in background job runs once a day at `schedule_run_time` (default 00:15,
    `timezone` default America/Los_Angeles; DST-safe) and once at server start. It creates every occurrence due
    up to today, catching up after downtime for at most `schedule_catchup_days` (default 90). Older ones are
    skipped and counted in the summary.
  * Generated transactions have source `scheduled` and `schedule_id`.
  * It is idempotent: each (schedule, date) is recorded in `schedule_occurrences` in the same database
    transaction, so re-runs never duplicate, and a generated transaction you delete is not re-created.
  * **Run now** / **Preview run** buttons and a run log (time, trigger, created, errors) are on the page.
  * API: `GET/POST /api/schedules`, `GET/PUT/DELETE /api/schedules/{id}` (`?keep_history=false` also deletes
    generated transactions), `POST /api/schedules/{id}/pause|resume`, `POST /api/schedules/preview`,
    `GET /api/schedules/upcoming?days=30`, `POST /api/schedules/run {"date"?, "dry_run"?}`,
    `GET /api/schedules/runs`, `GET /api/schedules/from-transaction/{tx_id}`.
  * CLI, for cron on a machine where the server isn't always running:
    `python -m app.cli run-schedules [--date YYYY-MM-DD] [--dry-run]`, e.g. crontab
    `15 0 * * * cd /path/to/finance-app && FINANCE_DB=data/finance.db .venv/bin/python -m app.cli run-schedules`.
    On this machine the in-app job is the trigger (there's no cron).
* **Investments:** holdings from your trades, with average cost, unrealized/realized P/L and dividends.
  Quotes come from Yahoo (chart API, with yfinance as a fallback). If a quote can't be fetched, you can set a
  manual price.
* **Budgets:** monthly budgets, total or per category, with progress bars and "copy last month".
* **Dashboard & reports**
  * Dashboard: month income/expense/balance, spending pie, 12-month income-vs-expense chart, net-worth history,
    balances, budgets and recent activity.
  * Reports: group by category, member, merchant, project or account over any date range.
* **Capture:** quick-add (free text, EN/中文), the email ingest endpoint, IMAP Email Monitoring and a Telegram
  bot. Anything uncertain goes to the **To review 待确认** inbox, where you approve or edit it.
* **Imports**
  * Suishouji (随手记) `.xls`, `.xlsx` and `.csv` exports.
  * Bank/card CSVs (Chase, Amex, BofA, Capital One, Citi, Discover, Alipay, WeChat) or any CSV via column mapping.
* **Backups:** download, restore and scheduled snapshots (30 kept), with an optional rclone upload to Google Drive.
* **Demo data:** Settings → Demo data can load clearly labeled `[DEMO]` sample data and remove it again in one
  click. The app ships with no data.

## Running
```bash
./start.sh        # creates .venv on first run, then starts in the background with nohup
./stop.sh
tail -f data/logs/server.log
```
Settings come from environment variables or an optional `.env` file next to the app (see `.env.example`). The app reads `.env`
itself, so this works the same on Linux and Windows; `start.sh` also reads it.
* `HOST`: the address(es) to listen on, comma separated. The app default is `127.0.0.1`; `start.sh` defaults to
  `127.0.0.1,xxx.xxx.xxx.xxx` on this server. Any address that can't be bound is skipped with a warning. `0.0.0.0` is allowed,
  but it logs a warning: prefer explicit addresses.
* `PORT` (default `8088`)
* `DATA_DIR`: where `finance.db`, backups, logs, attachments and `secret.key` go. The default is `./data`; relative paths are relative to the app folder.
* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`
* The older names `FINANCE_HOSTS`, `FINANCE_PORT` and `FINANCE_DB` (the full database path) still work and take priority.

**Windows:** see [RUN_ON_WINDOWS.md](RUN_ON_WINDOWS.md). It covers `setup.ps1`, `start.ps1` and `stop.ps1`, double-click
`start.bat` / `stop.bat`, and the optional `install-service.ps1` boot task. No Node.js or WSL is needed.

`watchdog.sh` restarts the server if it isn't running. To use it, put this in your crontab:
`*/5 * * * * /workspace/finance-app/watchdog.sh`

**Docker (optional):** run `docker compose up -d --build`. The compose file publishes the port only on
`127.0.0.1:8088` and `xxx.xxx.xxx.xxx:8088`, and `./data` is mounted into the container.

### Password & admin CLI
```bash
.venv/bin/python -m app.cli reset-password   # back to the first-run /setup page
.venv/bin/python -m app.cli show-api-key
.venv/bin/python -m app.cli clear-demo
.venv/bin/python -m app.cli backup           # snapshot now (+ upload if rclone is configured)
```
You can also change the password in Settings → Password.

## Security
* There is one user. The password is hashed with PBKDF2-SHA256 (390k iterations). Sessions are signed cookies,
  and changing the password invalidates old sessions. Login attempts are rate-limited.
* Every page and API call requires either a logged-in session or the header `X-API-Key: <key>`. The key is
  generated on first run, shown in **Settings → API & Bots** (where it can also be regenerated), and written to
  `data/API_KEY.txt` (mode 600).
* IMAP app passwords are encrypted with Fernet using `data/secret.key` (mode 600) and are never sent back to the
  browser; only a mask is shown. If you lose `secret.key`, you'll need to re-enter the mailbox passwords.
* Keep `data/` private. It holds the database, keys and backups.

## API (curl examples)
```bash
KEY=$(cat data/API_KEY.txt); H="X-API-Key: $KEY"; B=http://127.0.0.1:8088

curl -s -H "$H" $B/api/accounts                      # accounts, balances, net worth
curl -s -H "$H" "$B/api/transactions?q=coffee&start=2026-10-01&limit=50"
curl -s -H "$H" -X POST $B/api/transactions -H 'Content-Type: application/json' \
  -d '{"kind":"expense","date":"2026-10-11 12:30","amount":12.5,"account_id":1,"category_id":12,"note":"lunch"}'
curl -s -H "$H" -o tx.csv "$B/api/transactions/export.csv?start=2026-01-01"
curl -s -H "$H" $B/api/review                         # items waiting for review
curl -s -H "$H" -X POST $B/api/transactions/42/approve
curl -s -H "$H" "$B/api/dashboard?month=2026-10"
curl -s -H "$H" "$B/api/reports?group_by=category&start=2026-01-01&end=2026-12-31"
curl -s -H "$H" -X POST $B/api/backups               # snapshot now
curl -s -H "$H" -o finance.db $B/api/backups/download
```
Interactive API docs are at `/api/docs` (you need to be logged in).

### Set balance — `POST /api/accounts/{id}/set-balance`
```bash
curl -s -H "$H" -X POST $B/api/accounts/3/set-balance -H 'Content-Type: application/json' \
  -d '{"balance": 5230.00, "date": "2026-10-11 09:00", "note": "matched bank app"}'
```
* `balance` is the real balance. For liability accounts it is the amount owed, unless you send `"as_owed": false`.
* `date` is optional. The default is now; a date on its own means the end of that day.
* The response gives `created`, `previous`, `difference`, `message` and the `transaction` (or `null` when nothing
  changed).
* `PUT /api/accounts/{id}` with `{"balance": …}` does the same thing.
* Quick-add also works: `余额 支付宝 1234.5`, `balance checking 5230`, `balance amex 820` (owed),
  `bal cash 40 yesterday`. Email ingest never sets balances.

### Quick-add — `POST /api/quick-add`
```bash
curl -s -H "$H" -X POST $B/api/quick-add -H 'Content-Type: application/json' -d '{"text":"lunch 25.5 chase sapphire"}'
curl -s -H "$H" -X POST $B/api/quick-add --data-binary '午饭 35 支付宝'      # text/plain body also works
curl -s -H "$H" -X POST $B/api/quick-add -H 'Content-Type: application/json' -d '{"text":"dinner 40","dry_run":true}'
```
Optional body fields:
* `source`: one of web, api, email, bot, message
* `dry_run`: parse only, don't save
* `review`: always send to the review inbox
* `ledger_id`

The response contains `status` (`posted` or `review`), `confidence` (0–1) and the transaction.

Every response also has a short top-level **`message`** that Siri can read aloud. It is in Chinese when the input
is Chinese:

| Case | `message` |
|---|---|
| posted | `Saved: Coffee $4.50 · Cash` · `已记: 午饭 ¥35 · 支付宝` |
| review | `Needs review: lunch $25 (account not found)` |
| transfer | `Transferred $300 Checking → Amex` |
| `balance …` / `reconcile …` | `Balance set: Alipay ¥1,234.50` · `Balance unchanged: …` · `Reconciled: …` (the long explanation is in `detail`) |
| `dry_run` | `Preview: Coffee $4.50 · Cash` · `Preview: balance Amex → owed $250` |
| error (HTTP 400) | JSON `{"detail": "...", "message": "Couldn't save: …", "ok": false}` |

The body can be JSON (`{"text": ...}`, any `application/json; charset=…`; the `text` key is case-insensitive;
`dry_run`/`review` accept `true` or `"true"`), a form or plain text. If text is sent with a JSON content type, it
is treated as the text. `POST /api/accounts/{id}/set-balance` is separate and keeps its own `message`.

**iPhone / Apple Watch Shortcut:**
1. Use *Dictate Text* (or *Ask for Input*).
2. Add *Get Contents of URL* with:
   * URL `http://<tailscale-ip>:8088/api/quick-add`
   * Method POST
   * Header `X-API-Key: <key>`
   * Request Body JSON with `text` = the dictated text
3. Add *Get Dictionary Value* `message`, then *Show Result* / *Speak Text*.

**Syntax.** Write a free-form line containing an amount, plus optionally an account, a category word, a date,
a merchant and #tags. Order doesn't matter.

| Example | Result |
|---|---|
| `lunch 25.5 chase sapphire` | expense 25.50, Meals, Chase Sapphire |
| `午饭 35 支付宝` | expense ¥35 on Alipay (account currency) |
| `coffee 4.5 sapphire yesterday #work` | yesterday, tag `work` |
| `uber 18 amex 10/3` · `打车 23 微信 10月3日` | explicit dates (`YYYY-MM-DD`, `M/D`, `X月Y日`, `yesterday/昨天/前天`) |
| `groceries 86.20 at Whole Foods amex` | merchant from `at …` / `@ …` / `在…` |
| `salary 5000 to checking` · `工资 8k 招行` | income (from category keywords) |
| `paid 300 from checking to amex` · `转账 500 支付宝 到 招行` | transfer (`from … to …`, 转/到) |
| `refund 20 amazon sapphire` · `退款 15 支付宝` | refund |
| `rent 2.1k checking` · `房租 1.2万 招行` | multipliers k / w / 万 / 千 |
| `taxi 300 CNY wechat` · `¥88 晚饭 微信` | explicit currency (USD/$, CNY/RMB/¥/元, EUR/€ …) |

**How accounts are matched:** by account name, alias (Accounts → Edit → Aliases, e.g. `sapphire, csp`) or card
last-4 digits. If no account is given, the default account (Settings → General) is used.

**Confidence score:** amount 0.40, account 0.30, category 0.25, plus small bonuses. Anything below the review
threshold (Settings → General, default 0.75) goes to **To review**. A line with no amount or no account always
goes to review.

### Email ingest — `POST /api/ingest/email`
```bash
curl -s -H "$H" -X POST $B/api/ingest/email -H 'Content-Type: application/json' -d '{
  "from":"alerts@chase.com","subject":"Your $18.40 transaction",
  "body":"You made a $18.40 transaction with SAMPLE COFFEE on your card ending in 4242.",
  "message_id":"<optional-unique-id>","received_at":"2026-10-11T08:10:00","auto_post":false}'
```
The same parser is used for IMAP mail:
1. Parsing rules are tried by sender. If no rule matches, a generic extractor runs.
2. The account is matched by card last-4, then by name, then the mailbox's default account.
3. Messages are deduplicated by Message-ID.
4. The result goes to review, or is posted directly when `auto_post` is true and confidence is above the
   threshold.
5. Card payment notices always go to review as transfers.

This endpoint is handy for forwarding mail from Gmail filters, Zapier, iOS Shortcuts and similar tools.

### Email Monitoring (IMAP) — Settings → Email Monitoring 邮件监控
* **Add mailbox:** fill in host, port, SSL, username, **app password** (encrypted at rest), folder, polling
  interval, enabled, sender allowlist, subject keywords, after-processing action (mark read, or move to a
  folder), default account and auto-post.
  * Gmail: `imap.gmail.com:993`. Create the app password under Google Account → Security → 2-Step Verification
    → App passwords.
  * Outlook: `outlook.office365.com:993`.
  * QQ / 163: enable IMAP in their web settings and use the authorization code they give you (授权码).
* **Buttons:** *Test connection* (lists folders and the unread count) and *Run now*. A background poller checks
  each enabled mailbox at its interval and only fetches recent mail (`SINCE` lookback).
* **Parsing rules 解析规则:** each rule has a sender regex, an optional subject regex, and regexes for amount,
  merchant, last-4 and date, plus a kind and priority. Presets are included for Chase, American Express,
  Bank of America, Citi, Capital One, Discover, Alipay 支付宝 and Chinese bank-card alerts (招行/工行/建行… 动账提醒).
  The presets are best effort; check them with **Test with a sample email**, then edit or add your own.
* **Ingest log 处理日志:** shows every message processed and its result: posted, review, duplicate, skipped or
  error.

### Suishouji (随手记) import — Settings → Import 导入
1. **Export from Suishouji** using one of these:
   * Web: 新功能 → 导入导出 → 数据导出 → 随手记 web版 (`.xls`, one sheet per type)
   * App / 神象云账本: 设置 → 数据导出 (`.xlsx`)
   * App CSV: 设置 → 高级功能 → 备份与同步 → 备份与恢复 → 本地备份与恢复 → 导出Excel文件(CSV)
2. **Upload and preview.** Sheets and columns are detected by header name, with synonyms for older or alternative
   layouts. Any column can be remapped by hand.
3. **Map each Suishouji account** to an existing account, or create a new one. The type is guessed (信用卡 →
   credit card, 支付宝/微信 → e-wallet, 储蓄卡 → checking, 负债/借入 → payable, 债权/借出 → receivable…).
   Categories (一级 › 二级) are mapped or created the same way. Members, merchants and projects are created
   automatically.
   The preview also shows each account's **net from file**. It flags accounts that look wrong from the file alone,
   such as an asset that ends up negative or a liability that ends up in credit. Suishouji exports don't include
   accounts' initial balances, so you can type the balance Suishouji shows today (for cards and loans, the amount
   owed) in **Balance in 随手记 today**. The opening balance is then set so the imported balance matches.
   Currencies without an exchange rate are listed too.
4. **Import.** A backup is taken automatically first.
   * 转账 (transfer) rows are imported as transfers, and the app CSV's 转出/转入 pairs are joined by 关联Id.
   * 余额变更 becomes a balance adjustment.
   * 负债变更 and 债权变更 become adjustments on payable/receivable accounts.
   * 借入/借出/收债/还债/代付/报销 become transfers. Their direction is best effort, so check the preview.
   * Rows already imported (same date + amount + account + note) are skipped when **Skip duplicates** is on.
   * The import summary shows counts by type, totals, duplicates, skipped rows and created accounts.

Format research and sources are in `docs/sui_reference.md`.

### Bank CSV import
Use Settings → Import → Bank / card CSV.
* Known formats are recognized by their headers. For anything else, map date, amount (or separate debit/credit
  columns), description, category, and the sign convention.
* Card payments are imported as transfers into review.
* Re-importing the same file skips duplicates.

## Telegram bot (optional)
1. Message @BotFather, run `/newbot`, and copy the token.
2. Put `TELEGRAM_BOT_TOKEN=...` in `.env` and run `./stop.sh && ./start.sh`.
3. Message your bot. It replies with your chat id because you're not authorized yet. Add that id to `.env` as
   `TELEGRAM_ALLOWED_CHAT_IDS=<id>` and restart.
4. From then on, every message is treated as quick-add, e.g. `lunch 25.5 sapphire`. The bot replies with what it
   recorded, or tells you the item went to review.

The bot uses long polling, so it needs no public URL. Status is shown in Settings → API & Bots.

## Backups & Google Drive
* Settings → Backup 备份 has buttons to snapshot now, download the live DB, download or restore any snapshot,
  and upload a `.db` to restore. Every restore takes a safety backup first.
* Scheduled snapshots run every `backup_interval_hours` (default 24) into `data/backups/`, and the newest
  `backup_keep` (default 30) are kept.
* **Google Drive via rclone (optional):** the app never signs in to Google itself. Set it up once on this machine:
  ```bash
  rclone config            # n) new remote → name: gdrive → storage: drive → follow the prompts
                           # on a headless box answer "n" to auto config and run `rclone authorize "drive"`
                           # on a computer with a browser, then paste the token back
  rclone mkdir gdrive:FinanceAppBackups
  ```
  Once a remote named `gdrive` exists, each scheduled backup is also copied to `gdrive:FinanceAppBackups`.
  Settings → Backup shows the rclone status and has an *Upload latest now* button.

## Tests
```bash
.venv/bin/python -m pytest -q
```
The tests cover:
* the quick-add parser (EN/中文)
* ledger math, FX conversion, investments and the API (auth, API key, CRUD, CSV import/export, backups)
* email rules, using fake sample alerts for every preset
* Suishouji import, using a synthetic workbook, a synthetic app CSV, and ezBookkeeping's MIT-licensed sample `.xls`
  files

## Layout
```
app/        main.py (routes), db.py (schema/seed), ledger.py (balances/reports), parser.py (quick-add),
            ingest.py (quick-add/email/IMAP), email_rules.py, importers.py (CSV + Suishouji), backup.py,
            quotes.py, telegram_bot.py, demo.py, serve.py, cli.py, config.py (env/.env), reconcile.py, returns.py, schedules.py
static/     SPA (index.html, login.html, style.css, js/, vendor/chart.umd.min.js)
tests/      pytest suite + fixtures
docs/       sui_reference.md
scripts/    screenshots.py (Playwright), win_common.ps1 (helpers for the Windows scripts)
setup.ps1 start.ps1 stop.ps1 install-service.ps1 *.bat   Windows scripts (RUN_ON_WINDOWS.md)
```

## Account reconciliation 对账

Compare an account with a bank/card statement and lock the balance at that date.

* **Accounts → ✔ Reconcile 对账** (or quick add `reconcile chase 1234.56` / `对账 支付宝 1000`, or `POST /api/accounts/{id}/reconcile`
  with `{balance, as_of, note, cleared_ids, adjust_reason}`). The dialog lists the transactions since the previous checkpoint with
  "cleared" checkboxes, the balance the app computes, the statement balance and the difference. A difference can be recorded as a
  balance adjustment with a *Reason for difference* (interest, fees, unrecorded spending…) or accepted ("Finalize anyway").
  Quick add only finalizes when there is no difference.
* Finalizing stores a **checkpoint** (account, statement date, balance, note) and flags every transaction of that account dated on or
  before it as reconciled (✔🔒 in lists; Transactions → filter "Reconciled 已对账").
* **Balance rule:** balance(t) = latest checkpoint at or before t + that account's transactions after it up to t. Charts, net-worth
  history and investment returns use the same rule (returns count the jump at a checkpoint as the "reconciliation" component).
  Transfers: each side follows its own account's checkpoints. Income/expense reports are unaffected.
* Adding, editing or deleting a transaction dated on/before a checkpoint does **not** change today's balance. The form warns, and with
  Settings → General → "Lock 🔒" the change needs a confirmation (API: `confirm_reconciled: true`). Edits that don't affect balances
  (note, tags, category, merchant) don't warn.
* **History** (click "✔ Reconciled …" under an account): per checkpoint the statement balance, **drift** (what the transactions now add up
  to from the previous checkpoint − statement balance) and **changed since** (effect of later edits), cleared/total count, and
  **Undo** for the latest checkpoint (un-flags its transactions; optionally deletes the adjustment it created).
* Set balance has an "Also mark as reconciled" checkbox (`reconcile: true`). Imported 随手记 余额变更 rows are normal adjustments, not checkpoints.

## Home 首页, account history 账户明细 and infinite scroll

* **Home 首页** (first in the sidebar, and the page you land on after login or at `/`):
  * Cards for the selected month's Income, Expense, Net (= income − expense) and Investment income. They follow the Reports rules.
    The ‹ Oct 2026 › switcher changes the month.
  * The quick-add box (Add / ✎ Review) and a **+ Detailed 详细记账** button for the full form.
  * Below that, the month's transactions, newest first. Scrolling keeps loading older months.
  * After you save anything, the cards and the list refresh.
  * The old Dashboard is unchanged and is now called **Dashboard 仪表盘**.
* **Account history:** click an account row (or **📜 History 明细**) on the Accounts page. It opens `#/account/<id>`:
  * The balance, plus Set balance / Reconcile / Edit, and **+ Add** pre-filled with the account.
  * A period filter (1M by default; 3M, 6M, 1Y, YTD, All, or a custom range), search, and a category filter.
  * Period opening and closing balances, inflow and outflow.
  * A **running balance** column. It's the account's real balance after each row and follows the reconciliation rule
    (latest checkpoint + later transactions). Filters hide rows but don't change the balances. For investment accounts it shows the cash part.
* **Infinite scroll** on Transactions, Home and account history. More rows load automatically as you scroll, with a
  "Load more" button as a fallback.
* **API:**
  * `GET /api/transactions?limit=50[&before=<next_cursor>]` pages by cursor, newest first (`date|id`); there's also `has_more`.
    The first page also returns `total` and `summary` (count, income, expense) for the whole filtered set. `offset=` still works.
    The default limit is now 50 (max 500); the CSV export is unlimited.
  * `GET /api/summary/month?month=YYYY-MM` gives income, expense, net, investment_income, prev/next and count.
  * `GET /api/accounts/{id}/history?period=1M|3M|6M|1Y|YTD|ALL` (or `start`/`end`) also takes `q`, `category_id`, `before` and `limit`.
    It returns items with `running_balance`/`running_shown`/`account_effect`, plus `balances` {opening, closing, inflow, outflow}.
  * New indexes: `transactions(date,id)`, `(account_id,date,id)` and `(to_account_id,date,id)`.

## Principal 本金投入 vs returns 投资收益 vs unexplained 未分类 (investment accounts)

Investment-type accounts (brokerage, retirement 401k/IRA, crypto) split their value into three parts:
**value 市值 = principal 本金 + returns 收益 + unexplained 未分类**.

* **Principal** = net contributions:
  * opening balance (minus the part marked as prior returns)
  * transfers in − transfers out (a transfer between two investment accounts moves principal from one to the
    other)
  * income deposited (payroll contributions; an employer match counts as a contribution and is labelled
    *Employer match 雇主匹配*)
  * refunds − withdrawals/expenses
  * adjustments with reason *Opening balance* or *Unrecorded income/spending*
* **Returns** = explicit returns only:
  * prior returns in the opening balance
  * adjustments with reason *Investment gain/loss*, *Interest* or *Fees* (negative)
  * dividends − investment fees
  * income in an interest/dividend category
  * trade gains on holdings (realized + unrealized = market value − buys + sells)
* **Unexplained 未分类** has two parts:
  * `unclassified`: balance adjustments with reason *Correction/other* or no reason, e.g. imported 随手记 余额变更.
  * `reconciliation_diff`: whatever else the ledger doesn't explain (reconciliation checkpoint jumps).

  `unexplained` = `unclassified` + `reconciliation_diff`. Unclassified adjustments are not counted as returns.
  Use **🏷 Reclassify** (the button on the account's Unexplained card opens it for that account) to give them a
  reason. This is computation only: nothing is changed in the data.
* **Return %** = returns / principal, and the period return (Modified Dietz) uses explicit returns only.
* **Overrides:** the transaction form has *Investment: counts as 计入* (Auto / Principal / Returns / Unexplained;
  the API also accepts `other` as an older name). The account form has *Prior returns in opening balance*
  (default 0, so the whole opening balance is principal).

**Account page:**
* Cards: Current value, Principal, Returns (amount and % of principal) and Unexplained (with a Reclassify button).
* Period figures for the selected filter: principal start → end, contributions/withdrawals, returns, and the
  Modified Dietz return.
* A chart of market value, cumulative principal, cumulative returns and unexplained. It follows the period filter
  (1M/3M/6M/YTD/1Y/3Y/All/custom), with automatic daily/weekly/monthly granularity or a manual choice.
* Each row has a 本金/收益/未分类/交易 badge, plus running Principal, Returns and Unexplained columns next to the
  running cash balance. On mobile these columns are hidden by default, with a toggle button.

**Accounts page:** investment accounts and groups show
`Principal $X · Returns +$Y (+Z%) · Unexplained $U` (Unexplained is omitted when 0; toggle: *principal &
returns*).

API:
```bash
curl -s -H "$H" "$B/api/accounts"                    # accounts[].invest {value, principal, returns, returns_pct,
                                                     #   unexplained, unclassified, reconciliation_diff, correction_count, ...}; tree[].invest for groups
curl -s -H "$H" "$B/api/accounts/7/history?period=1Y" # items[] add pr_class, pr_badge, running_principal,
                                                     #   running_returns, running_unexplained; first page adds invest {..., period, series}
curl -s -H "$H" "$B/api/accounts/7/investment-series?from=2026-01-01&to=2026-06-30&interval=week"
#   ?period=1M|3M|6M|YTD|1Y|3Y|ALL or from/to; interval=auto|day|week|month
#   -> {interval, points:[{date, principal, returns, value, unexplained}], summary:{start_principal, end_principal,
#       returns_change, start/end_unexplained, contributions, withdrawals, period_returns, return_pct (Modified Dietz)}}
curl -s -H "$H" -X PUT "$B/api/transactions/123" -H 'Content-Type: application/json' -d '{"pr_class":"returns"}'
curl -s -H "$H" -X PUT "$B/api/accounts/7" -H 'Content-Type: application/json' -d '{"opening_returns":2500}'
```


### Investments page: principal & returns 本金与收益
- The principal / returns / unexplained summary lives on **Investments**. The Accounts tree no longer shows it, though the API still returns the `invest` fields on accounts and groups.
- There is one row per investment account: value, principal 本金投入, returns 投资收益 (amount and % of principal), unexplained 未分类, and returns for the selected period (money-weighted %).
- The table has group subtotals in the base currency, totals by currency, and an overall total.
- A range filter (1M/3M/6M/YTD/1Y/3Y/All/Custom) drives a combined principal vs returns (vs value) chart. Each row opens the account detail inside Investments (`#/investments/account/<id>`): the Investments nav item stays highlighted, and the back button (or browser back) returns to the list with the same range and scroll position. Accounts page links still open `#/account/<id>`.
- The figures are as of the end of the range. When the range ends today they equal the account page header.
- `GET /api/investments/principal-returns?from=&to=&interval=auto|day|week|month&include_archived=` returns `accounts`, `groups`, `by_currency`, `total`, and `series`.
- The **Investment returns** report uses the same logic:
  - contributions/withdrawals are principal flows only;
  - gain = returns;
  - unclassified adjustments and reconciliation gaps appear in a separate `unexplained` column.
- The old ledger-flow figures are kept as `flow_contributions`, `flow_withdrawals`, `flow_net_contributions`, `flow_gain`, `flow_md_return`, and `flow_simple_return`.

## Reimbursements 报销

- Flag any **expense** as *可报销 reimbursable* (quick/detail/edit forms, or the 报销中心 page). Optional: payer 报销方, expected amount (defaults to the expense amount), note.
- Record money coming back as kind **`reimbursement` 报销到账**: it is an inflow to the chosen account (balances move normally) and is linked to one or more reimbursable expenses. Partial settlement is allowed; amounts are kept in each expense's own currency (multi-currency supported).
- **Stats:** reimbursable expenses are excluded from home, dashboard, category reports and budgets; reimbursement receipts are not counted as income.
- **Write off 核销:** gives up on the rest. The unreimbursed remainder counts as a normal expense again.
- Deleting a receipt or an expense removes its links (DB trigger), so the expense goes back to pending.
- Status: 待报销 pending / 部分报销 partial / 已报销 settled / written off.
- Quick add (including Apple Watch / Shortcuts): start with `报销 …` or add `#报销`, `#reimb`, or `#报销:Payer`, e.g. `报销 打车 32 支付宝` or `hotel 389 chase #报销:Acme`.
- The pre-existing *income* category "Reimbursement 报销" is unchanged. Old transactions in it stay income; use the new kind for new receipts.

API:
- `GET /api/reimbursements?status=open|pending|partial|settled|written_off|all&from=&to=&payer=&account_id=` returns items plus outstanding totals by currency and by payer.
- `POST /api/reimbursements/receipts` takes `{account_id, date, amount?, payer?, note?, links:[{expense_id, amount?}]}`.
- `GET /api/reimbursements/receipts` and `GET /api/reimbursements/receipts/{id}`.
- `POST /api/reimbursements/mark` takes `{ids, reimbursable, payer?, expected?, note?}`.
- `POST /api/reimbursements/{id}/write-off` takes `{undo?}`.

## Email rules: Balance adjust 调整余额 (statement balance)
- A parsing rule with **Kind = Balance adjust** (`kind: "balance"`) treats the extracted amount as the account's **new balance** (statement value), not a transaction amount.
- **Account:** matched by the last-4 regex (e.g. `Account: XXXXX1516` → 1516) against the account's last 4 digits.
- **What gets recorded:** the difference, as a balance adjustment using the same logic as Set balance, dated the email date.
  - The reason comes from the rule's `adjust_reason`: Gain/loss, Interest, Fees, Unrecorded spending/income, Correction/other, or Opening. Blank means the account default (gain/loss for investment accounts).
  - The reason flows into principal/returns as usual. For example, gain/loss counts as returns, and correction counts as unexplained.
- **Nothing recorded:**
  - When the difference is 0, nothing is recorded and the result is logged as `unchanged`.
  - An unknown account, or no amount found, is `skipped` and logged.
  - Duplicate emails (same Message-ID) are ignored.
- **Review mode:**
  - Without auto-post, or below the review threshold, the adjustment goes to the review inbox.
  - An email dated on or before the account's latest reconciliation always goes to review.
- **Tester:** the rule tester shows the adjustment it would record (`balance_preview`) without writing anything.
- **Template:** "From template" in Settings → Email Monitoring offers *Statement balance (Total Account Value)*. Templates are never inserted automatically, and they are saved disabled.
  - `amount_regex`: `Total Account Value\s*\$?([\d,]+\.\d{2})`
  - `last4_regex`: `Account:\s*X+(\d{4})`
  - `adjust_reason`: gain/loss
- **API:** `GET /api/email-rules/templates`. The `adjust_reason` field is on `/api/email-rules`.
