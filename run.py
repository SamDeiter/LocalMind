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
    """Kill any existing process on the target port.
    
    Also cleans up stale Python processes that may have been orphaned
    from previous server runs, dev scripts, or crashed terminals.
    """
    killed = 0

    if os.name == "nt":
        # Windows: find PIDs listening on our port
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        current_pid = os.getpid()
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                try:
                    pid = int(parts[-1])
                    if pid != current_pid:
                        os.kill(pid, signal.SIGTERM)
                        killed += 1
                        print(f"  Killed server on port {port} (PID {pid})")
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
    else:
        # Unix/macOS: use lsof to find port holders
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"], capture_output=True, text=True
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    pid = int(pid_str)
                    if pid != os.getpid():
                        os.kill(pid, signal.SIGTERM)
                        killed += 1
                        print(f"  Killed server on port {port} (PID {pid})")
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
    time.sleep(0.5)
    return killed


def cleanup_stale_python(max_age_seconds: int = 1800):
    """Kill orphaned Python processes older than max_age_seconds (default: 30 min).
    
    Detects stale 'python -c' processes that were started from this project
    directory but never properly terminated (e.g., from crashed terminals
    or abandoned commands). These orphans consume RAM and can cause
    port conflicts.
    
    Only kills processes whose command line contains 'python -c' and
    our project path, to avoid killing unrelated Python processes.
    
    Args:
        max_age_seconds: Processes older than this are considered stale.
    """
    if os.name != "nt":
        return 0  # Only implemented for Windows currently

    try:
        # Use WMIC to find Python processes with their creation time and command line
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get",
             "ProcessId,CommandLine,CreationDate", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=10,
        )

        killed = 0
        current_pid = os.getpid()
        project_dir = os.path.dirname(os.path.abspath(__file__)).lower()

        for line in result.stdout.strip().splitlines():
            if not line.strip() or "ProcessId" in line or "Node" in line:
                continue
            parts = line.strip().split(",")
            if len(parts) < 4:
                continue

            try:
                cmd_line = ",".join(parts[1:-2]).lower()  # Command line (may contain commas)
                pid = int(parts[-1].strip())

                # Only kill 'python -c' processes from our project directory
                if pid == current_pid:
                    continue
                if "python" not in cmd_line or "-c" not in cmd_line:
                    continue
                if project_dir not in cmd_line and "localmind" not in cmd_line:
                    continue

                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ValueError, ProcessLookupError, PermissionError):
                continue

        if killed:
            print(f"  🧹 Cleaned up {killed} stale Python process(es)")
        return killed
    except Exception:
        return 0  # Non-fatal — don't block startup


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

    # ── Auto-Increment Build Number ─────────────────────────
    # Every server start bumps the build counter in version.json.
    # This makes it easy to track which build a user is running.
    try:
        from scripts.bump_build import bump
        build_info = bump()
        build_display = f"v{build_info['version']} (build #{build_info['build']})"
    except Exception as e:
        print(f"  ⚠ Build bump skipped: {e}")
        build_display = "unknown"

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
║  Build:    {build_display:<30}║
║  Mode:     {mode:<30}║
║  Port:     {port:<30}║
║  Workers:  {workers:<30}║
║  Reload:   {str(reload_enabled):<30}║
║  Log:      {log_level:<30}║
╚══════════════════════════════════════════╝
""")

    # Clean up stale Python processes from previous sessions
    cleanup_stale_python()

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
