#!/usr/bin/env bash
# Remove the field hotspot and go back to normal Wi-Fi.
sudo nmcli connection delete thermal-hotspot || true
echo "Hotspot removed. Pi will rejoin known Wi-Fi networks."
