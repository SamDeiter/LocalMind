#!/usr/bin/env python3
"""
LocalMind Test Runner

Usage:
    python run_tests.py              # run all tests
    python run_tests.py --unit       # run only unit-marked tests
    python run_tests.py --integration # run only integration-marked tests
    python run_tests.py --slow       # run only slow-marked tests
    python run_tests.py --coverage   # run all tests with coverage reporting
"""
import argparse
import subprocess
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalMind test runner")
    parser.add_argument("--unit", action="store_true", help="Run only unit tests")
    parser.add_argument("--integration", action="store_true", help="Run only integration tests")
    parser.add_argument("--slow", action="store_true", help="Run only slow tests")
    parser.add_argument("--coverage", action="store_true", help="Enable coverage reporting")
    parser.add_argument("paths", nargs="*", default=["backend/tests"], help="Test paths (default: backend/tests)")
    args = parser.parse_args()

    # Build the pytest command
    cmd = [sys.executable, "-m", "pytest"]
    cmd.extend(args.paths)
    cmd.extend(["-v", "--tb=short"])

    # Marker filters
    markers = []
    if args.unit:
        markers.append("unit")
    if args.integration:
        markers.append("integration")
    if args.slow:
        markers.append("slow")
    if markers:
        cmd.extend(["-m", " or ".join(markers)])

    # Coverage
    if args.coverage:
        cmd.extend([
            "--cov=backend",
            "--cov-report=term-missing",
            "--cov-report=html:htmlcov",
        ])

    # Print what we are running
    print("=" * 70)
    print("LocalMind Test Runner")
    print("=" * 70)
    print(f"Command: {' '.join(cmd)}")
    print(f"Filters: {', '.join(markers) if markers else 'none (all tests)'}")
    print(f"Coverage: {'enabled' if args.coverage else 'disabled'}")
    print("=" * 70)
    print()

    start = time.time()
    result = subprocess.run(cmd)
    elapsed = time.time() - start

    # Summary
    print()
    print("=" * 70)
    if result.returncode == 0:
        print(f"ALL TESTS PASSED  ({elapsed:.1f}s)")
    elif result.returncode == 5:
        print(f"NO TESTS COLLECTED  ({elapsed:.1f}s)")
        print("Hint: the marker filter may not match any tests.")
    else:
        print(f"TESTS FAILED  (exit code {result.returncode}, {elapsed:.1f}s)")
    print("=" * 70)

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
