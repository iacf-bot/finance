"""Take UI screenshots with Playwright (uses the API key header, so no password is needed)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8088"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
KEY = (Path(__file__).resolve().parent.parent / "data" / "API_KEY.txt").read_text().strip()
OUT.mkdir(exist_ok=True)

PAGES = [("01_dashboard", "#/dashboard"), ("02_accounts", "#/accounts"), ("03_transactions", "#/transactions"),
         ("04_quick_add_review", "#/review"), ("05_investments", "#/investments"), ("06_budgets", "#/budgets"),
         ("07_reports", "#/reports"), ("08_settings_email_monitoring", "#/settings/email"), ("09_settings_import", "#/settings/import"),
         ("10_settings_backup", "#/settings/backup")]

with sync_playwright() as p:
    b = p.chromium.launch()
    # first-run setup page (no auth)
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(BASE + "/setup"); pg.wait_for_timeout(500)
    pg.screenshot(path=str(OUT / "00_first_run_setup.png"))
    ctx = b.new_context(viewport={"width": 1440, "height": 1000}, extra_http_headers={"X-API-Key": KEY})
    pg = ctx.new_page()
    for name, h in PAGES:
        pg.goto(BASE + "/" + h); pg.wait_for_timeout(1500)
        if name == "04_quick_add_review":
            pg.fill("#qa2i", "[DEMO] coffee 4.5 sapphire yesterday #work"); pg.wait_for_timeout(900)
        pg.screenshot(path=str(OUT / f"{name}.png"), full_page=name not in ("03_transactions",))
    # Suishouji import preview (sample file from tests/fixtures; preview only, nothing is committed)
    pg.goto(BASE + "/#/settings/import"); pg.wait_for_timeout(1200)
    pg.set_input_files("#suiFile", str(OUT.parent / "tests" / "fixtures" / "ezbookkeeping_feidee_web_sample.xls"))
    pg.click("#suiGo"); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "09b_suishouji_import_preview.png"), full_page=True)
    # transaction editor modal
    pg.goto(BASE + "/#/transactions"); pg.wait_for_timeout(1200)
    pg.click("#fab"); pg.wait_for_timeout(600)
    pg.screenshot(path=str(OUT / "11_new_transaction_modal.png"))
    # mobile
    m = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                      extra_http_headers={"X-API-Key": KEY})
    mp = m.new_page()
    for name, h in (("12_mobile_dashboard", "#/dashboard"), ("13_mobile_review", "#/review"), ("14_mobile_accounts", "#/accounts")):
        mp.goto(BASE + "/" + h); mp.wait_for_timeout(1500)
        mp.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
    b.close()
print("saved to", OUT)
