"""
run.py — Single-command launcher for the Polyglot Voice Agent.

Usage (from the project root):
    python run.py

Options:
    --host   Bind address  (default: 0.0.0.0)
    --port   Port number   (default: 8000)
    --no-reload  Disable auto-reload on code changes

The FastAPI backend serves both the API and the browser frontend
(static files at /static). No separate frontend process is needed.
"""
import argparse
import sys
from pathlib import Path

# Make `backend/` importable as a flat package (same as running from inside it)
sys.path.insert(0, str(Path(__file__).parent / "backend"))

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start the Polyglot Voice Agent")
    parser.add_argument("--host",      default="0.0.0.0",  help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port",      default=8000, type=int, help="Port (default: 8000)")
    parser.add_argument("--no-reload", action="store_true",  help="Disable auto-reload")
    args = parser.parse_args()

    print(f"\n  Polyglot Voice Agent")
    print(f"  ─────────────────────────────────────────")
    print(f"  URL  →  http://localhost:{args.port}")
    print(f"  API  →  http://localhost:{args.port}/health")
    print(f"  ─────────────────────────────────────────\n")

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=[str(Path(__file__).parent / "backend")],
    )
