import termios
from unittest.mock import patch

import pytest

import sms_modem_at
from sms_modem_at import ServiceStatus, parse_sysinfo

# Captured while SMS were silently not arriving.
_PS_ONLY = """AT^SYSINFO
^SYSINFO:2,2,1,3,1,0,3
OK
"""

# Captured after AT+COPS=0 restored circuit-switched service.
_HEALTHY = """AT^SYSINFO
^SYSINFO:2,3,1,3,1,0,3
OK
"""


def test_parses_all_sysinfo_fields():
    s = parse_sysinfo(_HEALTHY)
    assert s.srv_status == 2
    assert s.srv_domain == 3
    assert s.roam_status == 1
    assert s.sys_mode == 3
    assert s.sim_state == 1


def test_ps_only_is_not_sms_capable():
    """srv_domain 2 = packet-switched only. Data works, SMS cannot arrive."""
    assert parse_sysinfo(_PS_ONLY).sms_capable is False


def test_cs_and_ps_is_sms_capable():
    assert parse_sysinfo(_HEALTHY).sms_capable is True


def test_cs_only_is_sms_capable():
    """srv_domain 1 = CS only. Unusual, but SMS work."""
    assert parse_sysinfo("^SYSINFO:2,1,1,3,1,0,3\n").sms_capable is True


def test_searching_is_not_sms_capable():
    """srv_domain 4 = registering. Transient during recovery."""
    assert parse_sysinfo("^SYSINFO:1,4,0,0,1,0,0\n").sms_capable is False


def test_no_service_is_not_sms_capable():
    assert parse_sysinfo("^SYSINFO:0,0,0,0,1,0,0\n").sms_capable is False


def test_unparseable_output_yields_empty_status_not_an_exception():
    """A garbled reply must never crash the checker."""
    s = parse_sysinfo("GARBAGE\r\nOK\r\n")
    assert s == ServiceStatus(None, None, None, None, None)
    assert s.sms_capable is False


def test_empty_output_is_not_sms_capable():
    assert parse_sysinfo("").sms_capable is False


def test_open_port_clears_hupcl_so_closing_does_not_reset_the_modem():
    captured = {}

    def fake_tcsetattr(fd, when, attrs):
        captured["attrs"] = attrs

    # A plausible default termios list: HUPCL set in the control flags.
    default = [0, 0, termios.HUPCL | termios.CREAD | termios.CLOCAL, 0, 0, 0, [0] * 32]

    with patch("sms_modem_at.os.open", return_value=7), \
         patch("sms_modem_at.termios.tcgetattr", return_value=default), \
         patch("sms_modem_at.termios.tcsetattr", side_effect=fake_tcsetattr):
        fd = sms_modem_at.open_port("/dev/null")

    assert fd == 7
    cflag = captured["attrs"][2]
    assert not cflag & termios.HUPCL, "HUPCL still set: closing the port will reset the modem"


def test_open_port_closes_fd_when_tcgetattr_raises():
    """Finding (Minor 5): a fd opened before termios configuration must not
    leak if tcgetattr/tcsetattr raises -- e.g. the modem re-enumerating or a
    by-id symlink resolving to a non-tty."""
    closed = []

    with patch("sms_modem_at.os.open", return_value=7), \
         patch("sms_modem_at.os.close", side_effect=closed.append), \
         patch("sms_modem_at.termios.tcgetattr", side_effect=termios.error("not a tty")):
        with pytest.raises(termios.error):
            sms_modem_at.open_port("/dev/null")

    assert closed == [7]


def test_open_port_does_not_set_a_baud_rate():
    """The device rejects stty 115200 and baud is meaningless on USB serial."""
    captured = {}

    def fake_tcsetattr(fd, when, attrs):
        captured["attrs"] = attrs

    default = [0, 0, termios.HUPCL, 0, termios.B9600, termios.B9600, [0] * 32]

    with patch("sms_modem_at.os.open", return_value=7), \
         patch("sms_modem_at.termios.tcgetattr", return_value=default), \
         patch("sms_modem_at.termios.tcsetattr", side_effect=fake_tcsetattr):
        sms_modem_at.open_port("/dev/null")

    assert captured["attrs"][4] == termios.B9600
    assert captured["attrs"][5] == termios.B9600


def test_query_transport_bypasses_hardware_entirely():
    """When a transport is supplied, no real serial port is ever opened."""
    with patch("sms_modem_at.os.open") as mock_open:
        result = sms_modem_at.query(["AT^SYSINFO"], transport=lambda commands: "reply")

    mock_open.assert_not_called()
    assert result == "reply"


def test_query_transport_receives_exact_command_list():
    received = {}

    def fake_transport(commands):
        received["commands"] = commands
        return ""

    sms_modem_at.query(["AT^SYSINFO", "AT+COPS?"], transport=fake_transport)

    assert received["commands"] == ["AT^SYSINFO", "AT+COPS?"]


def test_query_concatenates_replies_from_multiple_commands_in_order():
    """The hardware path drains after each command and joins the chunks in order."""
    with patch("sms_modem_at.open_port", return_value=7), \
         patch("sms_modem_at.os.write"), \
         patch("sms_modem_at.os.close"), \
         patch("sms_modem_at.time.sleep"), \
         patch("sms_modem_at._drain", side_effect=["first reply\n", "second reply\n"]):
        result = sms_modem_at.query(["AT^SYSINFO", "AT+COPS?"], port="/dev/fake")

    assert result == "first reply\nsecond reply\n"


def test_query_uses_diag_port_by_default():
    captured = {}

    def fake_open_port(path):
        captured["port"] = path
        return 7

    with patch("sms_modem_at.open_port", side_effect=fake_open_port), \
         patch("sms_modem_at.os.write"), \
         patch("sms_modem_at.os.close"), \
         patch("sms_modem_at.time.sleep"), \
         patch("sms_modem_at._drain", return_value=""):
        sms_modem_at.query(["AT^SYSINFO"])

    assert captured["port"] == sms_modem_at.DIAG_PORT


def test_drain_propagates_non_blocking_hardware_error():
    """A real fault (e.g. ENODEV from a disconnected modem) must not be swallowed.

    Only BlockingIOError (EAGAIN/EWOULDBLOCK) means "buffer empty, stop reading".
    Any other OSError means the modem itself is gone or faulted.
    """
    with patch("sms_modem_at.os.read", side_effect=OSError(19, "No such device")):
        with pytest.raises(OSError):
            sms_modem_at._drain(7)
