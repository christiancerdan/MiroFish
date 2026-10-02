"""Run the production WSGI server in one process."""

import os
import sys


def main():
    from app import create_app
    from app.config import Config

    errors = Config.validate()
    if errors:
        print("Configuration errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)

    from waitress import serve

    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5001"))
    threads = int(os.environ.get("WAITRESS_THREADS", "8"))

    application = create_app()
    application.debug = False
    # Background simulations share process memory; use threads, not worker processes.
    serve(application, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
