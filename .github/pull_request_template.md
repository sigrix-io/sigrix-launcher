## What this changes

<!-- One or two sentences. The diff shows what changed; say why. -->

## Does a buyer see it?

<!--
Delete the rows that do not apply.

- [ ] No: refactor, docs, tests or tooling only.
- [ ] A start behaves differently: the check, the download, the install or the
      handover. Say what a buyer sees.
- [ ] The command line a client's configuration carries, or what the launcher
      reads from a package, changed. CONTRIBUTING.md calls that a breaking
      change for every buyer's configuration; the changelog says so.
- [ ] The `sigrix-runtime` pin moved. Say what the new release changes for a
      buyer.
-->

## The test that fails without it

<!--
One change per pull request, with a test that fails without it. Break the
implementation on purpose, confirm the test goes red, then put it back. Say
what you broke and which test caught it.
-->

## Checks

- [ ] `pytest`
- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] Nothing new on stdout, which is the MCP connection
- [ ] `CHANGELOG.md` has a line under *Unreleased*
