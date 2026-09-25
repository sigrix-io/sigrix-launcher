# Security

## Reporting

Email security@sigrix.io with what you found and how to reproduce it. Do not
open a public issue for a vulnerability. You will get an acknowledgement
within three working days.

## What the launcher holds

One secret: the buyer's token, in `SIGRIX_TOKEN`. It is sent only in the
`Authorization` header of the purchase check and the download, to the
configured distributor, and over plain HTTP only when the connection lands on a
loopback address. It is never written to disk — the cached purchase answer
carries a fingerprint of it — and the seller's server is started without it.

What the launcher installs is the seller's code, as Sigrix reviewed and
approved it. It is verified twice before anything is written: the whole
download against the distributor's `Repr-Digest`, and the wheel against the
checksum in its manifest. It then runs on your machine with your permissions,
in an environment of its own.

If a token leaks, regenerate it on your Sigrix account's plugins page. The old
one stops working on the next start.

## What to report to Sigrix instead

A seller's server doing something its listing does not disclose is a report
about the listing: use the report link on the listing's page, or the same
address above.
