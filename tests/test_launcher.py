"""The launcher, against a distributor on loopback.

Acceptance, one section each:

- a buyer who paid gets the seller's server started, installed once per version;
- someone who did not pay, or was refunded, is refused with a sentence naming
  why, and the seller's code is never downloaded;
- offline inside the grace window it starts from the cache, and past it, or
  with nothing installed, it says why not;
- the token is never written to disk, and never reaches the seller's server;
- what arrives is verified before anything is written.

The last test starts a real process from a real wheel and speaks to it over
stdio, because "nothing but the server writes to stdout" is only provable on a
real pipe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import AGENT_ID, TOKEN, build_package, build_wheel

from sigrix_launcher import DELIVERY_MODE
from sigrix_launcher.launcher import (
    LaunchError,
    Settings,
    prepare,
    read_package,
    server_environment,
    settings_from_environment,
    start_server,
)


def _settings(distributor: Any, home: Path) -> Settings:
    return Settings(agent_id=AGENT_ID, token=TOKEN, distributor=distributor.base_url, home=home)


def _prepare(distributor: Any, home: Path, installer: Any, **kwargs: Any) -> Any:
    return prepare(_settings(distributor, home), installer=installer, tell=lambda _text: None, **kwargs)


# ---------------------------------------------------------------------------
# A buyer who paid
# ---------------------------------------------------------------------------


def test_a_buyer_who_paid_gets_the_version_installed(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.serve(build_package(version="1.0.0"))

    installed = _prepare(distributor, tmp_path, installer)

    assert installed.version == "1.0.0" and not installed.from_cache
    assert installed.script.is_file()
    assert len(installer.created) == 1
    check_headers = dict(distributor.requests[0][1])
    assert check_headers["Authorization"] == f"Bearer {TOKEN}"
    assert check_headers["X-Sigrix-Delivery-Mode"] == DELIVERY_MODE
    assert distributor.paths() == [f"/postern/v0/entitlements/{AGENT_ID}", f"/postern/v0/bundles/{AGENT_ID}"]


def test_a_second_start_reuses_the_installed_version(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.serve(build_package(version="1.0.0"))
    _prepare(distributor, tmp_path, installer)

    again = _prepare(distributor, tmp_path, installer)

    assert again.from_cache and again.version == "1.0.0"
    assert len(installer.created) == 1


def test_a_new_version_is_installed_on_the_next_start(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.serve(build_package(version="1.0.0"))
    first = _prepare(distributor, tmp_path, installer)
    distributor.serve(build_package(version="1.1.0"))

    second = _prepare(distributor, tmp_path, installer)

    assert second.version == "1.1.0" and not second.from_cache
    assert first.script != second.script and first.script.is_file()


# ---------------------------------------------------------------------------
# Someone who did not pay, or was refunded
# ---------------------------------------------------------------------------


def test_someone_who_did_not_pay_is_refused_and_nothing_is_downloaded(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    distributor.check_status = 404
    distributor.serve(build_package())

    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)

    assert "does not own" in str(refused.value) and "refunded" in str(refused.value)
    assert f"/mcp/{AGENT_ID.split('/')[1]}" in str(refused.value)
    assert not [path for path in distributor.paths() if "/bundles/" in path]
    assert installer.created == []


def test_a_refund_takes_effect_at_the_next_start_even_with_a_version_installed(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    distributor.serve(build_package())
    _prepare(distributor, tmp_path, installer)

    distributor.check_status = 404  # refunded
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer, now=datetime.now(UTC) + timedelta(minutes=5))
    assert "does not own" in str(refused.value)


# ---------------------------------------------------------------------------
# Offline
# ---------------------------------------------------------------------------


def test_offline_inside_the_grace_window_it_starts_from_the_cache(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    distributor.serve(build_package(version="1.0.0"))
    first = _prepare(distributor, tmp_path, installer)
    distributor.stop()

    offline = _prepare(distributor, tmp_path, installer, now=datetime.now(UTC) + timedelta(hours=2))

    assert offline.from_cache and offline.script == first.script


def test_offline_past_the_grace_window_it_refuses(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.serve(build_package())
    _prepare(distributor, tmp_path, installer)
    distributor.stop()

    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer, now=datetime.now(UTC) + timedelta(hours=25))
    assert "Could not confirm your purchase" in str(refused.value)


def test_offline_on_the_very_first_start_it_says_it_has_never_checked(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    distributor.stop()
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert "no recent confirmation" in str(refused.value)


def test_a_package_that_cannot_be_fetched_the_first_time_starts_nothing(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    distributor.bundle_status = 503
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert "nothing to start" in str(refused.value)


# ---------------------------------------------------------------------------
# An answer is not an outage: nothing here reaches the cache
# ---------------------------------------------------------------------------


def test_nothing_approved_is_said_and_the_cached_version_is_not_started(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    """A withdrawn version stops being served on the next pull, including here."""
    distributor.serve(build_package())
    _prepare(distributor, tmp_path, installer)

    distributor.bundle_status = 404
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert "no version" in str(refused.value) and "waiting for review" in str(refused.value)


def test_past_the_withdrawal_tail_it_says_when_access_ended(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.bundle_status = 410
    distributor.bundle_error = {
        "error": {"code": "withdrawn", "message": "Gone.", "detail": {"access_ends_at": "2027-01-01T00:00:00Z"}}
    }
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert "withdrew" in str(refused.value) and "2027-01-01" in str(refused.value)


# ---------------------------------------------------------------------------
# Verified before anything is written
# ---------------------------------------------------------------------------


def _versions(home: Path) -> list[Path]:
    return sorted(home.glob("*/*/versions/*"))


@pytest.mark.parametrize(("digest", "phrase"), [("sha-256=:AAAA:", "does not match"), ("", "without a checksum")])
def test_a_package_that_does_not_verify_writes_nothing(
    distributor: Any, installer: Any, tmp_path: Path, digest: str, phrase: str
) -> None:
    distributor.serve(build_package())
    distributor.digest = digest

    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert phrase in str(refused.value)
    assert _versions(tmp_path) == []


def test_a_wheel_that_disagrees_with_its_manifest_is_refused(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.serve(build_package(manifest_overrides={"sha256": "0" * 64}))
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert "does not match the checksum its manifest gives" in str(refused.value)
    assert _versions(tmp_path) == []


@pytest.mark.parametrize(
    ("overrides", "phrase"),
    [
        ({"format": 2}, "sigrix-launcher@latest"),
        ({"kind": "something-else"}, "not a Sigrix MCP package"),
        ({"entry_point": "../../bin/sh"}, "'entry_point'"),
        ({"version": "1.0/../../x"}, "'version'"),
    ],
)
def test_a_manifest_this_launcher_cannot_trust_is_refused(overrides: dict[str, Any], phrase: str) -> None:
    with pytest.raises(LaunchError) as refused:
        read_package(build_package(manifest_overrides=overrides))
    assert phrase in str(refused.value)


def test_a_package_carrying_anything_else_is_refused() -> None:
    with pytest.raises(LaunchError) as refused:
        read_package(build_package(extra_entries={"../../etc/cron.d/x": b"* * * * * true"}))
    assert "nothing else" in str(refused.value)


def test_a_python_floor_above_this_interpreter_is_refused_before_installing(
    distributor: Any, installer: Any, tmp_path: Path
) -> None:
    distributor.serve(build_package(requires_python=">=3.99"))
    with pytest.raises(LaunchError) as refused:
        _prepare(distributor, tmp_path, installer)
    assert '"--python", "3.99"' in str(refused.value)
    assert installer.created == []


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def test_the_token_is_never_written_to_disk(distributor: Any, installer: Any, tmp_path: Path) -> None:
    distributor.serve(build_package())
    _prepare(distributor, tmp_path, installer)
    _prepare(distributor, tmp_path, installer, now=datetime.now(UTC) + timedelta(minutes=5))

    written = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert written, "the start wrote nothing at all, so this proves nothing"
    assert not [path for path in written if TOKEN.encode() in path.read_bytes()]


def test_the_sellers_server_gets_the_environment_without_the_launchers_variables() -> None:
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/buyer",
        "MODELWATCH_API_KEY": "the-sellers-own",
        "SIGRIX_TOKEN": TOKEN,
        "SIGRIX_LAUNCHER_HOME": "/tmp/x",
        "POSTERN_DISTRIBUTOR": "https://example.test",
    }
    assert server_environment(environ) == {
        "PATH": "/usr/bin",
        "HOME": "/home/buyer",
        "MODELWATCH_API_KEY": "the-sellers-own",
    }


def test_the_server_is_started_with_its_arguments_and_that_environment(tmp_path: Path) -> None:
    from sigrix_launcher.launcher import Installed

    seen: list[tuple[list[str], dict[str, str]]] = []
    installed = Installed(version="1.0.0", script=tmp_path / "modelwatch-mcp", from_cache=False)

    status = start_server(
        installed,
        ["--verbose"],
        {"SIGRIX_TOKEN": TOKEN, "PATH": "/usr/bin"},
        execute=lambda command, env: seen.append((command, env)) or 7,
    )

    assert status == 7
    assert seen == [([str(tmp_path / "modelwatch-mcp"), "--verbose"], {"PATH": "/usr/bin"})]


# ---------------------------------------------------------------------------
# Configuration mistakes
# ---------------------------------------------------------------------------


def test_an_unset_token_says_where_to_get_one() -> None:
    with pytest.raises(LaunchError) as refused:
        settings_from_environment(AGENT_ID, {})
    assert "SIGRIX_TOKEN is not set" in str(refused.value)
    assert "https://sigrix.io/account/plugins" in str(refused.value)


@pytest.mark.parametrize("agent_id", ["", "modelwatch", "Mira/ModelWatch", "a/b/c", "../x"])
def test_a_malformed_identifier_is_refused_before_anything_is_asked(agent_id: str) -> None:
    with pytest.raises(LaunchError) as refused:
        settings_from_environment(agent_id, {"SIGRIX_TOKEN": TOKEN})
    assert "not a listing identifier" in str(refused.value)


# ---------------------------------------------------------------------------
# A real start: a real wheel, a real environment, a real pipe
# ---------------------------------------------------------------------------


def test_a_real_start_hands_stdio_to_the_sellers_server(distributor: Any, tmp_path: Path) -> None:
    """The whole path, as an MCP client drives it.

    A dependency-free wheel is installed for real — with ``uv`` if this machine
    has it, ``venv`` and ``pip`` otherwise — and the launcher process is spoken
    to over its own stdin and stdout. The reply on stdout is the seller's
    server's and nothing else's, which is the property an MCP client depends
    on; and the server reports that the buyer's token never reached it.
    """
    distributor.serve(build_package(wheel=build_wheel(version="1.0.0")))
    env = {
        **os.environ,
        "SIGRIX_TOKEN": TOKEN,
        "POSTERN_DISTRIBUTOR": distributor.base_url,
        "SIGRIX_LAUNCHER_HOME": str(tmp_path / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "sigrix_launcher", "run", AGENT_ID, "--", "--verbose"],
        input=request.encode(),
        capture_output=True,
        env=env,
        timeout=300,
        check=False,
    )

    stderr = completed.stderr.decode("utf-8", "replace")
    assert completed.returncode == 0, stderr
    lines = completed.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1, f"stdout carried more than the server's reply: {lines!r}"
    reply = json.loads(lines[0])
    assert reply["id"] == 1 and reply["result"]["serverInfo"] == {"name": "echo"}
    assert reply["result"]["token_seen"] is False
    assert reply["result"]["argv"] == ["--verbose"]
    assert "Installed version 1.0.0" in stderr
