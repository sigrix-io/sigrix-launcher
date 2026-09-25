"""One start of a Sigrix-delivered MCP server: check, fetch, install, hand over stdio.

Everything a buyer can be told arrives as a :class:`LaunchError` whose text is
the sentence printed, on stderr, before the launcher exits. Nothing here
writes to stdout: from the moment an MCP client starts this process, stdout
is the protocol connection, and a stray line on it is a client that cannot
talk to the server.

**Which refusals fall back to the cache, and which do not.** A distributor
that cannot be reached is no answer at all, so a version already installed
here still starts — inside the entitlement's grace, which the check has
already decided. A distributor that *answers* no is an answer: a withdrawn
version must stop being served on the next pull, and starting the cached copy
after a ``404`` would quietly undo the withdrawal. So only
:class:`PackageUnavailable` reaches the cache.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sigrix_launcher import DELIVERY_MODE, MANIFEST_FILENAME, MANIFEST_KIND, SUPPORTED_MANIFEST_FORMAT
from sigrix_launcher._postern import PATH_PREFIX, pull
from sigrix_launcher._postern.describe import AGENT_ID_SHAPE, is_agent_id
from sigrix_launcher._postern.entitlement import DEFAULT_DISTRIBUTOR, GATE_NOT_ENTITLED, GATE_UNAVAILABLE, Entitlement
from sigrix_launcher._postern.transport import TransportError, open_response
from sigrix_launcher.installer import Installer, InstallError, console_script

#: The three variables the launcher reads. The token is the one on the buyer's
#: Sigrix account; the other two are for testing against another distributor
#: and for moving the cache.
TOKEN_ENV = "SIGRIX_TOKEN"  # noqa: S105 - the variable's name, not a credential
DISTRIBUTOR_ENV = "POSTERN_DISTRIBUTOR"
HOME_ENV = "SIGRIX_LAUNCHER_HOME"

#: Prefixes of the variables that are the launcher's own. The seller's server
#: is started without them: it has no use for the buyer's Sigrix token.
OWN_VARIABLE_PREFIXES = ("SIGRIX_", "POSTERN_")

ENTITLEMENT_CACHE = "entitlement.json"
CURRENT_FILE = "current.json"
READY_FILE = "ready.json"
LOCK_FILE = ".lock"
VERSIONS_DIR = "versions"

#: The largest wheel this launcher unpacks, and the manifest beside it.
MAX_WHEEL_BYTES = pull.MAX_BUNDLE_BYTES
MAX_MANIFEST_BYTES = 64 * 1024
_ERROR_BODY_BYTES = 64 * 1024

_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,63}$")
_SAFE_SCRIPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_WHEEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]{0,200}\.whl$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PYTHON_FLOOR = re.compile(r"^\s*>=\s*(\d+)\.(\d+)")


class LaunchError(RuntimeError):
    """Why the seller's server was not started. ``str()`` is what the buyer reads."""


class PackageUnavailable(LaunchError):
    """No answer about the package — the network, or the distributor having a bad minute.

    The one failure a cached version may start through: see the module docstring.
    """


@dataclass(frozen=True)
class Settings:
    """What one start needs, resolved from the command line and the environment."""

    agent_id: str
    token: str
    distributor: str
    home: Path

    @property
    def owner(self) -> str:
        return self.agent_id.partition("/")[0]

    @property
    def listing_id(self) -> str:
        return self.agent_id.partition("/")[2]

    @property
    def listing_dir(self) -> Path:
        # Both parts are safe path components: the identifier grammar allows
        # letters, digits, '-' and '.', and neither part may start or end with
        # a '.', so neither can be '..'.
        return self.home / self.owner / self.listing_id

    @property
    def listing_url(self) -> str:
        return f"{self.distributor}/mcp/{self.listing_id}"

    @property
    def token_page(self) -> str:
        return f"{self.distributor}/account/plugins"


def settings_from_environment(agent_id: str, environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    agent_id = str(agent_id or "").strip()
    if not is_agent_id(agent_id):
        raise LaunchError(
            f"{agent_id!r} is not a listing identifier: an identifier is {AGENT_ID_SHAPE}. "
            "Copy this server's configuration from the listing's page again."
        )
    distributor = (str(env.get(DISTRIBUTOR_ENV) or "").strip() or DEFAULT_DISTRIBUTOR).rstrip("/")
    token = str(env.get(TOKEN_ENV) or "").strip()
    if not token:
        raise LaunchError(
            f"{TOKEN_ENV} is not set. It is the token on your Sigrix account's plugins page, "
            f'{distributor}/account/plugins — put it in the "env" block of this server\'s configuration.'
        )
    return Settings(agent_id=agent_id, token=token, distributor=distributor, home=cache_home(env))


def cache_home(environ: Mapping[str, str]) -> Path:
    """Where installed versions and the last purchase check are kept."""
    explicit = str(environ.get(HOME_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt":
        base = str(environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = str(environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return Path(base) / "sigrix-launcher"


@dataclass(frozen=True)
class Package:
    """A pulled package, read and checked, not yet written anywhere."""

    name: str
    version: str
    entry_point: str
    wheel_filename: str
    wheel: bytes
    sha256: str
    requires_python: str
    manifest: dict[str, Any]


@dataclass(frozen=True)
class Installed:
    """A version ready to start."""

    version: str
    script: Path
    from_cache: bool


# ---------------------------------------------------------------------------
# 1. The purchase
# ---------------------------------------------------------------------------


def check_purchase(settings: Settings, *, now: datetime | None = None) -> None:
    """Return if this token may start the listing now; raise :class:`LaunchError` if not.

    The answer is the runner's :class:`Entitlement`, unchanged: re-asked once
    the held answer is past its window, kept through the declared grace when
    the distributor cannot be reached, and final on a ``404``. It is cached
    beside the listing's versions with a fingerprint of the token, never the
    token, so a restart offline is decided by the last answer rather than by
    whether the network came back.
    """
    settings.listing_dir.mkdir(parents=True, exist_ok=True)
    entitlement = Entitlement(
        base_url=settings.distributor,
        token=settings.token,
        agent_id=settings.agent_id,
        cache_path=settings.listing_dir / ENTITLEMENT_CACHE,
        delivery_mode=DELIVERY_MODE,
        listing_url=settings.listing_url,
    )
    verdict = entitlement.refresh(now=now)
    if entitlement.misaddressed:
        raise LaunchError(
            f"{settings.distributor} did not recognise {settings.agent_id!r} as a listing. "
            "Copy this server's configuration from the listing's page again."
        )
    if verdict.gate == GATE_NOT_ENTITLED:
        # SPEC 5.5: the distributor cannot say which of these it is, so neither
        # can the launcher. Every cause, and where to act on each.
        raise LaunchError(
            f"Sigrix says this token does not own {settings.agent_id}: it was not bought with this account, "
            "it was refunded, or the token has been replaced since. Nothing was downloaded. See the listing "
            f"at {settings.listing_url}, and check {TOKEN_ENV} against {settings.token_page}."
        )
    if verdict.gate == GATE_UNAVAILABLE:
        raise LaunchError(
            f"Could not confirm your purchase: {settings.distributor} did not answer, and there is no recent "
            "confirmation on this machine to start on. Check the network connection and start the server again."
        )


# ---------------------------------------------------------------------------
# 2. The package, held in memory until it verifies
# ---------------------------------------------------------------------------


def fetch_package(
    settings: Settings,
    *,
    timeout: float = pull.PULL_TIMEOUT_SECONDS,
    max_bytes: int = pull.MAX_BUNDLE_BYTES,
) -> bytes:
    """The package's bytes, verified against the distributor's ``Repr-Digest``.

    The bounded read and the digest check are the runner's own (``pull``);
    what is the launcher's is the wording, because a buyer starting an MCP
    server has no ``.env`` and no ``POSTERN_AGENT_ID`` to be pointed at. A
    missing digest is refused rather than warned about: the Sigrix distributor
    always sends one, and there is nothing else to verify the manifest by.
    """
    path = f"{PATH_PREFIX}/bundles/{settings.owner}/{settings.listing_id}"
    try:
        with open_response(
            settings.distributor, path, token=settings.token, accept=pull.BUNDLE_MEDIA_TYPE, timeout=timeout
        ) as response:
            status = int(response.status)
            if status == 200:
                pull._refuse_declared_length(response.getheader("Content-Length"), max_bytes=max_bytes)
                content = pull._read_bounded(response, max_bytes=max_bytes)
                digest_header = response.getheader(pull.REPR_DIGEST_HEADER) or ""
                error_body = b""
            else:
                content, digest_header = b"", ""
                error_body = response.read(_ERROR_BODY_BYTES)
    except TransportError as exc:
        raise PackageUnavailable(f"Could not reach {settings.distributor} to fetch the package: {exc}.") from exc
    except pull.PullRefused as exc:
        raise LaunchError(f"The package was refused before it was read: {exc}") from exc

    if status != 200:
        raise _refusal(status, error_body, settings)
    try:
        bundle = pull._verified(content, digest_header, agent_id=settings.agent_id)
    except pull.PullRefused as exc:
        raise LaunchError(str(exc)) from exc
    if not bundle.verified:
        raise LaunchError(
            f"{settings.distributor} sent the package without a checksum to verify it by, so nothing was "
            f"installed. The Sigrix distributor always sends one; check {DISTRIBUTOR_ENV} if you set it."
        )
    return bundle.content


def _refusal(status: int, body: bytes, settings: Settings) -> LaunchError:
    code, detail = pull._envelope(body)
    if status == 404:
        # The check has just said this token owns the listing, so this is about
        # the package rather than the purchase.
        return LaunchError(
            f"Sigrix has no version of {settings.agent_id} to hand over right now. The seller's newest version "
            "may still be waiting for review, or the listing may install from a public registry instead — its "
            f"page says which: {settings.listing_url}. Nothing was downloaded."
        )
    if status == 410 or code == "withdrawn":
        ends_at = str(detail.get("access_ends_at") or "")[:10]
        when = f" Your access ended on {ends_at}." if ends_at else " Your access has ended."
        return LaunchError(f"The seller withdrew {settings.agent_id}.{when} Nothing was downloaded.")
    if status == 400:
        return LaunchError(
            f"{settings.distributor} did not recognise {settings.agent_id!r} as a listing. "
            "Copy this server's configuration from the listing's page again."
        )
    if 400 <= status < 500:
        return LaunchError(f"{settings.distributor} refused to hand over {settings.agent_id} (HTTP {status}).")
    return PackageUnavailable(
        f"{settings.distributor} could not hand over {settings.agent_id} just now (HTTP {status})."
    )


def read_package(content: bytes) -> Package:
    """The manifest and the wheel out of a verified package, each checked against the other."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise LaunchError("The package Sigrix sent is not a zip archive, so nothing was installed.") from exc
    with archive:
        entries = {info.filename: info for info in archive.infolist()}
        if MANIFEST_FILENAME not in entries:
            raise LaunchError(f"The package carries no {MANIFEST_FILENAME}, so there is no telling what to start.")
        manifest = _read_manifest(archive, entries[MANIFEST_FILENAME])
        wheel_filename = manifest["wheel"]
        unexpected = sorted(set(entries) - {MANIFEST_FILENAME, wheel_filename})
        if wheel_filename not in entries or unexpected:
            raise LaunchError(
                f"The package should hold {MANIFEST_FILENAME} and {wheel_filename} and nothing else, "
                "so nothing was installed."
            )
        if entries[wheel_filename].file_size > MAX_WHEEL_BYTES:
            raise LaunchError(
                f"The wheel in the package is larger than {MAX_WHEEL_BYTES:,} bytes; nothing was installed."
            )
        wheel = archive.read(entries[wheel_filename])

    if hashlib.sha256(wheel).hexdigest() != manifest["sha256"]:
        raise LaunchError(
            "The wheel in the package does not match the checksum its manifest gives, so nothing was installed."
        )
    return Package(
        name=manifest["name"],
        version=manifest["version"],
        entry_point=manifest["entry_point"],
        wheel_filename=wheel_filename,
        wheel=wheel,
        sha256=manifest["sha256"],
        requires_python=str(manifest.get("requires_python") or ""),
        manifest=manifest,
    )


def _read_manifest(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict[str, Any]:
    if info.file_size > MAX_MANIFEST_BYTES:
        raise LaunchError(f"The package's {MANIFEST_FILENAME} is implausibly large, so nothing was installed.")
    try:
        manifest = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LaunchError(f"The package's {MANIFEST_FILENAME} is not readable JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != MANIFEST_KIND:
        raise LaunchError(f"This is not a Sigrix MCP package ({MANIFEST_FILENAME} says otherwise).")
    package_format = manifest.get("format")
    if not isinstance(package_format, int) or package_format < 1:
        raise LaunchError(f"The package's {MANIFEST_FILENAME} names no format this launcher knows.")
    if package_format > SUPPORTED_MANIFEST_FORMAT:
        raise LaunchError(
            f"This package is in format {package_format}, and this launcher reads format "
            f"{SUPPORTED_MANIFEST_FORMAT}. Update the launcher: write sigrix-launcher@latest in place of "
            "sigrix-launcher in this server's configuration, then start it again."
        )
    checks = {
        "name": lambda value: bool(value.strip()),
        "version": lambda value: bool(_SAFE_VERSION.fullmatch(value)),
        "entry_point": lambda value: bool(_SAFE_SCRIPT.fullmatch(value)),
        "wheel": lambda value: bool(_SAFE_WHEEL.fullmatch(value)),
        "sha256": lambda value: bool(_SHA256.fullmatch(value)),
    }
    for key, valid in checks.items():
        value = manifest.get(key)
        if not isinstance(value, str) or not valid(value):
            raise LaunchError(f"The package's {MANIFEST_FILENAME} has no usable {key!r}, so nothing was installed.")
    return manifest


# ---------------------------------------------------------------------------
# 3. One environment per version, reused on the next start
# ---------------------------------------------------------------------------


def ensure_installed(settings: Settings, package: Package, installer: Installer) -> Installed:
    """This version's environment, built once and reused while its files are intact.

    Built in place and marked ready last: an environment's scripts name its
    own path, so one built elsewhere and moved would start nothing. A start
    that dies halfway leaves no ready marker, and the next start rebuilds.
    Serialised per listing, so two clients starting the same server at once
    do not build it twice into one folder.
    """
    target = settings.listing_dir / VERSIONS_DIR / f"{package.version}-{package.sha256[:12]}"
    env_dir = target / "env"
    script = console_script(env_dir, package.entry_point)
    with _locked(settings.listing_dir / LOCK_FILE):
        ready = _read_json(target / READY_FILE)
        if (
            ready.get("sha256") == package.sha256
            and ready.get("entry_point") == package.entry_point
            and script.is_file()
        ):
            _write_current(settings, target, package)
            return Installed(version=package.version, script=script, from_cache=True)

        _check_python(package)
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        try:
            wheel_path = target / package.wheel_filename
            wheel_path.write_bytes(package.wheel)
            (target / MANIFEST_FILENAME).write_text(
                json.dumps(package.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            installer.create(env_dir)
            installer.install(env_dir, wheel_path)
            if not script.is_file():
                raise LaunchError(
                    f"Version {package.version} of {package.name} installed, but it has no console script named "
                    f"{package.entry_point!r} to start. That is the seller's to fix; tell them on the listing's page."
                )
            _write_json(
                target / READY_FILE,
                {"version": package.version, "sha256": package.sha256, "entry_point": package.entry_point},
            )
        except InstallError as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise LaunchError(f"Could not install version {package.version} of {package.name}. {exc}") from exc
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        _write_current(settings, target, package)
    return Installed(version=package.version, script=script, from_cache=False)


def cached_installation(settings: Settings) -> Installed | None:
    """The version this machine started last, if it is still intact."""
    current = _read_json(settings.listing_dir / CURRENT_FILE)
    folder = str(current.get("folder") or "")
    if not folder or "/" in folder or "\\" in folder or folder.startswith("."):
        return None
    target = settings.listing_dir / VERSIONS_DIR / folder
    ready = _read_json(target / READY_FILE)
    entry_point = str(ready.get("entry_point") or "")
    if not entry_point or not _SAFE_SCRIPT.fullmatch(entry_point):
        return None
    script = console_script(target / "env", entry_point)
    if not script.is_file():
        return None
    return Installed(version=str(ready.get("version") or ""), script=script, from_cache=True)


def _check_python(package: Package) -> None:
    """A friendly refusal for the common floor (``>=3.12``); the installer judges the rest."""
    floor = _PYTHON_FLOOR.match(package.requires_python)
    if floor and sys.version_info[:2] < (int(floor[1]), int(floor[2])):
        wanted = f"{floor[1]}.{floor[2]}"
        raise LaunchError(
            f"Version {package.version} of {package.name} needs Python {package.requires_python}, and the "
            f"launcher is running on {sys.version.split()[0]}. Start it with a newer one: put "
            f'"--python", "{wanted}" before "sigrix-launcher" in this server\'s args.'
        )


def _write_current(settings: Settings, target: Path, package: Package) -> None:
    _write_json(settings.listing_dir / CURRENT_FILE, {"folder": target.name, "version": package.version})


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    staged = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    staged.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(staged, path)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """An exclusive lock on one file, held for the block, on either platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # LK_LOCK gives up after ten seconds; keep waiting
                    continue
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# 4. The whole start, and the hand-over
# ---------------------------------------------------------------------------


def say(text: str) -> None:
    """One line for the buyer, on stderr — the only stream that is not the connection."""
    print(f"sigrix-launcher: {text}", file=sys.stderr, flush=True)


def prepare(
    settings: Settings,
    *,
    installer: Installer | None = None,
    now: datetime | None = None,
    tell: Callable[[str], None] = say,
) -> Installed:
    """Check, fetch, verify and install; the version to start, or :class:`LaunchError`."""
    check_purchase(settings, now=now)
    try:
        content = fetch_package(settings)
    except PackageUnavailable as exc:
        cached = cached_installation(settings)
        if cached is None:
            raise LaunchError(
                f"{exc} No version of this listing has been installed on this machine yet, so there is "
                "nothing to start until the download succeeds."
            ) from exc
        tell(f"{exc} Starting version {cached.version}, installed here earlier.")
        return cached
    package = read_package(content)
    installed = ensure_installed(settings, package, installer or Installer())
    if not installed.from_cache:
        tell(f"Installed version {package.version} of {package.name}.")
    return installed


def server_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The client's environment for the seller's server, less the launcher's own variables."""
    return {key: value for key, value in environ.items() if not key.startswith(OWN_VARIABLE_PREFIXES)}


def start_server(
    installed: Installed,
    args: Sequence[str],
    environ: Mapping[str, str],
    *,
    execute: Callable[[list[str], dict[str, str]], int] | None = None,
) -> int:
    """Hand this process's stdio to the seller's server.

    On POSIX the launcher *becomes* the server (``execve``), so the client's
    pipes reach it with nothing in between and its exit status is the one the
    client sees. Windows has no such call, so the server runs as a child that
    inherits the same handles, and the launcher exits with its status.
    """
    command = [str(installed.script), *args]
    env = server_environment(environ)
    sys.stdout.flush()
    sys.stderr.flush()
    if execute is not None:
        return execute(command, env)
    if os.name == "nt":
        return subprocess.run(command, env=env, check=False).returncode  # noqa: S603 - the installed server
    os.execve(command[0], command, env)  # noqa: S606 - the installed server replaces this process
    return 0  # pragma: no cover - execve does not return


__all__ = [
    "DISTRIBUTOR_ENV",
    "HOME_ENV",
    "TOKEN_ENV",
    "Installed",
    "LaunchError",
    "Package",
    "PackageUnavailable",
    "Settings",
    "cache_home",
    "cached_installation",
    "check_purchase",
    "ensure_installed",
    "fetch_package",
    "prepare",
    "read_package",
    "server_environment",
    "settings_from_environment",
    "start_server",
]
