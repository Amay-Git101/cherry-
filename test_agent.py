"""
Run this BEFORE submitting to the hackathon to verify all edge cases.
Usage: python test_agent.py
"""

import requests
import json

BASE_URL = "http://localhost:8000"

TEST_CASES = [
    # (query, expected_output)
    ("What is 10 + 15?",          "The sum is 25."),
    ("What is -10 + 5?",          "The sum is -5."),
    ("Sum of -5 and -5",          "The sum is -10."),
    ("Add 1.5 and 2.5",           "The sum is 4."),
    ("Calculate 100 + 200",       "The sum is 300."),
    ("What is 0 + 0?",            "The sum is 0."),
    ("Sum of -10 and -20",        "The sum is -30."),
    ("What is 7 + 3?",            "The sum is 10."),
    ("Add 2.5 and 7.5",           "The sum is 10."),
    ("What is 50 + 39?",          "The sum is 89."),
]

def run_tests():
    print("=" * 55)
    print("  HACKATHON LEVEL 1 — PRE-SUBMISSION TEST SUITE")
    print("=" * 55)

    passed = 0
    failed = 0

    for query, expected in TEST_CASES:
        payload = {"query": query, "assets": []}
        try:
            resp = requests.post(f"{BASE_URL}/v1/answer", json=payload, timeout=5)
            data = resp.json()
            actual = data.get("output", "")

            if actual == expected:
                print(f"  ✅ PASS | {query}")
                passed += 1
            else:
                print(f"  ❌ FAIL | {query}")
                print(f"          Expected : {expected}")
                print(f"          Got      : {actual}")
                failed += 1

        except Exception as e:
            print(f"  💥 ERROR | {query} → {e}")
            failed += 1

    print("=" * 55)
    print(f"  Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("  🚀 All tests passed! Safe to submit.")
    else:
        print("  ⚠️  Fix failures before submitting!")
    print("=" * 55)

if __name__ == "__main__":
    run_tests()
