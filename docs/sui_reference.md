# Suishouji (随手记 / sui.com) reference notes

Short research summary (Oct 2026) used to shape this app's data model and UI.
Sources: sui.com app-store listing (随手记Pro, App Store CN id373454750), Baidu 经验 / mmet.cn
how-tos on 分类管理, zhuzhuwenda "账户设置" guide, woshipm.com product analysis
("随手记，随时随地记一笔"), liqucn "随手记账本怎么用", Douyin "怎么设置二级分类".

## Core concepts

| Suishouji | Meaning | This app |
|---|---|---|
| 账本 (ledger / book) | Separate books per scenario: 个人, 家庭, 生意, 旅游, 装修, 人情, 宝宝… each with its own templates, categories and members; many-user collaboration with roles. | `ledgers` table; one "Personal 个人账本" ledger by default; every transaction belongs to a ledger. Multi-user roles not in MVP (single user). |
| 账户 + 账户分组 | Accounts are grouped: 现金账户, 金融账户 (储蓄卡/借记卡/存折, with 子账户 like 活期/定期), 虚拟账户 (支付宝, 微信, 饭卡, 公交卡), 信用卡账户（负债）, 投资账户 (基金/股票/理财), 负债 (应付款项/借入), 债权 (应收款项/公司报销/借出). Credit card setup asks 发卡行 + 卡号后四位; name defaults to bank + last 4. | `account_groups` + `accounts` with `type`, `currency`, `opening_balance`, `credit_limit`, `last4`, `institution`, `aliases`, and `is_liability` from the type. Net worth = assets − liabilities. |
| 记一笔: 支出 / 收入 / 转账 | Each record: 金额, 分类 (two-level), 账户, 成员 (who: 本人/老公/老婆/家庭公用), 商家 (where: 超市/餐馆…), 项目 (装修/回老家/购物节…), 备注, 时间. 转账 moves money between accounts (e.g. 支付宝提现到银行卡, 银行卡取现). Also 退款, 报销, 借贷, 余额调整. | `transactions.kind` ∈ expense / income / transfer / refund / adjustment (+ investment trades). Fields: account, to_account, category (sub-category id), merchant, member, project, tags, note, source, attachment. |
| 两级分类 | 支出分类 and 收入分类, each 一级 → 二级 (e.g. 食品酒水 → 早午晚餐 / 烟酒茶 / 水果零食). Editable, sortable; delete requires no remaining records. | `categories` with `parent_id` and `kind`. Seeded bilingual defaults modeled on Suishouji's (食品酒水, 衣服饰品, 居家物业, 行车交通, 交流通讯, 休闲娱乐, 学习进修, 人情往来, 医疗保健, 金融保险, 其他杂项; income 职业收入 / 其他收入). |
| 成员 / 商家 / 项目 / 标签 | Extra dimensions for slicing reports; managed under 设置 → 分类标签. | `members`, `merchants`, `projects` tables + free-form `tags` (comma list). |
| 预算中心 | Monthly budgets per category (total and per-category), can inherit previous period. | `budgets` (category, month, amount); "copy last month" helper; progress bars. |
| 报表 / 超级流水 / 流水日历 | 首页即报表: cards for this month's income/expense/balance; charts by 时间/分类/账户/项目/成员/商家; "super flow" filtered list. | Dashboard (month cards, category pie, 12-month trend, net-worth history, balances, recent) + Reports page (group by category/member/merchant/project/account over a date range) + filterable transaction list. |
| 投资账户 | Stocks/funds tracked as accounts; investment module (理财) in-app. | Brokerage/retirement/crypto accounts with trades (buy/sell/dividend/fee), holdings, cost basis, quotes via yfinance + manual price. |
| AI / quick capture | Voice 记账, screenshot/receipt OCR batch entry, iOS shortcuts auto-record. | Rule-based `/api/quick-add` (EN/中文 free text) + email-alert ingestion + optional Telegram bot, with a "To review" inbox for low confidence. |
| Safety | Cloud sync, operation logs, recover deleted records. | Local SQLite, manual + scheduled backups (keep 30), optional rclone → Google Drive. |

## Export formats (for the Settings → Import → Suishouji importer)

Nothing here is guessed from memory; it comes from the sources below. Where a detail could not be
confirmed, the importer is tolerant (header synonyms, per-column override in the preview, unknown
types reported rather than dropped).

### Sources
1. **ezBookkeeping** (MIT, github.com/mayswind/ezbookkeeping): Suishouji importers in
   `pkg/converters/feidee/` (`feidee_mymoney_xls_file_importer.go`, `feidee_mymoney_elecloud_xlsx_file_importer.go`,
   `feidee_mymoney_csv_file_importer.go`, and the `*_test.go` files) plus testdata under `testdata/`. Two of its sample
   `.xls` files are vendored in `tests/fixtures/` (license in `tests/fixtures/EZBOOKKEEPING_LICENSE`) and used in tests.
2. ezBookkeeping docs, “导出 & 导入”: https://ezbookkeeping.mayswind.net/zh_Hans/export_and_import/
   (step-by-step export paths for 随手记 App, Web版 and 神象云账本).
3. CSDN posts showing the Suishouji import/export template columns:
   https://blog.csdn.net/luchengtao11/article/details/127340578 and
   https://blog.csdn.net/weixin_39585914/article/details/127784712

### How to export from Suishouji (source 2)
| Variant | Path | File |
|---|---|---|
| Web 版 | 随手记网页版 → 新功能 → 导入导出 → 数据导出 → 数据来源 “随手记 web版” | `.xls` |
| 神象云账本 (app or web) | 账本 设置 → 数据导出 → choose range → 导出 | `.xlsx` |
| App CSV | 账本 设置 → 高级功能 → 备份与同步 → 备份与恢复 → 本地备份与恢复 → 导出Excel文件(CSV) | `.csv` |

Known caveats (source 2): the Web 版 export has only one amount for cross-currency transfers; the iPhone
app's CSV does not escape newlines inside 备注 (Android export is safer).

### Layouts (source 1)
* **Web `.xls`** — one sheet per type, header row first. 支出 sheet: `交易类型, 日期, 一级分类, 二级分类, 支出账户, 金额, 成员, 商家, 项目, 备注`;
  收入 uses `收入账户`; 转账 uses `转出账户, 转入账户`; 余额变更 / 负债变更 / 债权变更 use `账户1, 账户2`.
  Older exports use `分类, 子分类, 账户1, 账户2` everywhere.
* **神象云账本 `.xlsx`** — single sheet: `交易类型, 日期, 分类, 子分类, 账户1, 账户2, 账户币种, 金额, 成员, 商家, 项目分类, 项目, 记账人, 备注`.
  Extra 交易类型 values: 借入, 借出, 收债, 还债, 代付, 报销, 退款.
* **App CSV** — first line `随手记导出文件(headers:v5;…)`, then `交易类型, 日期, 类别, 子类别, 账户, 账户币种, 金额, 备注, 关联Id, 成员, 项目, 商家`.
  Transfers are two rows (转出 / 转入) linked by `关联Id`, in either order. Encoding may be UTF-8 (BOM) or GB18030.
  Observed in a real iPhone export (headers:v5, Oct 2026): column order `交易类型, 日期, 类别, 子类别, 项目, 账户, 账户币种, 金额,
  成员, 商家, 备注, 关联Id` (the importer matches by name, so order doesn't matter). Times are `YYYY-MM-DD HH:MM:SS`, and notes can
  contain quoted newlines. Cross-currency transfers carry each side's own amount and currency. 负债变更 is used for credit cards
  and other liabilities, 余额变更 only for asset accounts, and zero-amount rows occur. Initial account balances are **not**
  in the export.

### Type / sign mapping used here
| Suishouji 交易类型 | This app |
|---|---|
| 支出 | expense (negative amount → refund) |
| 收入 | income (negative amount → expense, noted) |
| 转账 / 转出+转入 | transfer (account1 → account2) |
| 余额变更 | adjustment; positive amount raises the balance (source 1) |
| 负债变更 / 债务变更 | adjustment on a liability account (auto-created as `payable`); positive = more debt |
| 债权变更 | adjustment on a receivable account (auto-created as `receivable`); positive = more owed to you |
| 借入 / 借出 / 收债 / 还债 / 代付 / 报销 | transfers between the money account and a payable/receivable account. **Direction is best effort**: not documented beyond the type names; review the preview. |
| 退款 | refund |

Duplicate detection: SHA-1 of date(minute) + |amount| + account + note (stored as `external_id = sui:<hash>#n`), so
re-importing the same file is a no-op when “Skip duplicates” is on.
