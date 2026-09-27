# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the versioning is
described in `VERSIONING.md`.

## [Unreleased]

### Changed

- The purchase check and the verified download come from the `sigrix-runtime`
  package, pinned at 0.1.0, instead of a copy of its client code carried in
  `sigrix_launcher._postern`. Its code is the copy's, so nothing a buyer sees
  changes; a start now installs that one package beside the launcher, and it
  has no dependencies of its own.

## [0.1.0] — 2026-09-23

First release, reading package format `1`.

### Added

- `sigrix-launcher run <seller>/<listing-id>`: check the purchase, fetch and
  verify the package, install it into an environment of its own and start the
  seller's server on stdio. Arguments after the identifier go to the server.
- A cache per listing: each version started, and the last purchase answer
  (with a fingerprint of the token), so a start offline inside the grace period
  uses the version installed last.
- The Postern reference runner's client code, copied in, for the purchase check
  and the verified download.
