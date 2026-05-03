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

# Defense in depth: Windows worker subprocesses default to cp1252 stdout,
# so any non-ASCII byte in a log message takes down the logging handler.
# We keep logs ASCII-only as a rule, but set UTF-8 here so a stray char
# from a dependency can't kill the server.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def kill_existing_server(port: int):
    """Kill any existing process on the target port.

    Robust against:
      - Stale netstat entries on Windows (PID listed but process gone)
      - Processes that ignore SIGTERM (Windows uvicorn workers)
      - Multiple servers bound to the same port (rare but possible)

    The strategy is: identify candidate PIDs three ways, kill them with the
    OS-native force-kill, then BLOCK until the port is free or 5s elapsed.
    """
    candidates: set[int] = set()
    current_pid = os.getpid()

    if os.name == "nt":
        # Source 1: netstat -ano (may include stale entries)
        try:
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    try:
                        candidates.add(int(line.split()[-1]))
                    except ValueError:
                        pass
        except Exception as exc:
            print(f"  netstat lookup failed: {exc}")

        # Source 2: any python.exe whose command line includes run.py + same port arg
        try:
            wmic = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get",
                 "ProcessId,CommandLine", "/FORMAT:CSV"],
                capture_output=True, text=True, timeout=10,
            )
            for line in wmic.stdout.splitlines():
                low = line.lower()
                if "run.py" in low and f"--port {port}" in low.replace("=", " "):
                    parts = line.strip().split(",")
                    try:
                        candidates.add(int(parts[-1].strip()))
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass  # WMIC is being deprecated on newer Windows; netstat is enough

        candidates.discard(current_pid)
        candidates.discard(0)

        # Force-kill each candidate via taskkill (more reliable than os.kill on Windows)
        for pid in candidates:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True, timeout=5,
                )
                print(f"  Killed PID {pid} on port {port}")
            except Exception as exc:
                print(f"  taskkill PID {pid} failed: {exc}")
    else:
        # Unix/macOS: lsof gives us the live PIDs
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=5
            )
            for pid_str in result.stdout.strip().split("\n"):
                if pid_str.strip():
                    try:
                        candidates.add(int(pid_str))
                    except ValueError:
                        pass
        except Exception:
            pass

        candidates.discard(current_pid)
        for pid in candidates:
            try:
                os.kill(pid, signal.SIGKILL)
                print(f"  Killed PID {pid} on port {port}")
            except (ProcessLookupError, PermissionError):
                pass

    # Block until the port is actually free (max 5s). Without this we can race
    # the new server's bind() against the OS releasing the socket.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _is_port_listening(port):
            break
        time.sleep(0.25)
    else:
        print(f"  WARNING: port {port} still LISTENING after 5s — bind may fail")

    return len(candidates)


def _is_port_listening(port: int) -> bool:
    """True if the OS reports an actual (non-stale) listener on `port`."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=3
        )
    except Exception:
        return False
    for line in result.stdout.splitlines():
        if f":{port}" not in line or "LISTENING" not in line:
            continue
        try:
            pid = int(line.split()[-1])
        except ValueError:
            continue
        # On Windows, verify the PID actually exists. taskkill returns
        # error code 128 for non-existent PIDs — we treat that as "stale".
        if os.name == "nt":
            check = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=3,
            )
            if "No tasks" in check.stdout or pid <= 0:
                continue  # phantom entry; ignore
        return True
    return False


def cleanup_stale_python():
    """Kill stale or orphaned LocalMind processes.
    
    Searches for all 'python.exe' processes where the command line contains
    'run.py', 'backend.server', or 'uvicorn' and related project paths.
    """
    if os.name != "nt":
        return 0

    try:
        # Get process list with command lines
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get",
             "ProcessId,CommandLine", "/FORMAT:CSV"],
            capture_output=True, text=True, timeout=10,
        )

        killed = 0
        current_pid = os.getpid()
        project_dir = os.path.dirname(os.path.abspath(__file__)).lower()

        for line in result.stdout.strip().splitlines():
            if not line.strip() or "ProcessId" in line or "Node" in line:
                continue
            
            parts = line.strip().split(",")
            if len(parts) < 3:
                continue

            try:
                # Command line is parts[1:-1] joined (to handle internal commas)
                cmd_line = ",".join(parts[1:-1]).lower()
                pid = int(parts[-1].strip())

                if pid == current_pid:
                    continue
                
                # Keywords that identify a LocalMind related process
                is_localmind = any(kw in cmd_line for kw in ["run.py", "backend.server", "uvicorn", "localmind"])
                in_project = project_dir in cmd_line or "localmind" in cmd_line
                
                if is_localmind and in_project:
                    print(f"  Killing stale process {pid}: {cmd_line[:60]}...")
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
            except (ValueError, ProcessLookupError, PermissionError):
                continue

        if killed:
            print(f"  🧹 Cleaned up {killed} stale LocalMind process(es)")
        return killed
    except Exception as e:
        print(f"  ⚠ Cleanup error: {e}")
        return 0


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
        "timeout_graceful_shutdown": 5,
    }

    # In reload mode, watch specific directories
    if reload_enabled:
        uvicorn_kwargs["reload_dirs"] = ["backend", "frontend"]
        uvicorn_kwargs["reload_includes"] = ["*.py", "*.html", "*.js", "*.css"]
        # Exclude files the autonomy engine edits to prevent reload loops:
        # The engine edits backend/tools/*.py, runs tests, and needs the server
        # to stay alive during that cycle. Also exclude binary DB files.
        uvicorn_kwargs["reload_excludes"] = [
            "backend/tools/*",
            "backend/memory_db/*",
            "*.bak",
            "__pycache__/*",
        ]

    # Remove None values
    uvicorn_kwargs = {k: v for k, v in uvicorn_kwargs.items() if v is not None}

    uvicorn.run(**uvicorn_kwargs)


if __name__ == "__main__":
    main()
