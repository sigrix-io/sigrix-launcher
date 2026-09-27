# Contributing

Thanks for looking. This is a small launcher, and the bar for a change is that
it stays one: check the purchase, fetch and verify, install, hand over stdio.

## What fits

- A bug in how a start is checked, fetched, verified, installed or handed over.
- A clearer sentence when it refuses. Say what happened and what the buyer can
  do about it; never guess at a cause the distributor did not give.
- Another platform or installer case that stops a real buyer's server starting.

## What does not

- A second implementation of anything the launcher imports from
  `sigrix_runtime.postern`. That is the Postern runner's own client code, from
  the `sigrix-runtime` package; a change to how an entitlement is decided or a
  download verified belongs in `sigrix-io/sigrix-runtime`, and reaches buyers
  when a release here moves the pin.
- Anything that runs while the seller's server runs. The launcher checks at
  start and then gets out of the way.
- Output on stdout. It is the MCP connection; everything else goes to stderr.

## Working on it

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
```

The tests run a distributor on loopback, so they need no network; the one that
installs a real wheel builds an environment with `uv` when it is on the path
and with `venv` and `pip` otherwise.

## Pull requests

- One change per pull request, with a test that fails without it.
- `CHANGELOG.md` gets a line under *Unreleased*.
- A change to what the launcher reads from a package, or to the command line
  a client's configuration carries, is a breaking change for every buyer's
  configuration; say so in the changelog.
