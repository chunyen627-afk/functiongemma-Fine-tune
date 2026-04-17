#!/bin/bash
# =============================================================================
# setup_whisper.sh - Install whisper.cpp on Raspberry Pi 5 for voice input
#
# Usage:
#   chmod +x setup_whisper.sh
#   ./setup_whisper.sh
#
# This script will:
#   1. Install audio dependencies (ALSA, SDL2)
#   2. Clone and build whisper.cpp
#   3. Download Whisper tiny/base model (GGML format)
#   4. Test microphone recording
#   5. Test speech-to-text
# =============================================================================

set -euo pipefail

WORK_ROOT="$HOME/llama"
WHISPER_DIR="$WORK_ROOT/whisper.cpp"
MODELS_DIR="$WORK_ROOT/models/whisper"
JOBS=3

mkdir -p "$WORK_ROOT" "$MODELS_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Step 1: Install audio dependencies ---
log_info "Step 1/5: Installing audio dependencies..."
sudo apt update
sudo apt install -y \
    libasound2-dev \
    libsdl2-dev \
    alsa-utils \
    sox \
    libsox-fmt-all \
    pulseaudio \
    pulseaudio-utils \
    ffmpeg

# --- Step 2: Clone and build whisper.cpp ---
log_info "Step 2/5: Building whisper.cpp..."

if [ -d "$WHISPER_DIR" ]; then
    log_warn "whisper.cpp directory already exists at $WHISPER_DIR"
    read -p "Update and rebuild? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        cd "$WHISPER_DIR"
        git pull
    else
        log_info "Skipping whisper.cpp build"
    fi
else
    git clone https://github.com/ggerganov/whisper.cpp "$WHISPER_DIR"
fi

cd "$WHISPER_DIR"

# Build with CMake + SDL2 (for real-time microphone capture)
cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_SDL2=ON

log_info "Compiling with $JOBS parallel jobs..."
cmake --build build --config Release -j"$JOBS"

# Verify builds
REQUIRED_BINS=("build/bin/whisper-cli" "build/bin/whisper-stream")
for bin in "${REQUIRED_BINS[@]}"; do
    if [ -f "$bin" ]; then
        log_info "Built: $bin"
    else
        log_error "Build failed: $bin not found"
        exit 1
    fi
done

# --- Step 3: Download Whisper model ---
log_info "Step 3/5: Downloading Whisper model..."
mkdir -p "$MODELS_DIR"

echo ""
echo "Select Whisper model size:"
echo "  1) tiny   (~75 MB)  - Fastest, lowest accuracy       [recommended for RPI5]"
echo "  2) base   (~142 MB) - Good balance                    [recommended if RAM allows]"
echo "  3) small  (~466 MB) - Better accuracy, slower"
echo ""
read -p "Choose (1/2/3) [default: 2]: " -n 1 -r MODEL_CHOICE
echo ""

case "${MODEL_CHOICE:-2}" in
    1)
        MODEL_NAME="tiny"
        MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin"
        ;;
    3)
        MODEL_NAME="small"
        MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
        ;;
    *)
        MODEL_NAME="base"
        MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
        ;;
esac

MODEL_FILE="$MODELS_DIR/ggml-${MODEL_NAME}.bin"

if [ -f "$MODEL_FILE" ]; then
    log_warn "Model already exists: $MODEL_FILE"
else
    log_info "Downloading ggml-${MODEL_NAME}.bin..."
    wget -O "$MODEL_FILE" "$MODEL_URL"
    log_info "Downloaded: $MODEL_FILE"
fi

# --- Step 4: Test microphone ---
log_info "Step 4/5: Testing microphone..."

echo ""
echo "Checking audio devices..."
echo "Recording devices:"
arecord -l 2>/dev/null || log_warn "No ALSA recording devices found"

echo ""
# Check for any capture device
if arecord -l 2>/dev/null | grep -q "card"; then
    log_info "Microphone detected!"

    read -p "Record a 3-second test clip? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        TEST_WAV="/tmp/whisper_test.wav"
        log_info "Recording 3 seconds... Speak now!"
        arecord -d 3 -r 16000 -c 1 -f S16_LE "$TEST_WAV" 2>/dev/null

        if [ -f "$TEST_WAV" ]; then
            FILE_SIZE=$(stat -c%s "$TEST_WAV" 2>/dev/null || echo "0")
            if [ "$FILE_SIZE" -gt 1000 ]; then
                log_info "Recording successful! ($FILE_SIZE bytes)"
            else
                log_warn "Recording seems too small. Check microphone connection."
            fi
        fi
    fi
else
    log_warn "No microphone detected!"
    echo "Please connect a USB microphone and run 'arecord -l' to verify."
    echo "The voice scripts will still be installed - you can test later."
fi

# --- Step 5: Test whisper ---
log_info "Step 5/5: Testing whisper.cpp..."

if [ -f "/tmp/whisper_test.wav" ]; then
    log_info "Transcribing test recording..."
    "$WHISPER_DIR/build/bin/whisper-cli" \
        -m "$MODEL_FILE" \
        -f /tmp/whisper_test.wav \
        --no-timestamps \
        -l auto \
        2>/dev/null
    echo ""
else
    log_info "No test recording available. Skipping transcription test."
fi

# --- Done ---
echo ""
echo "=============================================="
log_info "Whisper.cpp setup complete!"
echo "=============================================="
echo ""
echo "Installed:"
echo "  whisper-cli:    $WHISPER_DIR/build/bin/whisper-cli"
echo "  whisper-stream: $WHISPER_DIR/build/bin/whisper-stream"
echo "  Model:          $MODEL_FILE"
echo ""
echo "Quick test commands:"
echo ""
echo "  # Record and transcribe:"
echo "  arecord -d 5 -r 16000 -c 1 -f S16_LE /tmp/test.wav"
echo "  $WHISPER_DIR/build/bin/whisper-cli -m $MODEL_FILE -f /tmp/test.wav -l auto"
echo ""
echo "  # Real-time streaming (hold to talk):"
echo "  $WHISPER_DIR/build/bin/whisper-stream -m $MODEL_FILE -l auto"
echo ""
echo "  # Voice chat with FunctionGemma:"
echo "  python3 ~/scripts/voice_chat.py"
echo ""
