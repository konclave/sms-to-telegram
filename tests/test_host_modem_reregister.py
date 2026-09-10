import sms_modem_reregister


def test_main_reports_missing_modem_instead_of_a_traceback(tmp_path, capsys):
    """Finding (Minor 6): anyone reaching for this handle already has a
    misbehaving modem; os.open raising FileNotFoundError must not be the
    first thing they see."""
    missing_port = str(tmp_path / "no-such-port")
    rc = sms_modem_reregister.main(["--port", missing_port])
    assert rc == 1
    out = capsys.readouterr().out
    assert missing_port in out
    assert "not present" in out


class FakeModem:
    """Answers AT commands; flips to CS+PS after `flip_after` SYSINFO polls."""

    def __init__(self, flip_after=2, cops2_errors=True):
        self.flip_after = flip_after
        self.cops2_errors = cops2_errors
        self.sysinfo_polls = 0
        self.commands = []

    def __call__(self, commands):
        self.commands.extend(commands)
        command = commands[0]
        if command == "AT+COPS=2":
            return "AT+COPS=2\nERROR\n" if self.cops2_errors else "AT+COPS=2\nOK\n"
        if command == "AT+COPS=0":
            return "AT+COPS=0\nOK\n"
        if command == "AT^SYSINFO":
            self.sysinfo_polls += 1
            if self.sysinfo_polls > self.flip_after:
                return "^SYSINFO:2,3,1,3,1,0,3\nOK\n"
            return "^SYSINFO:1,4,0,0,1,0,0\nOK\n"
        return "OK\n"


def test_reregister_succeeds_once_cs_service_returns():
    modem = FakeModem(flip_after=2)
    ok = sms_modem_reregister.reregister(
        port="/dev/null", transport=modem, sleep=lambda s: None, out=lambda *a: None
    )
    assert ok is True
    assert "AT+COPS=0" in modem.commands


def test_cops2_error_is_tolerated():
    """This firmware answers ERROR to AT+COPS=2; COPS=0 still works."""
    modem = FakeModem(flip_after=1, cops2_errors=True)
    ok = sms_modem_reregister.reregister(
        port="/dev/null", transport=modem, sleep=lambda s: None, out=lambda *a: None
    )
    assert ok is True


def test_reregister_times_out_when_cs_never_returns():
    """If the network genuinely refuses CS service, report honestly."""
    modem = FakeModem(flip_after=10_000)
    ticks = iter([0.0] + [i * 30.0 for i in range(1, 20)])
    ok = sms_modem_reregister.reregister(
        port="/dev/null",
        transport=modem,
        sleep=lambda s: None,
        now=lambda: next(ticks),
        timeout=60.0,
        out=lambda *a: None,
    )
    assert ok is False


def test_progress_is_printed_during_recovery():
    """Recovery passes through srv_domain 4 with no signal for a minute or
    more. Silence there looks like a hang and gets abandoned."""
    lines = []
    modem = FakeModem(flip_after=2)
    sms_modem_reregister.reregister(
        port="/dev/null", transport=modem, sleep=lambda s: None, out=lines.append
    )
    printed = " ".join(str(line) for line in lines)
    assert "srv_domain" in printed


def test_main_returns_zero_on_success(monkeypatch):
    monkeypatch.setattr(sms_modem_reregister, "reregister", lambda **kw: True)
    assert sms_modem_reregister.main(["--port", "/dev/null"]) == 0


def test_main_returns_one_on_timeout(monkeypatch):
    monkeypatch.setattr(sms_modem_reregister, "reregister", lambda **kw: False)
    assert sms_modem_reregister.main(["--port", "/dev/null"]) == 1
