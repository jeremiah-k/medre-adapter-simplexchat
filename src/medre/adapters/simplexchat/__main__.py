"""Run operator commands only when invoked as a module entrypoint."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
