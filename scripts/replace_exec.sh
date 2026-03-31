#!/bin/bash

FILES=(
    "$HOME/.local/share/applications/Riemann.desktop"
    "$HOME/Desktop/Riemann.desktop"
)

TEMPLATE='[Desktop Entry]
Type=Application
Name=Riemann
GenericName=PDF Reader
Comment=A standalone PDF reader and manager
Exec=/home/shadow30812/Downloads/Riemann
Icon=/home/shadow30812/.local/share/icons/riemann.png
Terminal=false
Categories=Office;Viewer;Utility;
StartupWMClass=Riemann
MimeType=application/pdf;'

for FILE in "${FILES[@]}"; do
    DIR=$(dirname "$FILE")

    mkdir -p "$DIR"

    if [[ -f "$FILE" ]]; then
        sed -i.bak 's|^Exec=.*|Exec=/home/shadow30812/Downloads/Riemann|' "$FILE"
        echo "$FILE: Exec line updated"
    else
        printf "%s\n" "$TEMPLATE" > "$FILE"
        echo "$FILE: Created from template"
    fi

    chmod +x "$FILE"
    update-desktop-database ~/.local/share/applications
done