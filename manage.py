#!/usr/bin/env python3
import os
import sys

def runserver():
    from app import app
    # 6000 is on browsers' unsafe-port blocklist (X11); 5050 avoids that
    # and the macOS AirPlay listener on 5000.
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "runserver"
    if cmd == "runserver":
        runserver()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
