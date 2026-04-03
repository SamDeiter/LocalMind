
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).resolve().parent))

from backend.validation.robust_parser import parse_json

def test_robust_parser():
    print("Testing Robust Parser...")
    bad_json = "Here is the result: ```json\n{ \"title\": \"Fix Bug\", \"category\": \"bugfix\" } \n```"
    result = parse_json(bad_json)
    if result and result["title"] == "Fix Bug":
        print("✅ Robust Parser: Passed basic markdown extraction.")
    else:
        print("❌ Robust Parser: Failed.")

if __name__ == "__main__":
    test_robust_parser()
