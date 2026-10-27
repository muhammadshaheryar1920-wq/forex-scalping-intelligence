import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import main as cli
from forexintel import config
from forexintel.api import app as appmod
from tests.conftest import seed


@pytest.fixture
def client(env):
    with TestClient(appmod.app) as c:
        yield c


def test_health_and_static_dashboard(client):
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["paper_only"] is True
    page = client.get("/")
    assert page.status_code == 200 and "Forex Intelligence" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_settings_never_leak_secrets(client, env):
    config.set_settings(replace(env, oanda_token="SUPER-SECRET-TOKEN", twelvedata_key="KEY-ABC123", api_token=""))
    body = client.get("/api/v1/settings").text
    assert "SUPER-SECRET-TOKEN" not in body and "KEY-ABC123" not in body
    assert '"oanda_token":true' in body.replace(" ", "")


def test_api_token_is_enforced_when_configured(client, env):
    config.set_settings(replace(env, api_token="s3cret"))
    assert client.get("/api/v1/signals").status_code == 401
    assert client.get("/api/v1/signals", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/signals", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/v1/signals", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/api/v1/health").status_code == 200            # liveness stays open


def test_unknown_symbol_and_bad_timeframe(client):
    assert client.get("/api/v1/markets/FOOBAR/snapshot").status_code == 404
    assert client.get("/api/v1/markets/EURUSD/candles?timeframe=D1").status_code == 400


def test_empty_database_states_are_honest(client):
    r = client.get("/api/v1/markets/EURUSD/analysis").json()
    assert r["trade_action"] == "CANNOT_ASSESS" and r["blockers"] == ["INSUFFICIENT_DATA"]
    assert client.get("/api/v1/markets/EURUSD/candles").json()["candles"] == []
    assert client.get("/api/v1/signals").json() == []
    assert client.get("/api/v1/paper-trades").json()["authorized"]["n"] == 0
    assert client.get("/api/v1/calendar").json()["state"]["status"] == "CALENDAR_UNAVAILABLE"


def test_market_endpoints_with_data(client):
    seed("EURUSD", 45)
    snap = client.get("/api/v1/markets/EURUSD/snapshot").json()
    assert snap["last_bar"]["spread_is_real"] is True and snap["quality"]["bars"] > 2700
    c = client.get("/api/v1/markets/EURUSD/candles?timeframe=M15&limit=200").json()
    assert len(c["candles"]) == 200 and c["overlays"] is not None
    assert set(c["candles"][0]) == {"time", "open", "high", "low", "close"}
    a = client.post("/api/v1/signals/analyze", json={"symbol": "EURUSD"}).json()["analysis"]
    assert a["directional_forecast"]["bias"] in ("UP", "DOWN", "NEUTRAL") and "blockers" in a
    sigs = client.get("/api/v1/signals?symbol=EURUSD").json()
    assert len(sigs) == 1 and sigs[0]["symbol"] == "EURUSD"
    again = client.post("/api/v1/signals/analyze", json={"symbol": "EURUSD"}).json()["analysis"]
    assert again["signal_id"] == a["signal_id"] and len(client.get("/api/v1/signals").json()) == 1


def test_job_lifecycle_has_real_ids_and_logs(client):
    seed("EURUSD", 60)
    jid = client.post("/api/v1/jobs/backtest", json={"symbol": "EURUSD", "walk_forward": False}).json()["job_id"]
    assert jid.startswith("backtest-")
    for _ in range(120):
        j = client.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] in ("DONE", "FAILED"):
            break
        time.sleep(1)
    assert j["status"] == "DONE", j
    assert "started backtest" in j["log"] and "finished OK" in j["log"]
    bt = client.get("/api/v1/backtests?symbol=EURUSD").json()
    assert bt["results"]["status"] == "DONE" and bt["results"]["pooled_rules_only"]["n"] >= 0
    assert any(x["id"] == jid for x in client.get("/api/v1/jobs").json())
    assert client.get("/api/v1/jobs/nope").status_code == 404
    assert client.get("/api/v1/strategy-stats").status_code == 200


def test_model_endpoints_require_known_version(client):
    assert client.post("/api/v1/models/does-not-exist/approve").status_code == 400
    assert client.get("/api/v1/models").json() == []


def test_sse_stream_sends_snapshot_then_heartbeat(client, monkeypatch):
    monkeypatch.setattr(appmod, "STREAM_INTERVAL", 0.05)
    with client.stream("GET", "/api/v1/stream") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        seen = []
        for line in r.iter_lines():
            seen.append(line)
            if any(l.startswith("event: snapshot") for l in seen) and any(l.startswith(": heartbeat") for l in seen):
                break
            if len(seen) > 50:
                break
    assert any(l.startswith("id: ") for l in seen) and any("snapshot" in l for l in seen)


# ---------------- CLI ----------------
def test_cli_parser_exposes_documented_commands():
    p = cli.build_parser()
    for argv in (["doctor"], ["sync", "--symbol", "XAUUSD", "--timeframes", "M5,M15,H1"], ["sync", "--all", "--catch-up"],
                 ["train", "--symbol", "XAUUSD", "--strategy", "all"], ["backtest", "--symbol", "XAUUSD", "--walk-forward"],
                 ["signal", "--symbol", "XAUUSD", "--auto-sync"], ["signal", "--all", "--auto-sync"],
                 ["monitor", "--all", "--on-candle-close"], ["model-status", "--all"], ["serve", "--host", "127.0.0.1", "--port", "8000"],
                 ["fetch", "--symbol", "XAUUSD"], ["dashboard"], ["model-approve", "v1"], ["model-rollback", "--symbol", "EURUSD"]):
        assert p.parse_args(argv).fn is not None
    with pytest.raises(SystemExit):
        p.parse_args(["nonsense"])


def test_cli_signal_exit_codes_and_output(env, capsys):
    assert cli.main(["signal", "--symbol", "EURUSD", "--no-persist"]) == 2          # no data: cannot assess
    assert "CANNOT ASSESS" in capsys.readouterr().out
    seed("EURUSD", 45)
    rc = cli.main(["signal", "--symbol", "EURUSD", "--no-persist"])
    out = capsys.readouterr().out
    assert rc in (0, 2) and "DIRECTIONAL FORECAST" in out and "TRADE DECISION" in out


def test_cli_rejects_bad_symbol_with_error_code(capsys):
    assert cli.main(["signal", "--symbol", "NOPE"]) == 1
    assert "Unsupported symbol" in capsys.readouterr().err


def test_cli_doctor_runs_offline(env, capsys):
    config.set_settings(replace(env, history_provider="demo", live_provider="none"))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "doctor:" in out and "bid/ask" in out


def test_csrf_and_host_guards(client):
    assert client.post("/api/v1/monitor/pause", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/v1/monitor/pause", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/v1/health", headers={"Host": "attacker.example"}).status_code == 400
