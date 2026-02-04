#!/bin/bash
set -e

# -----------------------------------------------------------------------------
# Script Name: install_shortcut.sh
# Description: Generates a Linux .desktop file for Riemann and installs it
#              to the user's local applications folder.
# -----------------------------------------------------------------------------

# --- Configuration ---
APP_NAME="Riemann"
APP_EXEC="dist/Riemann"
APP_ICON="python-app/riemann/assets/icon.ico"
DESKTOP_FILE_NAME="Riemann.desktop"
INSTALL_DIR="$HOME/.local/share/applications"

# --- Main Script ---

# 1. Get the absolute path of the project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXEC_PATH="$PROJECT_ROOT/$APP_EXEC"
ICON_PATH="$PROJECT_ROOT/$APP_ICON"

echo "Installing Desktop Shortcut for $APP_NAME..."

# 2. Verify files exist
if [ ! -f "$EXEC_PATH" ]; then
    echo "Error: Executable not found at $EXEC_PATH"
    echo "Please run './build.sh' first to generate the executable."
    exit 1
fi

if [ ! -f "$ICON_PATH" ]; then
    echo "Warning: Icon not found at $ICON_PATH"
    echo "The shortcut will be created, but the icon might be missing."
fi

# 3. Create the .desktop file content
DESKTOP_CONTENT="[Desktop Entry]
Type=Application
Name=$APP_NAME
GenericName=PDF Reader
Comment=A standalone PDF reader and manager
Exec=$EXEC_PATH
Icon=$ICON_PATH
Terminal=false
Categories=Office;Viewer;Utility;
Keywords=pdf;reader;document;
StartupWMClass=$APP_NAME"

# 4. Write the file to the applications directory
OUTPUT_FILE="$INSTALL_DIR/$DESKTOP_FILE_NAME"

echo "Generating $OUTPUT_FILE..."
echo "$DESKTOP_CONTENT" > "$OUTPUT_FILE"

# 5. Make it executable
chmod +x "$OUTPUT_FILE"

# 6. Update the desktop database
echo "Updating desktop database..."
update-desktop-database "$INSTALL_DIR" || echo "Note: 'update-desktop-database' command not found, but shortcut might still work."

echo "-------------------------------------------------------"
echo "Success! Riemann should now appear in your app launcher."
echo "-------------------------------------------------------"