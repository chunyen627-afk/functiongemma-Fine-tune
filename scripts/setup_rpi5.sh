#!/bin/bash
# =============================================================================
# setup_rpi5.sh - One-click setup for llama.cpp on Raspberry Pi 5
#
# Usage:
#   chmod +x setup_rpi5.sh
#   ./setup_rpi5.sh
#
# This script will:
#   1. Install system dependencies
#   2. Clone and build llama.cpp with OpenBLAS
#   3. Create model directory
#   4. Download official FunctionGemma Q8_0 GGUF (optional)
#   5. Run a quick test
# =============================================================================

set -euo pipefail

# --- Configuration ---
WORK_ROOT="$HOME/llama"                      # all llama-related assets live here
LLAMA_CPP_DIR="$WORK_ROOT/llama.cpp"
MODELS_DIR="$WORK_ROOT/models"
SYSTEM_PROMPT="$WORK_ROOT/system_prompt.txt"
JOBS=3  # Use 3 to avoid OOM on 8GB Pi 5

mkdir -p "$WORK_ROOT" "$MODELS_DIR" "$MODELS_DIR/whisper" "$WORK_ROOT/scripts"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Step 1: System dependencies ---
log_info "Step 1/5: Installing system dependencies..."
sudo apt update
sudo apt install -y \
    build-essential \
    cmake \
    git \
    python3-pip \
    python3-venv \
    libopenblas-dev \
    pkg-config \
    curl \
    wget

# --- Step 2: Clone and build llama.cpp ---
log_info "Step 2/5: Building llama.cpp..."

if [ -d "$LLAMA_CPP_DIR" ]; then
    log_warn "llama.cpp directory already exists at $LLAMA_CPP_DIR"
    read -p "Update and rebuild? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd "$LLAMA_CPP_DIR"
        git pull
    else
        log_info "Skipping llama.cpp build"
        cd "$LLAMA_CPP_DIR"
    fi
else
    git clone https://github.com/ggml-org/llama.cpp "$LLAMA_CPP_DIR"
    cd "$LLAMA_CPP_DIR"
fi

# Build with CMake + OpenBLAS
cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_BLAS=ON \
    -DGGML_BLAS_VENDOR=OpenBLAS

log_info "Compiling with $JOBS parallel jobs (this takes ~5-8 minutes)..."
cmake --build build --config Release -j"$JOBS"

# Verify
if [ ! -f "build/bin/llama-cli" ]; then
    log_error "Build failed: llama-cli not found"
    exit 1
fi
log_info "llama.cpp built successfully!"

# --- Step 3: Create model directory ---
log_info "Step 3/5: Creating model directory..."
mkdir -p "$MODELS_DIR"

# --- Step 4: Download official FunctionGemma GGUF ---
log_info "Step 4/5: Download official FunctionGemma Q8_0 GGUF?"
read -p "Download official model for testing? (y/N): " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
    GGUF_URL="https://huggingface.co/Edge-Quant/functiongemma-270m-it-Q8_0-GGUF/resolve/main/functiongemma-270m-it-q8_0.gguf"
    GGUF_FILE="$MODELS_DIR/functiongemma-270m-it-q8_0.gguf"

    if [ -f "$GGUF_FILE" ]; then
        log_warn "Model already exists at $GGUF_FILE, skipping download"
    else
        log_info "Downloading FunctionGemma Q8_0 GGUF (~280MB)..."
        wget -O "$GGUF_FILE" "$GGUF_URL"
        log_info "Download complete!"
    fi
else
    log_info "Skipping model download"
fi

# --- Step 5: Quick test ---
log_info "Step 5/5: Running quick test..."

# Find any GGUF file to test with
GGUF_TEST=$(find "$MODELS_DIR" -name "*.gguf" | head -1)

if [ -n "$GGUF_TEST" ]; then
    log_info "Testing with: $GGUF_TEST"

    # Build the test prompt from ~/llama/system_prompt.txt if it's there,
    # otherwise fall back to a plain "Hello" smoke test.
    if [ -f "$SYSTEM_PROMPT" ]; then
        USER_MSG="Change the background to blue"
        TEST_PROMPT="$(cat "$SYSTEM_PROMPT")
<start_of_turn>user
${USER_MSG}
<end_of_turn>
<start_of_turn>model
"
    else
        log_warn "$SYSTEM_PROMPT not found; running a plain smoke test instead."
        TEST_PROMPT="Hello, how are you?"
    fi

    "$LLAMA_CPP_DIR/build/bin/llama-cli" \
        -m "$GGUF_TEST" \
        -c 512 \
        -n 64 \
        --temp 0.1 \
        -p "$TEST_PROMPT" \
        --no-display-prompt \
        2>/dev/null

    echo ""
    log_info "Test complete!"
else
    log_warn "No GGUF model found in $MODELS_DIR. Skipping test."
    log_info "Copy your .gguf file to $MODELS_DIR and run:"
    echo "  $LLAMA_CPP_DIR/build/bin/llama-cli -m $MODELS_DIR/your_model.gguf -c 1024 -p 'test'"
fi

# --- Done ---
echo ""
echo "=============================================="
log_info "Setup complete!"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Copy your fine-tuned GGUF model to $MODELS_DIR/"
echo "  2. Run inference:"
echo "     $LLAMA_CPP_DIR/build/bin/llama-cli -m $MODELS_DIR/<model>.gguf -c 1024 -p '<prompt>'"
echo "  3. Or start the server:"
echo "     $LLAMA_CPP_DIR/build/bin/llama-server -m $MODELS_DIR/<model>.gguf -c 1024 --host 0.0.0.0 --port 8080"
echo "  4. To install as a system service:"
echo "     sudo bash scripts/install_service.sh"
echo ""
