# Versioning

Semantic versioning, `MAJOR.MINOR.PATCH`, released from a `vX.Y.Z` tag.

- **PATCH**: a fix that changes no command, no variable and nothing the
  launcher reads from a package.
- **MINOR**: a new option or variable; a new package format the launcher can
  read, alongside the old ones.
- **MAJOR**: a removed or renamed command, option or variable, or a package
  format the launcher stops reading. Every buyer's client configuration names
  this package, so a major version is a change to all of them.

Before 1.0 a MINOR bump may carry what would later be MAJOR; the changelog says
so when it does.

`SUPPORTED_MANIFEST_FORMAT` (`src/sigrix_launcher/__init__.py`) is the newest
package format this release reads. A package in a newer one is refused with a
message telling the buyer to update the launcher, never guessed at.

## Releasing

A release is a tag. `release.yml` builds the distribution and publishes it on
any `v*` tag pushed to this repository; nothing is uploaded by hand and no API
token exists to leak.

That works because PyPI is configured to trust this repository rather than a
credential, which takes two things that must both be in place before the first
tag — and neither fails loudly if it is missing, so check them rather than
assume:

1. On PyPI, a **trusted publisher** for `sigrix-io/sigrix-launcher`, workflow
   `release.yml`, environment `pypi`.
2. In this repository's settings, an **environment named `pypi`**. The
   publisher's claim names it, so a workflow running outside it is refused.

The version lives in two places, `pyproject.toml` and
`src/sigrix_launcher/__init__.py`; move both, and `tests/test_package_metadata.py`
fails while they differ. Then:

```sh
git tag v0.1.0 && git push origin v0.1.0
```

Allow about ten minutes after the upload before expecting `uvx sigrix-launcher`
to resolve the new version: that is the index CDN's cache, not a failed publish.
The release's `verify` job waits it out, installs the new version from PyPI by
name, and fails the run if that never arrives, reports another version, lacks
`py.typed`, or its `sigrix-launcher` command does not answer with that version.
The build job runs the same checks against the wheel before the upload, because
a version on PyPI can never be reused.
