#!/bin/bash
###############################################################################
#  switch-network.sh — Change Network Priority dynamically
#
#  Usage: sudo ./switch-network.sh [auto|4g|lan|wifi]
#  Description: Updates /etc/device/network.conf and restarts the watchdog.
#  Perfect for calling from Bluetooth API or Web Backend without blocking.
###############################################################################

if [ $# -eq 0 ]; then
    echo "Usage: $0 [auto|4g|lan|wifi]"
    exit 1
fi

MODE="$1"
CONF_FILE="/etc/device/network.conf"

case "$MODE" in
    auto|4g|lan|wifi)
        echo "Switching network mode to: $MODE"
        
        # Replace the NETWORK_MODE line in the config file
        if grep -q "^NETWORK_MODE=" "$CONF_FILE"; then
            sed -i "s/^NETWORK_MODE=.*/NETWORK_MODE=$MODE/" "$CONF_FILE"
        else
            echo "NETWORK_MODE=$MODE" >> "$CONF_FILE"
        fi
        
        echo "Restarting network-watchdog service..."
        systemctl restart network-watchdog
        sleep 3
        echo "Network mode updated successfully."
        ;;
    *)
        echo "Invalid mode. Use: auto, 4g, lan, or wifi."
        exit 1
        ;;
esac
