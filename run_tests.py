#!/usr/bin/env python3
"""Test runner for AI Paper Trader."""

import subprocess
import sys
import os

def run_tests():
    """Run all tests with coverage and verbose output."""
    os.environ.setdefault('ANTHROPIC_API_KEY', 'test-key')

    cmd = [
        sys.executable, '-m', 'pytest',
        'tests/',
        '-v',
        '--tb=short',
        '--color=yes'
    ]

    print(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode

if __name__ == '__main__':
    exit_code = run_tests()
    sys.exit(exit_code)