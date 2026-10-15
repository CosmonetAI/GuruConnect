"""Render/container API entrypoint. No shell interpolation or trusted proxy input."""
import asyncio
import os
import sys

import uvicorn


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

    # Psycopg's async connection pool is incompatible with the default
    # ProactorEventLoop on Windows. Render/Linux keeps its normal policy.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    uvicorn.run("recorder.main:app", host="0.0.0.0", port=port, proxy_headers=False,
                access_log=False, timeout_keep_alive=5, timeout_graceful_shutdown=30,
                server_header=False, date_header=True)


if __name__ == "__main__":
    main()
