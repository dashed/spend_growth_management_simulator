#!/usr/bin/env python3
"""
Test runner for SGM simulator test suite
Runs all tests and provides summary
"""

import sys
import subprocess
from pathlib import Path


def run_test_file(test_file: Path) -> tuple[bool, str]:
    """Run a single test file and return success status and output"""
    print(f"\n{'='*60}")
    print(f"Running {test_file.name}")
    print('='*60)
    
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v"],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        return result.returncode == 0, result.stdout
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT: {test_file.name} took too long to run")
        return False, "Timeout"
    except Exception as e:
        print(f"ERROR running {test_file.name}: {e}")
        return False, str(e)


def main():
    """Run all test files"""
    print("SGM Simulator Test Suite")
    print("="*60)
    
    # Find all test files
    test_dir = Path(__file__).parent
    test_files = sorted(test_dir.glob("test_*.py"))
    
    if not test_files:
        print("No test files found!")
        return 1
    
    print(f"Found {len(test_files)} test files:")
    for tf in test_files:
        print(f"  - {tf.name}")
    
    # Run each test file
    results = {}
    for test_file in test_files:
        success, output = run_test_file(test_file)
        results[test_file.name] = success
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for success in results.values() if success)
    failed = len(results) - passed
    
    for test_file, success in results.items():
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{status} {test_file}")
    
    print(f"\nTotal: {len(results)} test files")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    
    if failed > 0:
        print("\n⚠️  Some tests failed. Check output above for details.")
        return 1
    else:
        print("\n✅ All tests passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())