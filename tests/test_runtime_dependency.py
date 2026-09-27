"""The purchase check and the download are ``sigrix-runtime``'s client code, and nothing more of it.

The launcher used to carry a copy of the Postern runner's client code. It now
depends on the package the runner is published as, and two things have to hold
for that to be as safe as the copy was:

- **The pin is exact, and it is what these tests ran against.** The launcher
  calls helpers of the runtime's ``pull`` module that no version promise
  covers, so a new runtime release must reach buyers through a launcher
  release whose tests ran against it, never through a range resolving to it.
- **A start loads the runner's client and nothing that runs an agent.** The
  rest of the runtime builds and runs crews and needs PyYAML and CrewAI, which
  a ``uvx`` start never installs; importing it would stop every buyer's server
  before it started.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DISTRIBUTION = "sigrix-runtime"
RUNTIME_CLIENT = "sigrix_runtime.postern"


def test_the_runtime_is_pinned_exactly_and_is_the_version_installed() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    named = [spec for spec in project["dependencies"] if re.match(rf"{RUNTIME_DISTRIBUTION}\b", spec)]
    assert len(named) == 1, f"pyproject.toml should depend on {RUNTIME_DISTRIBUTION} once, not {named}"
    exact = re.fullmatch(rf"{RUNTIME_DISTRIBUTION}==(\d+\.\d+\.\d+)", named[0])
    assert exact, (
        f"{named[0]!r} is not an exact pin. The launcher calls helpers of the runtime's pull module that no "
        "version promise covers, so a runtime release reaches buyers only through a launcher release."
    )
    assert importlib.metadata.version(RUNTIME_DISTRIBUTION) == exact[1], (
        f"These tests are running against {RUNTIME_DISTRIBUTION} "
        f"{importlib.metadata.version(RUNTIME_DISTRIBUTION)}, not the {exact[1]} the launcher pins: "
        'reinstall with `pip install -e ".[dev]"`.'
    )


def test_a_start_loads_the_runner_client_and_nothing_that_runs_an_agent() -> None:
    # A fresh interpreter, because this one has pytest and every other test's
    # imports in it. What the console script's import adds is what a start pays for.
    probe = (
        "import json, sys\n"
        "before = set(sys.modules)\n"
        "import sigrix_launcher.cli\n"
        "print(json.dumps(sorted(set(sys.modules) - before)))\n"
    )
    loaded = json.loads(
        subprocess.run([sys.executable, "-c", probe], check=True, capture_output=True, text=True).stdout
    )
    runtime = [name for name in loaded if name.split(".")[0] == "sigrix_runtime"]
    # The canary: a launcher that had grown its own client again would load
    # none of the runtime's, and every check below would pass vacuously.
    assert {f"{RUNTIME_CLIENT}.entitlement", f"{RUNTIME_CLIENT}.pull"} <= set(runtime)
    assert [name for name in runtime if name != "sigrix_runtime" and not name.startswith(RUNTIME_CLIENT)] == []
    outside = sorted(
        {name.split(".")[0] for name in loaded} - set(sys.stdlib_module_names) - {"sigrix_launcher", "sigrix_runtime"}
    )
    assert outside == [], f"starting the launcher imports {outside}, which a uvx start does not install"
