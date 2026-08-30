from __future__ import annotations

import argparse

import uvicorn

from litrev.diagnostics import run_checks


def serve() -> None:
    """Run the local HTTP service used by the React interface."""
    uvicorn.run("litrev.api:app", host="127.0.0.1", port=8765, reload=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Litrev local application service")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the local runtime without opening the desktop window",
    )
    args = parser.parse_args()

    if args.check:
        for name, detail in run_checks().items():
            print(f"{name}: {detail}")
        return

    serve()


if __name__ == "__main__":
    main()
