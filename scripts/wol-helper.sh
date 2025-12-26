#!/bin/bash
#
# Wake-on-LAN Helper Script
# This script should be installed on the Docker host where Semaphore runs
# Install: sudo cp wol-helper.sh /usr/local/bin/ && sudo chmod +x /usr/local/bin/wol-helper.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to display usage
usage() {
    echo "Usage: $0 <MAC_ADDRESS> [BROADCAST_IP]"
    echo ""
    echo "Examples:"
    echo "  $0 AA:BB:CC:DD:EE:FF"
    echo "  $0 AA:BB:CC:DD:EE:FF 192.168.1.255"
    exit 1
}

# Check if wakeonlan is installed
if ! command -v wakeonlan &> /dev/null; then
    echo -e "${RED}Error: wakeonlan is not installed${NC}"
    echo "Install it with: sudo apt-get install wakeonlan"
    exit 1
fi

# Check arguments
if [ $# -lt 1 ]; then
    usage
fi

MAC_ADDRESS=$1
BROADCAST_IP=${2:-"255.255.255.255"}

# Validate MAC address format
if ! echo "$MAC_ADDRESS" | grep -qE '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'; then
    echo -e "${RED}Error: Invalid MAC address format${NC}"
    echo "Expected format: AA:BB:CC:DD:EE:FF"
    exit 1
fi

# Send magic packet
echo -e "${GREEN}Sending Wake-on-LAN packet...${NC}"
echo "MAC Address: $MAC_ADDRESS"
echo "Broadcast IP: $BROADCAST_IP"

if wakeonlan -i "$BROADCAST_IP" "$MAC_ADDRESS"; then
    echo -e "${GREEN}Magic packet sent successfully!${NC}"
    exit 0
else
    echo -e "${RED}Failed to send magic packet${NC}"
    exit 1
fi