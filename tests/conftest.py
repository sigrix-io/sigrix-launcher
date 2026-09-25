"""Shared fixtures: a wheel builder, a package builder, and a distributor on loopback.

The distributor is a real HTTP server on 127.0.0.1, because the launcher's
client is the Postern runner's own and speaks ``http.client`` to a socket —
there is no transport to swap for a fake. Every answer it gives is built on
the test's thread before the request arrives, so a mistake in a fixture fails
the test rather than surfacing as the connection error the launcher is built
to survive.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OWNER = "mira-fix"
LISTING = "modelwatch-1a2b3c"
AGENT_ID = f"{OWNER}/{LISTING}"
TOKEN = "feed-token-for-the-tests-0123456789"

#: The seller's server in the tests: answers one JSON-RPC line on stdin with
#: one on stdout, and reports whether the buyer's token reached it.
ECHO_SERVER = """\
import json
import os
import sys


def main():
    request = json.loads(sys.stdin.readline())
    reply = {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {"serverInfo": {"name": "echo"}, "token_seen": "SIGRIX_TOKEN" in os.environ, "argv": sys.argv[1:]},
    }
    sys.stdout.write(json.dumps(reply) + "\\n")
    sys.stdout.flush()
"""


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")


def build_wheel(
    *,
    name: str = "modelwatch-mcp",
    version: str = "1.0.0",
    entry_point: str = "modelwatch-mcp",
    module_source: str = ECHO_SERVER,
    requires_python: str = ">=3.11",
) -> tuple[str, bytes]:
    """A real, installable, dependency-free wheel, and its file name."""
    dist = name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    files = {
        f"{dist}/__init__.py": b"",
        f"{dist}/server.py": module_source.encode("utf-8"),
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: {requires_python}\n"
        ).encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{info}/entry_points.txt": f"[console_scripts]\n{entry_point} = {dist}.server:main\n".encode(),
    }
    record = "".join(f"{path},{_record_hash(data)},{len(data)}\n" for path, data in files.items())
    record += f"{info}/RECORD,,\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
        archive.writestr(f"{info}/RECORD", record)
    return f"{dist}-{version}-py3-none-any.whl", buffer.getvalue()


def build_package(
    *,
    version: str = "1.0.0",
    manifest_overrides: dict[str, Any] | None = None,
    wheel: tuple[str, bytes] | None = None,
    extra_entries: dict[str, bytes] | None = None,
    **wheel_kwargs: Any,
) -> bytes:
    """What the distributor serves: the manifest, then the wheel, as one zip."""
    filename, data = wheel or build_wheel(version=version, **wheel_kwargs)
    manifest = {
        "kind": "sigrix-mcp-package",
        "format": 1,
        "name": wheel_kwargs.get("name", "modelwatch-mcp"),
        "version": version,
        "entry_point": wheel_kwargs.get("entry_point", "modelwatch-mcp"),
        "wheel": filename,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "requires_python": wheel_kwargs.get("requires_python", ">=3.11"),
        "dependencies": [],
    }
    manifest.update(manifest_overrides or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sigrix-package.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        archive.writestr(filename, data)
        for path, content in (extra_entries or {}).items():
            archive.writestr(path, content)
    return buffer.getvalue()


def repr_digest(content: bytes) -> str:
    return f"sha-256=:{base64.b64encode(hashlib.sha256(content).digest()).decode('ascii')}:"


@dataclass
class Distributor:
    """What the fake distributor answers, and what it was asked."""

    base_url: str = ""
    check_status: int = 200
    state: str = "active"
    bundle_status: int = 200
    bundle: bytes = b""
    digest: str | None = None  # None: the right one; "": none at all
    bundle_error: dict[str, Any] | None = None
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None

    def serve(self, package: bytes) -> None:
        self.bundle = package

    def paths(self) -> list[str]:
        return [path for path, _ in self.requests]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _handler(distributor: Distributor) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

        def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
            distributor.requests.append((self.path, dict(self.headers.items())))
            if self.path == f"/postern/v0/entitlements/{AGENT_ID}":
                self._check()
            elif self.path == f"/postern/v0/bundles/{AGENT_ID}":
                self._bundle()
            else:
                self._send(404, b'{"error": {"code": "not_found", "message": "Not found."}}', {})

        def _check(self) -> None:
            if distributor.check_status != 200:
                self._send(distributor.check_status, b'{"error": {"code": "not_found", "message": "x"}}', {})
                return
            body = {
                "postern": "0.1",
                "state": distributor.state,
                "agent_id": AGENT_ID,
                "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "stale_after_seconds": 60,
                "grace_seconds": 86400,
            }
            self._send(200, json.dumps(body).encode(), {"Content-Type": "application/json"})

        def _bundle(self) -> None:
            if distributor.bundle_status != 200:
                error = distributor.bundle_error or {"error": {"code": "not_found", "message": "Not found."}}
                self._send(distributor.bundle_status, json.dumps(error).encode(), {})
                return
            headers = {"Content-Type": "application/zip"}
            digest = repr_digest(distributor.bundle) if distributor.digest is None else distributor.digest
            if digest:
                headers["Repr-Digest"] = digest
            self._send(200, distributor.bundle, headers)

    return Handler


@pytest.fixture()
def distributor() -> Any:
    state = Distributor()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    state._server = server
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    state.stop()


class FakeInstaller:
    """Builds an 'environment' holding only the console script, and counts builds."""

    tool = "fake"

    def __init__(self) -> None:
        self.created: list[Path] = []
        self.installed: list[Path] = []

    def create(self, env_dir: Path) -> None:
        (env_dir / "bin").mkdir(parents=True)
        self.created.append(env_dir)

    def install(self, env_dir: Path, wheel: Path) -> None:
        assert wheel.is_file(), "the wheel is written before the installer is asked"
        script = env_dir / "bin" / "modelwatch-mcp"
        script.write_text("#!/bin/sh\n", encoding="utf-8")
        self.installed.append(wheel)


@pytest.fixture()
def installer() -> FakeInstaller:
    return FakeInstaller()
