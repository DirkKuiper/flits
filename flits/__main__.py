"""Allow ``python -m flits`` to run the command line interface."""

from flits.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
