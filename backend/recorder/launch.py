"""Render/container API entrypoint. No shell interpolation or trusted proxy input."""
import asyncio
import os
import selectors
import sys

import uvicorn


def windows_selector_loop_factory():
    """Create the loop required by psycopg's async pool on Windows."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def main():
    from .config import Settings
    try:
        Settings()  # Validate production secrets/origins before opening a socket.
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError("Invalid port")
    except Exception as error:
        # Keep the validation failure visible in Render logs without printing
        # the values of SecretStr settings.
        print(f"API startup rejected: {error}", file=sys.stderr)
        raise SystemExit(1) from None

    # Psycopg's async connection pool is incompatible with Uvicorn's default
    # ProactorEventLoop on Windows. Uvicorn 0.36+ accepts a loop factory.
    loop = windows_selector_loop_factory if sys.platform == "win32" else "auto"

    uvicorn.run("recorder.main:app", host="0.0.0.0", port=port, proxy_headers=False,
                loop=loop,
                access_log=False, timeout_keep_alive=5, timeout_graceful_shutdown=30,
                server_header=False, date_header=True)


if __name__ == "__main__":
    main()
