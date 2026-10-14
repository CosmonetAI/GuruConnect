"""Render/container API entrypoint. No shell interpolation or trusted proxy input."""
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
    except Exception:
        print("API startup rejected: verify backend configuration and PORT", file=sys.stderr)
        raise SystemExit(1) from None
    uvicorn.run("recorder.main:app", host="0.0.0.0", port=port, proxy_headers=False,
                access_log=False, timeout_keep_alive=5, timeout_graceful_shutdown=30,
                server_header=False, date_header=True)


if __name__ == "__main__":
    main()
