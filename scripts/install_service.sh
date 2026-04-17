#!/bin/bash
# =============================================================================
# install_service.sh - Install FunctionGemma as a systemd service on RPI5
#
# Usage:
#   sudo bash install_service.sh [model_path]
#
# Arguments:
#   model_path  - Path to the GGUF model file (default: ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf)
# =============================================================================

set -euo pipefail

# --- Configuration ---
SERVICE_NAME="functiongemma"
SERVICE_USER="${SUDO_USER:-pi}"
SERVICE_HOME=$(eval echo "~$SERVICE_USER")
WORK_ROOT="$SERVICE_HOME/llama"
LLAMA_SERVER="$WORK_ROOT/llama.cpp/build/bin/llama-server"
MODEL_PATH="${1:-$WORK_ROOT/models/functiongemma-270m-finetuned-q8_0.gguf}"
PORT=8080
CONTEXT_SIZE=1024

# --- Validate ---
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with sudo"
    echo "Usage: sudo bash install_service.sh [model_path]"
    exit 1
fi

if [ ! -f "$LLAMA_SERVER" ]; then
    echo "Error: llama-server not found at $LLAMA_SERVER"
    echo "Please run setup_rpi5.sh first to build llama.cpp"
    exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
    echo "Error: Model file not found at $MODEL_PATH"
    echo "Please copy your GGUF model to $WORK_ROOT/models/ first"
    echo ""
    echo "Available models:"
    ls -la "$WORK_ROOT/models/"*.gguf 2>/dev/null || echo "  (none found)"
    exit 1
fi

echo "Installing FunctionGemma service..."
echo "  User:    $SERVICE_USER"
echo "  Server:  $LLAMA_SERVER"
echo "  Model:   $MODEL_PATH"
echo "  Port:    $PORT"
echo "  Context: $CONTEXT_SIZE"
echo ""

# --- Create systemd service ---
cat > /etc/systemd/system/${SERVICE_NAME}.service << SERVICEEOF
[Unit]
Description=FunctionGemma LLM Server (llama.cpp)
After=network.target
Documentation=https://github.com/ggml-org/llama.cpp

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$WORK_ROOT
ExecStart=$LLAMA_SERVER \\
    -m $MODEL_PATH \\
    -c $CONTEXT_SIZE \\
    --host 0.0.0.0 \\
    --port $PORT
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Resource limits
LimitNOFILE=65536
MemoryMax=2G

[Install]
WantedBy=multi-user.target
SERVICEEOF

# --- Enable and start ---
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo ""
echo "Service installed and started!"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status $SERVICE_NAME    # Check status"
echo "  sudo systemctl stop $SERVICE_NAME      # Stop"
echo "  sudo systemctl restart $SERVICE_NAME   # Restart"
echo "  sudo journalctl -u $SERVICE_NAME -f    # View logs"
echo ""
echo "Test the API:"
echo "  curl http://localhost:$PORT/health"
echo ""
