"""A reverse proxy that breaks things on purpose.

This is a test instrument. Nothing in the discovery or replay path imports it.
"""

import http.client
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Content-length is dropped too, since a rewritten body has a different length.
SKIP_RESPONSE_HEADERS = frozenset({"transfer-encoding", "connection", "content-length"})


class Match(BaseModel):
    """Which requests a rule applies to. An empty field means "any"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path_contains: str = ""
    method: str = ""

    def selects(self, method: str, path: str) -> bool:
        if self.method and self.method.upper() != method.upper():
            return False
        return self.path_contains in path


class Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    find: str
    # Named "with" in the rules file; aliased because "with" is a Python keyword.
    replace_with: str = Field(alias="with")


class Fault(BaseModel):
    """What to do to a matching request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    delay_ms: int = 0
    status: int | None = None
    body: str = ""  # served instead of calling the application, only with `status`
    drop_session: bool = False
    replace: tuple[Replacement, ...] = ()


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    when: Match = Match()
    # A fault that always fires only proves the retry limit works. `once` lets a
    # rule fire exactly once, which is what actually proves recovery works.
    once: bool = False
    then: Fault


class Faults(BaseModel):
    """A rules file. First matching rule wins; the rest are not consulted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    upstream: str = "http://localhost:8080"
    rules: tuple[Rule, ...] = ()


def load_faults(path: Path) -> Faults:
    return Faults.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class RuleBook:
    """The rules plus which of the `once` ones have been spent."""

    def __init__(self, faults: Faults) -> None:
        self.faults = faults
        self._spent: set[int] = set()
        self._lock = threading.Lock()  # a page and its assets can load in parallel

    def pick(self, method: str, path: str) -> Rule | None:
        with self._lock:
            for index, rule in enumerate(self.faults.rules):
                if index in self._spent or not rule.when.selects(method, path):
                    continue
                if rule.once:
                    self._spent.add(index)
                return rule
        return None


def charset_of(content_type: str) -> str:
    # Read from the response instead of assumed, since the app may declare a
    # charset other than UTF-8. Latin-1 is the fallback: it decodes any bytes.
    for part in content_type.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip().lower() == "charset" and value:
            return value.strip().strip('"')
    return "latin-1"


def rewrite(body: bytes, content_type: str, replacements: tuple[Replacement, ...]) -> bytes:
    if not replacements:
        return body
    encoding = charset_of(content_type)
    text = body.decode(encoding, errors="replace")
    for item in replacements:
        text = text.replace(item.find, item.replace_with)
    return text.encode(encoding, errors="replace")


def _forwarded_headers(incoming: Message, drop_session: bool) -> dict[str, str]:
    """Incoming headers, minus the ones the upstream request must not carry."""
    return {
        name: value
        for name, value in incoming.items()
        if name.lower() not in {"accept-encoding", "host", "connection"}
        and not (drop_session and name.lower() == "cookie")
    }


def _forward(
    upstream: SplitResult, method: str, path: str, body: bytes, headers: dict[str, str]
) -> tuple[int, list[tuple[str, str]], bytes]:
    """Send one request upstream and return its status, headers, and body."""
    connection = http.client.HTTPConnection(upstream.hostname or "", upstream.port or 80)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        out = [
            (name, value)
            for name, value in response.getheaders()
            if name.lower() not in SKIP_RESPONSE_HEADERS
        ]
        return response.status, out, payload
    finally:
        connection.close()


@dataclass(frozen=True)
class Request:
    """One incoming request, as much of it as the proxy needs."""

    method: str
    path: str
    headers: Message
    body: bytes


def _request_body(headers: Message, read: Callable[[int], bytes]) -> bytes:
    length = int(headers.get("Content-Length") or 0)
    return read(length) if length else b""


def _page(rule: Rule) -> bytes:
    body = rule.then.body or f"<h1>{rule.then.status}</h1>"
    return f"<html><head><title>Error</title></head><body>{body}</body></html>".encode()


def _localize(
    headers: list[tuple[str, str]],
    payload: bytes,
    origin: str,
    own_origin: str,
    replacements: tuple[Replacement, ...],
) -> tuple[list[tuple[str, str]], bytes]:
    """Rewrite the app's own origin to the proxy's, in headers and in the body.

    An absolute URL still pointing at the app would let the browser bypass the
    proxy and quietly stop being faulted.
    """
    headers = [(name, value.replace(origin, own_origin)) for name, value in headers]
    content_type = next((value for name, value in headers if name.lower() == "content-type"), "")
    if content_type.startswith("text/html"):
        payload = rewrite(payload, content_type, replacements)
        payload = payload.replace(origin.encode("ascii"), own_origin.encode("ascii"))
    return headers, payload


@dataclass(frozen=True)
class Proxy:
    """Everything about the proxy that does not change from request to request."""

    book: RuleBook
    upstream: SplitResult
    announce: Callable[[str], None]

    @property
    def origin(self) -> str:
        return f"{self.upstream.scheme}://{self.upstream.netloc}"


def _respond(
    proxy: Proxy, request: Request, own_origin: str
) -> tuple[int, list[tuple[str, str]], bytes]:
    """Pick a rule, apply its delay, then either answer directly or forward."""
    rule = proxy.book.pick(request.method, request.path)
    if rule is not None:
        proxy.announce(f"[fault] {rule.name}  {request.method} {request.path}")
        if rule.then.delay_ms:
            time.sleep(rule.then.delay_ms / 1000)
        if rule.then.status is not None:
            return rule.then.status, [("Content-Type", "text/html")], _page(rule)

    fault = rule.then if rule else Fault()
    headers = _forwarded_headers(request.headers, fault.drop_session)
    status, out, payload = _forward(
        proxy.upstream, request.method, request.path, request.body, headers
    )
    out, payload = _localize(out, payload, proxy.origin, own_origin, fault.replace)
    return status, out, payload


def _send_response(
    handler: BaseHTTPRequestHandler, status: int, headers: list[tuple[str, str]], body: bytes
) -> None:
    handler.send_response(status)
    for name, value in headers:
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def handler_for(book: RuleBook, announce: Callable[[str], None]) -> type[BaseHTTPRequestHandler]:
    proxy = Proxy(book, urlsplit(book.faults.upstream), announce)

    class Handler(BaseHTTPRequestHandler):
        # Closes the connection after each response instead of keep-alive: slower,
        # but much harder to get subtly wrong in a tool meant to be trusted.
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:
            self._handle()

        def do_HEAD(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def _handle(self) -> None:
            request = Request(
                self.command, self.path, self.headers, _request_body(self.headers, self.rfile.read)
            )
            status, headers, payload = _respond(proxy, request, self._own_origin())
            _send_response(self, status, headers, payload)

        def _own_origin(self) -> str:
            return f"http://{self.headers.get('Host', 'localhost')}"

        def log_message(self, format: str, *args: object) -> None:
            pass  # a rule firing is announced above; nothing else is worth logging

    return Handler


def serve(faults: Faults, port: int, announce: Callable[[str], None]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(RuleBook(faults), announce))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
