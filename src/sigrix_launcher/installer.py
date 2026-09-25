"""An environment per package version, and the seller's wheel installed into it.

``uv`` when it is on the path — a buyer who starts the launcher with ``uvx``
has it by definition, and it builds an environment in a second — and the
standard library's ``venv`` with that environment's own ``pip`` otherwise.
Either way the environment uses the interpreter running the launcher.

Every command here runs with stdin closed and both output streams captured:
the launcher's stdin and stdout are the MCP client's connection, and an
installer that read the one or printed to the other would corrupt it before
the seller's server ever started. What a failed install printed is handed
back in the error, for the launcher to show on stderr.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

#: How long one step may take. Resolving and downloading a server's
#: dependencies on a slow connection is the long case.
INSTALL_TIMEOUT_SECONDS = 600

#: The tail of an installer's output kept for the message a failure prints.
_OUTPUT_TAIL_LINES = 20


class InstallError(RuntimeError):
    """An environment could not be built; ``str()`` says why, with the installer's words."""


Runner = Callable[..., subprocess.CompletedProcess]


def environment_python(env_dir: Path) -> Path:
    """The interpreter inside an environment, on this platform's layout."""
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def console_script(env_dir: Path, name: str) -> Path:
    """Where an installed console script lands in an environment."""
    if os.name == "nt":
        return env_dir / "Scripts" / f"{name}.exe"
    return env_dir / "bin" / name


class Installer:
    """Builds an environment and installs one wheel into it.

    ``uv`` and ``runner`` are parameters so a test can say which installer it
    means and see the commands it would run; left alone, ``uv`` is looked up
    on the path and ``runner`` is :func:`subprocess.run`.
    """

    def __init__(
        self,
        *,
        uv: str | None = None,
        python: str = sys.executable,
        runner: Runner = subprocess.run,
        environ: Mapping[str, str] | None = None,
        prefer_uv: bool = True,
    ) -> None:
        self.uv = uv if uv is not None else (shutil.which("uv") if prefer_uv else None)
        self.python = python
        self.runner = runner
        self.environ = dict(os.environ if environ is None else environ)

    @property
    def tool(self) -> str:
        return "uv" if self.uv else "venv and pip"

    def create(self, env_dir: Path) -> None:
        if self.uv:
            self._run([self.uv, "venv", "--quiet", "--python", self.python, str(env_dir)], step="create")
        else:
            self._run([self.python, "-m", "venv", str(env_dir)], step="create")

    def install(self, env_dir: Path, wheel: Path) -> None:
        python = str(environment_python(env_dir))
        if self.uv:
            self._run([self.uv, "pip", "install", "--quiet", "--python", python, str(wheel)], step="install")
        else:
            self._run(
                [python, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", str(wheel)],
                step="install",
            )

    def _run(self, command: Sequence[str], *, step: str) -> None:
        try:
            completed = self.runner(
                list(command),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_installer_environment(self.environ),
                timeout=INSTALL_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError(f"{self.tool} could not {step} the environment: {exc}") from exc
        if completed.returncode != 0:
            said = _tail(completed.stderr) or _tail(completed.stdout)
            raise InstallError(
                f"{self.tool} could not {step} the environment (exit {completed.returncode})."
                + (f" It said:\n{said}" if said else "")
            )


def _installer_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The installer needs a network and a cache, not the buyer's token."""
    return {key: value for key, value in environ.items() if not key.startswith(("SIGRIX_", "POSTERN_"))}


def _tail(output: bytes | str | None) -> str:
    if not output:
        return ""
    text = output.decode("utf-8", "replace") if isinstance(output, bytes) else output
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-_OUTPUT_TAIL_LINES:])


__all__ = [
    "INSTALL_TIMEOUT_SECONDS",
    "InstallError",
    "Installer",
    "console_script",
    "environment_python",
]
