import json

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
