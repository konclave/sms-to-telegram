import termios
from unittest.mock import patch

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
