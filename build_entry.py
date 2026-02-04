"""
Entry point script for the frozen PyInstaller application.

This script acts as the bootstrap loader for the Riemann application when
packaged as a standalone executable. It imports the main run function
from the application package and executes it.
"""

from riemann.app import run

if __name__ == "__main__":
    run()
