"""
Entry for a Nuitka build of the session driver (scripts/nbuild.sh --sessions).

Importing build_entry sets the build up as it sets up the release executable:
the crash log, the Qt paths, and the flags that turn this same binary into a
render, convert or AI child. Then user_session.main plays the scenario, so a
scenario runs through the same compiled Riemann as a release. Point
tests/test_user_sessions.py at the result with RIEMANN_SESSION_DRIVER.
"""

import user_session

import build_entry  # noqa: F401

if __name__ == "__main__":
    user_session.main()
