"""A reverse proxy that breaks things on purpose.

**This is a test instrument, not part of the product.** Nothing in the discovery or
replay path imports this module. It exists because we declare three classes of
failure and the real application only produces two of them: ParaBank will happily
give us a business outcome and a hard failure, but it will not expire a session or
go slow on request, so the recoverable class was never actually run.

The proxy sits between the browser and the application. The driver points at it by
changing one line of tenant config - `base_url` - which is the same knob a second
institution would turn. So pointing at the proxy needs no code change at all, and
the system under test does not know it is being tested.

The alternative was intercepting requests inside Playwright with `page.route`. That
would have put fault-injection hooks in the production browser driver, and a driver
that can lie to itself is worth less than one that cannot.

Four faults, which between them cover every row of the error table:

    delay_ms       hold the response back           -> transient slowness
    status + body  answer without asking the app    -> application error
    drop_session   strip the cookie on the way up   -> session expiry, on demand
    replace        rewrite text in the HTML         -> a differently-worded tenant

`once: true` spends a rule after its first hit. That is the one that matters for
recovery: a fault that fires forever only ever proves the retry limit works.
"""

import http.client
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Hop-by-hop headers belong to one connection and must not be forwarded. Content
# length is dropped too because a rewritten body has a different one.
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
    replace_with: str = Field(alias="with")
    """Named `with` in the rules file, because that is what it reads like. `with` is
    a Python keyword, hence the alias."""


class Fault(BaseModel):
    """What to do to a matching request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    delay_ms: int = 0
    status: int | None = None
    body: str = ""
    """Served instead of calling the application. Only used when `status` is set."""
    drop_session: bool = False
    replace: tuple[Replacement, ...] = ()


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    when: Match = Match()
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
    """The rules plus which of the `once` ones have been spent.

    Separate from `Faults` because `Faults` is the file and this is the run. Locked
    because the browser fetches a page and its stylesheets in parallel, so two
    threads really do race for the same one-shot rule.
    """

    def __init__(self, faults: Faults) -> None:
        self.faults = faults
        self._spent: set[int] = set()
        self._lock = threading.Lock()

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
    """The encoding the application declared. ParaBank says ISO-8859-1, not UTF-8,
    and decoding its pages as UTF-8 would corrupt them. Latin-1 is the fallback
    because it decodes any byte sequence, so a rewrite can never fail outright."""
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


def handler_for(book: RuleBook, announce: Callable[[str], None]) -> type[BaseHTTPRequestHandler]:
    upstream = urlsplit(book.faults.upstream)
    origin = f"{upstream.scheme}://{upstream.netloc}"

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 closes the connection after each response. Slower than keep-alive
        # and much harder to get subtly wrong, which is the right trade for a tool
        # whose whole job is to be trusted while everything else misbehaves.
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:
            self._handle()

        def do_HEAD(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def _handle(self) -> None:
            rule = book.pick(self.command, self.path)
            if rule is not None:
                announce(f"[fault] {rule.name}  {self.command} {self.path}")
                if rule.then.delay_ms:
                    time.sleep(rule.then.delay_ms / 1000)
                if rule.then.status is not None:
                    self._send(rule.then.status, [("Content-Type", "text/html")], _page(rule))
                    return
            fault = rule.then if rule else Fault()

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            headers = {
                name: value
                for name, value in self.headers.items()
                # Stripped so the application answers in plain text and a rewrite
                # does not have to decompress anything first.
                if name.lower() not in {"accept-encoding", "host", "connection"}
                # A request with no cookie is a request with no session. The
                # application issues a fresh anonymous one and serves what it serves
                # to a stranger, which is exactly what an expired session looks like.
                and not (fault.drop_session and name.lower() == "cookie")
            }

            connection = http.client.HTTPConnection(upstream.hostname or "", upstream.port or 80)
            try:
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                payload = response.read()
                content_type = response.getheader("Content-Type", "")
                out = [
                    (name, value.replace(origin, self._own_origin()))
                    for name, value in response.getheaders()
                    if name.lower() not in SKIP_RESPONSE_HEADERS
                ]
                if content_type.startswith("text/html"):
                    payload = rewrite(payload, content_type, fault.replace)
                    payload = payload.replace(
                        origin.encode("ascii"), self._own_origin().encode("ascii")
                    )
                self._send(response.status, out, payload)
            finally:
                connection.close()

        def _own_origin(self) -> str:
            """Anything pointing at the application by absolute URL would let the
            browser step around the proxy and quietly stop being faulted."""
            return f"http://{self.headers.get('Host', 'localhost')}"

        def _send(self, status: int, headers: list[tuple[str, str]], body: bytes) -> None:
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Silence the per-request access log. The only interesting line is a
            rule firing, and that one is printed above."""

    return Handler


def _page(rule: Rule) -> bytes:
    body = rule.then.body or f"<h1>{rule.then.status}</h1>"
    return f"<html><head><title>Error</title></head><body>{body}</body></html>".encode()


def serve(faults: Faults, port: int, announce: Callable[[str], None]) -> ThreadingHTTPServer:
    """Start the proxy on a background thread and return the server, unstarted-looking
    but already listening. The caller owns shutting it down."""
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(RuleBook(faults), announce))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
