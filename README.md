# sigrix-launcher

Start an MCP server you bought on [Sigrix](https://sigrix.io) from the client you already use — Claude Code, Claude Desktop, Cursor, or any MCP client that starts a server over stdio.

Some Sigrix listings are delivered by Sigrix rather than installed from a public registry: the seller's server is a package only its buyers can download. This launcher is what your client starts for one. On every start it:

1. asks Sigrix whether your token owns the listing;
2. downloads the seller's package and verifies it before anything is written;
3. installs it into an environment of its own, once per version;
4. starts the seller's server on its own stdio, so your client talks to the server directly.

It speaks [Postern](https://github.com/sigrix-io/postern), the open protocol for checking a purchase and fetching what was bought, through the client code of the runner every Sigrix bundle carries, [`sigrix-runtime`](https://github.com/sigrix-io/sigrix-runtime).

## Configure your client

The listing's page shows this with its identifier filled in, once you own it — copy it from there. `<seller>/<listing-id>` below is that identifier, and `YOUR_SIGRIX_TOKEN` is where your token goes.

**Claude Code**

```sh
claude mcp add modelwatch -e SIGRIX_TOKEN=YOUR_SIGRIX_TOKEN -- uvx sigrix-launcher run <seller>/<listing-id>
```

**Claude Desktop** (`claude_desktop_config.json`) and **Cursor** (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "modelwatch": {
      "command": "uvx",
      "args": [
        "sigrix-launcher",
        "run",
        "<seller>/<listing-id>"
      ],
      "env": {
        "SIGRIX_TOKEN": "YOUR_SIGRIX_TOKEN"
      }
    }
  }
}
```

`uvx` comes with [uv](https://pypi.org/project/uv/). `pipx run sigrix-launcher run <seller>/<listing-id>` works as well. Python 3.11 or newer.

Arguments after the identifier are passed to the seller's server: `... run <seller>/<listing-id> -- --verbose`. Any other variable in the `env` block (an API key the listing asks for, say) reaches the server as it is.

## Your token

`SIGRIX_TOKEN` is a runner token for this listing: create one under **Runner tokens** on your Sigrix account's plugins page, <https://sigrix.io/account/plugins>, one for each machine. It covers this one listing, and revoking it there stops this configuration and nothing else. The plugin feed token on the same page works too and covers everything you bought, but regenerating it replaces it everywhere at once.

The launcher sends it only in the `Authorization` header of its two requests to Sigrix, never writes it to disk, and starts the seller's server without it — nor any other `SIGRIX_` or `POSTERN_` variable.

## When it does not start

It prints one sentence on stderr saying why and exits; your client shows it in the server's log.

- **Not owned.** The token has not bought this listing, the purchase was refunded, or the token has since been regenerated. Sigrix answers all three the same way on purpose, so the message names all three. Nothing is downloaded.
- **Offline.** If Sigrix confirmed your purchase within its grace period (a day, for Sigrix) and a version is already installed here, that version starts. Otherwise the launcher asks you to reconnect.
- **Nothing to install yet.** The seller's newest version may still be waiting for Sigrix's review, or a version may have been withdrawn. A withdrawn version is not started from the cache either.
- **Withdrawn listing.** Buyers of a listing its seller withdrew keep access for a year; after that, the message gives the date it ended.

A refund takes effect at the next start: the purchase is checked when the server starts, not while it runs.

## What it keeps on your machine

Everything is under one folder: `~/.cache/sigrix-launcher` on Linux, `~/Library/Caches/sigrix-launcher` on macOS, `%LOCALAPPDATA%\sigrix-launcher` on Windows. `SIGRIX_LAUNCHER_HOME` moves it. For each listing: every version you have started, each in its own environment, and Sigrix's last answer about your purchase, stored with a fingerprint of the token rather than the token. Delete the folder whenever you like; the next start downloads again.

## Variables

| Variable | |
| --- | --- |
| `SIGRIX_TOKEN` | Required. A runner token for this listing, from the plugins page. |
| `SIGRIX_LAUNCHER_HOME` | Optional. Where installed versions are kept. |
| `POSTERN_DISTRIBUTOR` | Optional; defaults to `https://sigrix.io`. For testing against another distributor. Plain `http://` is accepted only to a loopback address. |

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

The tests run a distributor on loopback and, for one of them, install a real wheel and talk to the started server over a real pipe. The purchase check and the verified download are [`sigrix-runtime`](https://github.com/sigrix-io/sigrix-runtime)'s, pinned exactly in `pyproject.toml`: a change to either is made there, and reaches buyers when a release here moves the pin.

## Licence

Apache-2.0. The Sigrix name and logo are not covered by the licence — see `NOTICE`.
