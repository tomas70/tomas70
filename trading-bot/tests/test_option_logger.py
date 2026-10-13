"""Tests for analysis/option_logger: selection logic, idempotent logging, derived metrics, settlement."""
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.option_logger as ol

NOW = datetime(2026, 10, 13, 0, 10, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
DAY = 86_400_000


def _inst(days, strike, kind):
    e = NOW_MS + int(days * DAY)
    return {"instrument_name": f"X-{days}-{strike}-{kind[0]}", "expiration_timestamp": e,
            "strike": strike, "option_type": kind}


def _chain():
    out = []
    for d in (7, 24, 38, 90):
        for k in (80000, 84000, 88000):
            out += [_inst(d, k, "call"), _inst(d, k, "put")]
    out.append(_inst(38, 90000, "call"))                      # strike with only one leg
    return out


def test_pick_expiry_closest_to_30_days_and_skips_short():
    inst = _chain()
    e = ol.pick_expiry(inst, NOW_MS)
    assert abs((e - NOW_MS) / DAY - 24) < 1e-6                # |24-30| < |38-30|
    assert ol.pick_expiry([_inst(5, 80000, "call")], NOW_MS) is None


def test_pick_atm_needs_both_legs_and_nearest_forward():
    inst = _chain()
    e = ol.pick_expiry(inst, NOW_MS)
    call, put, k = ol.pick_atm(inst, e, 84900.0)
    assert k == 84000.0 and call.endswith("84000-c") and put.endswith("84000-p")
    e38 = [i for i in inst if abs((i["expiration_timestamp"] - NOW_MS) / DAY - 38) < 1e-6][0]["expiration_timestamp"]
    assert ol.pick_atm(inst, e38, 91000.0)[2] == 88000.0      # 90000 has no put -> skipped


def test_atm_call_pct_matches_closed_form():
    assert abs(ol.atm_call_pct(0.6, 30) - 0.3989 * 0.6 * math.sqrt(30 / 365)) < 5e-4
    assert ol.atm_call_pct(0.0, 30) == 0.0


def _row(**kw):
    base = dict(date="2026-09-01", currency="BTC", expiry="2026-10-01", dte="30", strike="80000", spot="80000",
                call_bid="0.04", call_ask="0.044", call_mark="0.042", put_bid="0.04", put_ask="0.044",
                put_mark="0.042", call_mark_iv="55", put_mark_iv="57", dvol="60", funding_8h="0.0001")
    base.update(kw)
    return base


def test_derive_compares_with_proxy():
    d = ol.derive(_row())
    assert abs(d["mid"] - 0.084) < 1e-12
    assert abs(d["proxy"] - 2 * ol.atm_call_pct(0.6, 30)) < 1e-12
    assert abs(d["ratio"] - d["mid"] / d["proxy"]) < 1e-12
    assert abs(d["spread_pct"] - 0.008 / 0.084) < 1e-12
    assert abs(d["dvol_gap"] - 4.0) < 1e-9 and abs(d["funding_ann"] - 0.0001 * 3 * 365) < 1e-12
    assert ol.derive(_row(call_mark="")) is None


def test_settle_uses_bid_and_delivery_price(monkeypatch):
    monkeypatch.setattr(ol, "_delivery", lambda cur, date: 84000.0)
    s = ol.settle(_row(), "2026-10-13")
    assert abs(s["real"] - (0.08 - 4000 / 80000)) < 1e-12
    assert s["proxy"] is not None
    assert ol.settle(_row(expiry="2026-10-20"), "2026-10-13") is None        # not expired yet
    assert ol.settle(_row(call_bid=""), "2026-10-13") is None                 # no bid -> cannot sell


def test_update_is_idempotent_per_currency_and_day(tmp_path, monkeypatch):
    calls = []

    def fake_snapshot(cur, now=None):
        calls.append(cur)
        return {**_row(currency=cur, date=now.strftime("%Y-%m-%d")), "ts": "t", "fwd": 1, "dvol": 60}
    monkeypatch.setattr(ol, "snapshot", fake_snapshot)
    f = tmp_path / "q.csv"
    assert len(ol.update(NOW, f)) == 2
    assert ol.update(NOW, f) == [] and calls == ["BTC", "ETH"]
    assert len(ol.read_rows(f)) == 2
    assert len(ol.update(datetime(2026, 10, 14, 0, 5, tzinfo=timezone.utc), f)) == 2


def test_update_survives_api_failure(tmp_path, monkeypatch):
    def boom(cur, now=None):
        raise RuntimeError("down")
    monkeypatch.setattr(ol, "snapshot", boom)
    assert ol.update(NOW, tmp_path / "q.csv") == []


def test_report_empty_and_filled(tmp_path, monkeypatch):
    f = tmp_path / "q.csv"
    assert "Dar nėra" in ol.report(f, "2026-10-13")
    monkeypatch.setattr(ol, "_delivery", lambda cur, date: 84000.0)
    ol._append({**_row(), "ts": "t", "fwd": 1}, f)
    txt = ol.report(f, "2026-10-13")
    assert "BTC" in txt and "mid / proxy" in txt and "išsipildė 1" in txt
