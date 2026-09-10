import json

import sms_modem_at
import sms_modem_check


def test_missing_state_file_yields_defaults(tmp_path):
    """A missing state file must never stop the check from running."""
    state = sms_modem_check.load_state(str(tmp_path / "nope.json"))
    assert state == {"consecutive_failures": 0, "alert_active": False}


def test_corrupt_state_file_yields_defaults(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert sms_modem_check.load_state(str(p)) == {
        "consecutive_failures": 0,
        "alert_active": False,
    }


def test_state_file_containing_a_json_list_yields_defaults(tmp_path):
    """Valid JSON of the wrong shape must degrade to defaults, not crash."""
    p = tmp_path / "state.json"
    p.write_text("[]")
    assert sms_modem_check.load_state(str(p)) == {
        "consecutive_failures": 0,
        "alert_active": False,
    }


def test_state_file_containing_json_null_yields_defaults(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("null")
    assert sms_modem_check.load_state(str(p)) == {
        "consecutive_failures": 0,
        "alert_active": False,
    }


def test_non_numeric_consecutive_failures_yields_default_failures(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"consecutive_failures": "abc", "alert_active": True}))
    assert sms_modem_check.load_state(str(p)) == {
        "consecutive_failures": 0,
        "alert_active": True,
    }


def test_non_boolean_alert_active_yields_default_alert_active(tmp_path):
    """A JSON string like "false" must not be truthy-coerced to True."""
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"consecutive_failures": 3, "alert_active": "false"}))
    assert sms_modem_check.load_state(str(p)) == {
        "consecutive_failures": 3,
        "alert_active": False,
    }


def test_state_round_trips(tmp_path):
    p = str(tmp_path / "state.json")
    sms_modem_check.save_state(p, {"consecutive_failures": 2, "alert_active": True})
    assert sms_modem_check.load_state(p) == {
        "consecutive_failures": 2,
        "alert_active": True,
    }


def test_save_state_creates_parent_directory(tmp_path):
    p = str(tmp_path / "sub" / "dir" / "state.json")
    sms_modem_check.save_state(p, {"consecutive_failures": 1, "alert_active": False})
    assert json.loads(open(p).read())["consecutive_failures"] == 1


def _run(capable_sequence, alert_after=2):
    """Drive evaluate() across a sequence of checks, as the timer would."""
    state = {"consecutive_failures": 0, "alert_active": False}
    alerts = []
    for capable in capable_sequence:
        alerts.extend(sms_modem_check.evaluate(state, capable, alert_after=alert_after))
    return alerts


def test_healthy_modem_is_silent():
    assert _run([True, True, True]) == []


def test_single_failure_does_not_alert():
    """One bad reading may just be a modem mid-scan."""
    assert _run([False, True]) == []


def test_two_consecutive_failures_alert_once():
    alerts = _run([False, False, False, False])
    assert len(alerts) == 1
    assert "packet-switched" in alerts[0]


def test_recovery_sends_one_alert():
    alerts = _run([False, False, True])
    assert len(alerts) == 2
    assert "packet-switched" in alerts[0]
    assert "restored" in alerts[1]


def test_no_recovery_alert_without_a_prior_alert():
    assert _run([False, True, True]) == []


def test_failure_streak_resets_on_a_good_reading():
    assert _run([False, True, False, True]) == []


def test_alert_text_names_the_remedy_with_a_runnable_path():
    """The alert is read on a phone; it must carry a real command."""
    alerts = _run([False, False])
    assert "sms_modem_reregister.py" in alerts[0]
    assert "<repo>" not in alerts[0]
    assert "cd /" in alerts[0]


def test_reads_credentials_from_env_file(tmp_path):
    p = tmp_path / "notify.env"
    p.write_text('BOT_TOKEN=123:abc\nCHAT_ID=-100999\n')
    token, chat = sms_modem_check.read_credentials(str(p))
    assert token == "123:abc"
    assert chat == "-100999"


def test_credentials_tolerate_quotes_and_blank_lines(tmp_path):
    p = tmp_path / "notify.env"
    p.write_text('\n# comment\nBOT_TOKEN="123:abc"\n\nCHAT_ID=\'-100999\'\n')
    assert sms_modem_check.read_credentials(str(p)) == ("123:abc", "-100999")


def test_missing_credentials_file_returns_none(tmp_path):
    assert sms_modem_check.read_credentials(str(tmp_path / "nope")) == (None, None)


def test_main_exits_quietly_when_the_modem_is_absent(tmp_path, capsys):
    """Modem absence is covered by the udev rule; this check is only about a
    modem that is present but not CS-registered."""
    rc = sms_modem_check.main([
        "--port", str(tmp_path / "no-such-port"),
        "--state", str(tmp_path / "state.json"),
        "--credentials", str(tmp_path / "creds"),
    ])
    assert rc == 0
    assert "not present" in capsys.readouterr().out


def test_main_alerts_after_two_ps_only_readings(tmp_path):
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    state = str(tmp_path / "state.json")
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    def fake_transport(commands):
        return "^SYSINFO:2,2,1,3,1,0,3\nOK\n"

    for _ in range(2):
        rc = sms_modem_check.main(
            ["--port", str(port), "--state", state, "--credentials", str(creds)],
            transport=fake_transport,
            sender=lambda url, data: sent.append((url, data)),
        )
        assert rc == 0

    assert len(sent) == 1
    assert b"packet-switched" in sent[0][1]


def test_main_is_silent_while_sms_service_is_healthy(tmp_path):
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    for _ in range(3):
        sms_modem_check.main(
            ["--port", str(port), "--state", str(tmp_path / "s.json"),
             "--credentials", str(creds)],
            transport=lambda commands: "^SYSINFO:2,3,1,3,1,0,3\nOK\n",
            sender=lambda url, data: sent.append((url, data)),
        )
    assert sent == []


def test_main_does_not_alert_when_sysinfo_is_unparseable(tmp_path, capsys):
    """A reply with no parseable ^SYSINFO is a checker fault, not a modem fault."""
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")
    state = tmp_path / "s.json"

    rc = sms_modem_check.main(
        ["--port", str(port), "--state", str(state), "--credentials", str(creds)],
        transport=lambda commands: "garbage\nOK\n",
        sender=lambda url, data: sent.append((url, data)),
    )

    assert rc == 0
    assert sent == []
    assert not state.exists()
    assert "query failed" in capsys.readouterr().out


def test_main_retries_alert_once_credentials_become_available(tmp_path, capsys):
    """Finding 1 fix: an alert that could not be sent for want of credentials
    must not be latched as delivered -- it must be re-attempted once
    credentials are available, and the persisted state/log must agree."""
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    state = str(tmp_path / "state.json")
    missing_creds = tmp_path / "missing-creds"
    real_creds = tmp_path / "creds"
    real_creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    def fake_transport(commands):
        return "^SYSINFO:2,2,1,3,1,0,3\nOK\n"

    # Two consecutive failures cross the alert_after=2 threshold, but
    # credentials are unavailable at the moment the alert would fire.
    for _ in range(2):
        rc = sms_modem_check.main(
            ["--port", str(port), "--state", state, "--credentials", str(missing_creds)],
            transport=fake_transport,
            sender=lambda url, data: sent.append((url, data)),
        )
        assert rc == 0

    assert sent == []
    out = capsys.readouterr().out
    assert "alert suppressed: credentials unavailable" in out
    assert "alert_active=False" in out
    persisted = json.loads(open(state).read())
    assert persisted["alert_active"] is False

    # A later run with credentials available must still send the alert.
    rc = sms_modem_check.main(
        ["--port", str(port), "--state", state, "--credentials", str(real_creds)],
        transport=fake_transport,
        sender=lambda url, data: sent.append((url, data)),
    )
    assert rc == 0
    assert len(sent) == 1
    assert b"packet-switched" in sent[0][1]
    persisted = json.loads(open(state).read())
    assert persisted["alert_active"] is True


def test_main_does_not_alert_when_the_query_fails(tmp_path, capsys):
    """A checker fault is not a modem fault. Never alert about ourselves."""
    port = tmp_path / "port"
    port.write_text("")
    sent = []
    creds = tmp_path / "creds"
    creds.write_text("BOT_TOKEN=t\nCHAT_ID=c\n")

    def boom(commands):
        raise OSError("port busy")

    for _ in range(3):
        rc = sms_modem_check.main(
            ["--port", str(port), "--state", str(tmp_path / "s.json"),
             "--credentials", str(creds)],
            transport=boom,
            sender=lambda url, data: sent.append((url, data)),
        )
        assert rc == 0
    assert sent == []
    assert "query failed" in capsys.readouterr().out
