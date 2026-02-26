#!/usr/bin/env bash

# Configuration
VERSION="1.2"  # ← update this if a newer release appears
DOWNLOAD_DIR="$HOME/Downloads"
APPDIR="$HOME/.local/share/applications"
APP_BIN="$APPDIR/waypaper-y"
DESKTOP_FILE="$APPDIR/wpy.desktop"
CONFIG_FILE="$HOME/.config/hypr/hyprland.conf"  # mentioned but never used — kept for your original intent

echo ""
echo "Warning:"
echo "  This tool will:"
echo "  • Download files to $DOWNLOAD_DIR"
echo "  • Place waypaper-y in $APPDIR"
echo "  • Create $DESKTOP_FILE"
echo "  • Install wpydynamic to /usr/local/bin/ (requires sudo)"
echo "  • Note: it mentions $CONFIG_FILE but does not modify it"
echo ""
read -rp "Do you want to continue? (y/N): " answer
case "$answer" in
  [yY]|[yY][eE][sS])
    echo -e "\nContinuing...\n"
    ;;
  *)
    echo -e "\nCancelled."
    exit 0
    ;;
esac

# Check for wget
if ! command -v wget >/dev/null 2>&1; then
  echo "Error: wget not found. Please install it (e.g. sudo apt install wget / sudo pacman -S wget)."
  exit 1
fi

# Ensure download directory exists
mkdir -p "$DOWNLOAD_DIR" || { echo "Failed to create $DOWNLOAD_DIR"; exit 1; }

echo -e "\nDownloading waypaper-y v${VERSION}..."
wget -O "$DOWNLOAD_DIR/waypaper-y" \
  "https://github.com/rpd762/waypaper-y/releases/download/${VERSION}/waypaper-y" \
  || { echo "Download failed — check URL, network or if version ${VERSION} exists."; exit 1; }

echo -e "\nDownloading wpydynamic..."
wget -O "$DOWNLOAD_DIR/wpydynamic" \
  "https://github.com/rpd762/waypaper-y/releases/download/${VERSION}/wpydynamic" \
  || { echo "Download failed."; exit 1; }

# Make executables
chmod +x "$DOWNLOAD_DIR/waypaper-y" "$DOWNLOAD_DIR/wpydynamic" \
  || { echo "chmod failed."; exit 1; }

# Prepare app directory
mkdir -p "$APPDIR" || { echo "Failed to create $APPDIR"; exit 1; }

# Handle existing waypaper-y binary
if [ -e "$APP_BIN" ]; then
  echo "Warning: $APP_BIN already exists."
  read -rp "Overwrite it? (y/N): " ovr
  case "$ovr" in
    [yY]|[yY][eE][sS]) mv -f "$APP_BIN" "$APP_BIN.bak" ;;
    *) echo "Keeping old file — installation cancelled."; exit 0 ;;
  esac
fi

mv "$DOWNLOAD_DIR/waypaper-y" "$APP_BIN" \
  || { echo "Failed to move waypaper-y."; exit 1; }

echo -e "\nCreating desktop file..."
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=${VERSION}
Name=Waypaper - Yurz Edition v${VERSION}
Comment=Custom wallpaper setter (Yurz ver)
Exec=${APP_BIN}
Terminal=false
Categories=Utility;Settings;GTK;
EOF

# wpydynamic — system-wide install
echo -e "\nMoving wpydynamic to /usr/local/bin/ (sudo required)..."

if [ -e "/usr/local/bin/wpydynamic" ]; then
  echo "Warning: /usr/local/bin/wpydynamic already exists."
  read -rp "Overwrite it? (y/N): " ovr
  case "$ovr" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Skipping wpydynamic install."; sudo rm -f "$DOWNLOAD_DIR/wpydynamic"; exit 0 ;;
  esac
fi

sudo mv "$DOWNLOAD_DIR/wpydynamic" /usr/local/bin/ \
  || { echo "sudo mv failed — check permissions or disk space."; sudo rm -f "$DOWNLOAD_DIR/wpydynamic"; exit 1; }

# Cleanup leftover file if any
rm -f "$DOWNLOAD_DIR/wpydynamic" 2>/dev/null

echo -e "\nDone!"
echo "• waypaper-y is now at:          ${APP_BIN}"
echo "• Desktop entry created at:     ${DESKTOP_FILE}"
echo "• wpydynamic installed to:      /usr/local/bin/wpydynamic"
echo ""
echo "You may need to run the following for the menu to update:"
echo "  update-desktop-database ~/.local/share/applications"
echo "  # or just log out / restart your session"
echo ""
echo "Enjoy Waypaper-Y v${VERSION}!"
