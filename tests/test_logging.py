import logging

from webtracker.logging_setup import RedactingFilter, setup_logging

TOKEN = "1234567890:" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345678"


def test_redacts_registered_secret_and_patterns():
    f = RedactingFilter(["my-very-secret"])
    out = f.redact(
        f"token {TOKEN} key sk-abcdefghijklmnopqrstuv Bearer abcdefghijklmnop1234 "
        "url ?api_key=zzz123 and my-very-secret"
    )
    assert TOKEN not in out and "sk-abc" not in out and "zzz123" not in out and "my-very-secret" not in out
    assert "abcdefghijklmnop1234" not in out


def test_log_records_redacted(capsys):
    setup_logging("INFO", [TOKEN])
    log = logging.getLogger("t")
    log.info("calling https://api.telegram.org/bot%s/sendMessage", TOKEN)
    try:
        raise ValueError(f"bad {TOKEN}")
    except ValueError:
        log.exception("failed")
    err = capsys.readouterr().err
    assert TOKEN not in err and "[REDACTED]" in err


def test_httpx_logger_quieted():
    setup_logging("DEBUG")
    assert logging.getLogger("httpx").level == logging.WARNING
