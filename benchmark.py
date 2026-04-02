import timeit
import sys
import os

# Ensure backend can be imported
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from backend.logic.token_manager import TokenManager

def generate_test_cases():
    short_text = "This is a short text."
    long_text = "A" * (5 * 1024 * 1024) # 5MB
    massive_text = "A" * (10 * 1024 * 1024) # 10MB
    unicode_text = "Hello 🌍! " * 1000
    empty_text = ""
    none_text = None

    return {
        "Short Text (no truncation)": (short_text, 100),
        "Long Text (5MB, truncate to 1000)": (long_text, 1000),
        "Massive Text (10MB, truncate to 1000)": (massive_text, 1000),
        "Unicode Text (truncate to 100)": (unicode_text, 100),
        "Empty string": (empty_text, 100),
        "None": (none_text, 100)
    }

def run_benchmark():
    cases = generate_test_cases()
    results = {}
    print("Running benchmarks...")
    for name, (text, max_tokens) in cases.items():
        # Setup for timeit
        setup = f"""
from backend.logic.token_manager import TokenManager
text = {repr(text)}
max_tokens = {max_tokens}
"""
        stmt = "TokenManager.truncate_text(text, max_tokens)"

        try:
            # First verify it doesn't crash
            TokenManager.truncate_text(text, max_tokens)

            # Number of iterations depends on the case to keep it reasonable
            number = 10000 if len(str(text)) < 100000 else 1000

            times = timeit.repeat(stmt, setup, number=number, repeat=5)
            min_time = min(times)
            avg_time = sum(times) / len(times)
            results[name] = {"min": min_time, "avg": avg_time, "number": number}
            print(f"{name}: Min time: {min_time:.6f}s, Avg time: {avg_time:.6f}s (over {number} iterations)")
        except Exception as e:
            print(f"{name}: Failed with exception: {e}")
            results[name] = "Failed"

    return results

if __name__ == "__main__":
    run_benchmark()
