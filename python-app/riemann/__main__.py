"""
Package Entry Point.

Allows the package to be executed directly using `python -m riemann`.
"""

from .app import run

if __name__ == "__main__":
    run()
