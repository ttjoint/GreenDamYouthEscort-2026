"""``python -m green_dam`` - same entry point as the ``green-dam`` script."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
