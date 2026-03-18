"""
LocalMind Launcher — run.py

Usage:
    python run.py              → dev mode (1 worker, auto-reload, DEBUG logging)
    python run.py --prod       → production (auto-detect cores, INFO logging)
    python run.py --workers 4  → custom worker count
    python run.py --test       → run pytest then exit
    python run.py --port 9000  → custom port
"""

import argparse
import os
import signal
import subprocess
import sys
import time


def kill_existing_server(port: int):
    """Kill any existing process on the target port."""
    if os.name == "nt":
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        current_pid = os.getpid()
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                try:
                    pid = int(parts[-1])
                    if pid != current_pid:
                        os.kill(pid, signal.SIGTERM)
                        print(f"  Killed existing server (PID {pid}) on port {port}")
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
    else:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"], capture_output=True, text=True
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    pid = int(pid_str)
                    if pid != os.getpid():
                        os.kill(pid, signal.SIGTERM)
                        print(f"  Killed existing server (PID {pid}) on port {port}")
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
    time.sleep(0.5)


def run_tests():
    """Run the test suite via pytest."""
    print("\n🧪 Running tests...\n")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    return result.returncode


def get_cpu_count():
    """Get a sensible worker count based on CPU cores."""
    cores = os.cpu_count() or 4
    # Use half the cores (min 2, max 8) — leave room for Ollama
    return max(2, min(cores // 2, 8))


def main():
    parser = argparse.ArgumentParser(description="LocalMind Server Launcher")
    parser.add_argument("--port", type=int, default=8000, help="Port to run on (default: 8000)")
    parser.add_argument("--workers", type=int, default=None, help="Number of uvicorn workers")
    parser.add_argument("--prod", action="store_true", help="Production mode (multi-worker, INFO logging)")
    parser.add_argument("--reload", action="store_true", default=None, help="Enable auto-reload (default in dev)")
    parser.add_argument("--no-reload", action="store_true", help="Disable auto-reload")
    parser.add_argument("--test", action="store_true", help="Run tests and exit")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser on start")
    args = parser.parse_args()

    # Run tests and exit
    if args.test:
        exit_code = run_tests()
        sys.exit(exit_code)

    # Determine settings
    port = args.port
    is_prod = args.prod

    if args.workers:
        workers = args.workers
    elif is_prod:
        workers = get_cpu_count()
    else:
        workers = 1

    if args.no_reload:
        reload_enabled = False
    elif args.reload is not None:
        reload_enabled = args.reload
    else:
        reload_enabled = not is_prod and workers == 1  # reload only works with 1 worker

    log_level = "info" if is_prod else "debug"

    # Banner
    mode = "PRODUCTION" if is_prod else "DEVELOPMENT"
    print(f"""
╔══════════════════════════════════════════╗
║         🧠 LocalMind Server             ║
╠══════════════════════════════════════════╣
║  Mode:     {mode:<30}║
║  Port:     {port:<30}║
║  Workers:  {workers:<30}║
║  Reload:   {str(reload_enabled):<30}║
║  Log:      {log_level:<30}║
╚══════════════════════════════════════════╝
""")

    # Kill any existing server on target port
    kill_existing_server(port)

    # Open browser (dev mode only, unless --no-browser)
    if not args.no_browser and not is_prod:
        import webbrowser
        import threading
        def open_browser():
            time.sleep(2)  # Give server time to start
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser, daemon=True).start()

    # Launch uvicorn
    import uvicorn

    uvicorn_kwargs = {
        "app": "backend.server:app",
        "host": "0.0.0.0",
        "port": port,
        "workers": workers if workers > 1 else None,
        "reload": reload_enabled,
        "log_level": log_level,
    }

    # In reload mode, watch specific directories
    if reload_enabled:
        uvicorn_kwargs["reload_dirs"] = ["backend", "frontend"]
        uvicorn_kwargs["reload_includes"] = ["*.py", "*.html", "*.js", "*.css"]

    # Remove None values
    uvicorn_kwargs = {k: v for k, v in uvicorn_kwargs.items() if v is not None}

    uvicorn.run(**uvicorn_kwargs)


if __name__ == "__main__":
    main()
