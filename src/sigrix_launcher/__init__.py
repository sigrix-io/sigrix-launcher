"""Start a Sigrix-delivered MCP server you bought, from the MCP client you already use.

What happens on every start, in order:

1. **The purchase is checked.** ``SIGRIX_TOKEN`` is presented to the
   distributor's entitlement check, through the Postern runner's own client
   (``sigrix_runtime.postern.entitlement``, from the ``sigrix-runtime``
   package): its declared staleness window, its grace when the distributor
   cannot be reached, and a ``404`` as a final answer.
2. **The package is fetched and verified.** The seller's wheel and its
   manifest arrive as one zip, held in memory until its ``Repr-Digest``
   agrees, then checked again against the checksum in the manifest. Nothing
   is written before both agree, and a version already installed is reused.
3. **It is installed into an environment of its own** — one per version,
   under the launcher's cache — with ``uv`` when it is on the path and
   ``venv`` and ``pip`` otherwise.
4. **The seller's server is started on this process's stdio**, with the
   launcher's own variables removed from its environment.

The launcher writes nothing to stdout: that stream is the MCP connection. It
never writes the token anywhere; its cache holds a fingerprint of it, so a
rotated token is a new question rather than an old answer.
"""

from sigrix_runtime.postern.entitlement import DELIVERY_MODE_CONNECTOR_LOCAL

__version__ = "0.1.0"

#: The file beside the wheel in a served package, and the only two values of it
#: this launcher can read. A newer format is refused by name rather than guessed
#: at — upgrading the launcher is the fix, and the message says so.
MANIFEST_FILENAME = "sigrix-package.json"
MANIFEST_KIND = "sigrix-mcp-package"
SUPPORTED_MANIFEST_FORMAT = 1

#: How the launcher names itself on the entitlement check, so the distributor
#: can tell a package launched on a buyer's machine from a runner bundle. It is
#: the runner's own name for that shape, so the two cannot spell it differently.
DELIVERY_MODE = DELIVERY_MODE_CONNECTOR_LOCAL

__all__ = [
    "DELIVERY_MODE",
    "MANIFEST_FILENAME",
    "MANIFEST_KIND",
    "SUPPORTED_MANIFEST_FORMAT",
    "__version__",
]
