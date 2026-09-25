"""``python -m sigrix_launcher`` — the same command as the ``sigrix-launcher`` script."""

from sigrix_launcher.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
