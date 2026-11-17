"""
Options for starting child processes.

NO_WINDOW keeps a console program (ffmpeg, Tesseract, pip, Python) from
opening a console window when the windowed Windows build starts it; pass it
to subprocess as **NO_WINDOW. It is empty on other systems.

No Qt imports: the render and AI child processes use this module.
"""

import os
import subprocess

NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
