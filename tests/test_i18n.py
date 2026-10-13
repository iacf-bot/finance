"""i18n: API reply 'message' language (?lang= > Accept-Language > English), ui_lang pref, JS locale logic. FAKE data only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.ingest import resolve_lang

ROOT = Path(__file__).resolve().parent.parent


def test_resolve_lang_order_and_fallback():
    assert resolve_lang() == "en" and resolve_lang("auto", None) == "en"
    assert resolve_lang(None, "zh-CN,zh;q=0.9,en;q=0.8") == "zh"
    assert resolve_lang(None, "en-US,en;q=0.9,zh-CN;q=0.8") == "en"
    assert resolve_lang(None, "fr-FR,fr;q=0.9,zh-TW;q=0.5") == "zh"  # first supported by q-value
    assert resolve_lang(None, "fr-FR,de;q=0.8") == "en"  # unsupported -> English
    assert resolve_lang(None, "zh;q=0, en") == "en"
    assert resolve_lang("en", "zh-CN") == "en" and resolve_lang("zh-TW", "en") == "zh"  # param wins
    assert resolve_lang("cn") == "zh" and resolve_lang("xx") == "en"


def _setup(client):
    client.post("/api/accounts", json={"name": "Cash", "type": "cash", "currency": "USD", "opening_balance": 50, "aliases": "cash, 现金"})


def test_quick_add_message_defaults_to_english_even_for_chinese_text(client):
    _setup(client)
    r = client.post("/api/quick-add", json={"text": "午饭 12 现金"}).json()
    assert r["message"].startswith("Saved: ")
    r = client.post("/api/quick-add", json={"text": "coffee 3 cash"}, headers={"Accept-Language": "zh-CN,zh;q=0.9"}).json()
    assert r["message"].startswith("已记: ")
    r = client.post("/api/quick-add?lang=en", json={"text": "午饭 5 现金"}, headers={"Accept-Language": "zh-CN"}).json()
    assert r["message"].startswith("Saved: ")
    r = client.post("/api/quick-add", json={"text": "taxi 9 cash #项目:NoSuchTrip"}).json()
    assert "not found" in r["message"] and "未找到" not in r["message"]
    r = client.post("/api/quick-add?lang=zh", json={"text": "taxi 9 cash #项目:NoSuchTrip"}).json()
    assert "未找到项目" in r["message"]


def test_reminders_notify_follows_accept_language(client):
    assert client.get("/api/reminders/notify").json()["title"] != "没有待办提醒"  # default English
    assert client.get("/api/reminders/notify", headers={"Accept-Language": "zh-CN"}).json()["title"] == "没有待办提醒"
    assert client.get("/api/reminders/notify?lang=en", headers={"Accept-Language": "zh-CN"}).json()["title"] != "没有待办提醒"


def test_ui_lang_pref_validated(client):
    assert client.put("/api/prefs", json={"ui_lang": "zh-TW"}).status_code == 200
    assert client.get("/api/prefs").json()["ui_lang"] == "zh-TW"
    assert client.put("/api/prefs", json={"ui_lang": "<script>"}).status_code == 400
    assert client.put("/api/prefs", json={"ui_lang": ""}).status_code == 200


def test_locales_complete_and_consistent():
    zh = json.loads((ROOT / "locales/zh-CN.json").read_text("utf-8"))
    tw = json.loads((ROOT / "locales/zh-TW.json").read_text("utf-8"))
    assert zh["_name"] and tw["_name"] and len(zh) > 400
    assert set(zh) <= set(tw)  # zh-TW generated from zh-CN covers every key
    for k, v in zh.items():
        assert set(__import__("re").findall(r"\{\w+\}", k)) == set(__import__("re").findall(r"\{\w+\}", v)), k


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_locale_logic():
    out = subprocess.run(["node", str(ROOT / "tests/js/i18n_test.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
