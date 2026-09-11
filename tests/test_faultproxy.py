"""Fault proxy tests.

Two halves. Rule selection and body rewriting are pure functions, so they are tested
directly. Everything else only means something over a real socket, so the rest of
the file stands up a throwaway application, puts the proxy in front of it, and makes
actual requests. No browser is involved and the whole file runs in well under a
second.

The proxy is a test instrument. An instrument nobody checked is worse than no
instrument, because a run that passes through it and behaves oddly leaves you unable
to say which of the two was at fault.
"""

import http.client
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from finautomate.faultproxy import (
    Faults,
    Match,
    Replacement,
    RuleBook,
    charset_of,
    load_faults,
    rewrite,
    serve,
)

# -- pure parts -------------------------------------------------------------


def test_an_empty_match_selects_everything() -> None:
    assert Match().selects("GET", "/anything")


def test_a_match_narrows_by_method_and_path() -> None:
    only_posts = Match(path_contains="/openaccount.htm", method="POST")
    assert only_posts.selects("POST", "/parabank/openaccount.htm")
    assert not only_posts.selects("GET", "/parabank/openaccount.htm")
    assert not only_posts.selects("POST", "/parabank/overview.htm")


def test_a_once_rule_is_spent_after_one_hit() -> None:
    """The rule that makes recovery testable. A fault that fires forever only proves
    the retry limit works; one that fires once proves the retry works."""
    book = RuleBook(
        Faults.model_validate({"rules": [{"name": "once", "once": True, "then": {"status": 500}}]})
    )
    assert book.pick("GET", "/x") is not None
    assert book.pick("GET", "/x") is None


def test_the_first_matching_rule_wins() -> None:
    book = RuleBook(
        Faults.model_validate(
            {
                "rules": [
                    {"name": "specific", "when": {"path_contains": "/a"}, "then": {"status": 500}},
                    {"name": "catch all", "then": {"delay_ms": 1}},
                ]
            }
        )
    )
    first = book.pick("GET", "/a")
    assert first is not None and first.name == "specific"
    second = book.pick("GET", "/b")
    assert second is not None and second.name == "catch all"


def test_the_declared_charset_is_used_for_rewriting() -> None:
    """ParaBank serves ISO-8859-1. Decoding its pages as UTF-8 would corrupt every
    byte above 127, which is a strange bug to hunt down inside a test tool."""
    assert charset_of("text/html;charset=ISO-8859-1") == "ISO-8859-1"
    assert charset_of("text/html") == "latin-1"

    body = "caf\xe9 ParaBank".encode("ISO-8859-1")
    out = rewrite(
        body,
        "text/html;charset=ISO-8859-1",
        (Replacement.model_validate({"find": "ParaBank", "with": "Riverbend"}),),
    )
    assert out.decode("ISO-8859-1") == "caf\xe9 Riverbend"


def test_a_typo_in_a_rules_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "faults.yaml"
    path.write_text("rules:\n  - name: x\n    then:\n      delay_mss: 100\n", encoding="utf-8")
    with pytest.raises(ValueError, match="delay_mss"):
        load_faults(path)


# -- over a real socket -----------------------------------------------------


class Application(BaseHTTPRequestHandler):
    """A stand-in for ParaBank: reports whether it saw a session cookie."""

    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        seen = "with session" if self.headers.get("Cookie") else "no session"
        body = f"<html><body>Welcome to ParaBank, {seen}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html;charset=ISO-8859-1")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def application() -> Iterator[int]:
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Application)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield port
    server.shutdown()


def through_proxy(config: dict[str, object]) -> tuple[int, str]:
    port = free_port()
    server = serve(Faults.model_validate(config), port, lambda _message: None)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/parabank/overview.htm", headers={"Cookie": "JSESSIONID=abc"})
        response = connection.getresponse()
        return response.status, response.read().decode("ISO-8859-1")
    finally:
        server.shutdown()


def test_with_no_rules_the_proxy_is_invisible(application: int) -> None:
    status, body = through_proxy({"upstream": f"http://127.0.0.1:{application}"})
    assert status == 200
    assert "Welcome to ParaBank, with session" in body


def test_a_status_fault_answers_without_asking_the_application(application: int) -> None:
    status, body = through_proxy(
        {
            "upstream": f"http://127.0.0.1:{application}",
            "rules": [
                {
                    "name": "app error",
                    "then": {"status": 500, "body": "An internal error has occurred"},
                }
            ],
        }
    )
    assert status == 500
    assert "An internal error has occurred" in body
    assert "Welcome" not in body, "the application must not have been reached"


def test_dropping_the_session_makes_the_application_see_a_stranger(application: int) -> None:
    _, body = through_proxy(
        {
            "upstream": f"http://127.0.0.1:{application}",
            "rules": [{"name": "expire", "then": {"drop_session": True}}],
        }
    )
    assert "no session" in body


def test_replacing_text_rebrands_the_page(application: int) -> None:
    _, body = through_proxy(
        {
            "upstream": f"http://127.0.0.1:{application}",
            "rules": [
                {
                    "name": "tenant b",
                    "then": {"replace": [{"find": "ParaBank", "with": "Riverbend Credit Union"}]},
                }
            ],
        }
    )
    assert "Riverbend Credit Union" in body
    assert "ParaBank" not in body
