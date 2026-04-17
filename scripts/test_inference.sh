#!/bin/bash
# =============================================================================
# test_inference.sh - Test FunctionGemma inference on RPI5
#
# Usage:
#   # Test with CLI (direct model loading)
#   bash test_inference.sh cli /path/to/model.gguf
#
#   # Test with server (must be running)
#   bash test_inference.sh server [url]
# =============================================================================

set -euo pipefail

MODE="${1:-server}"
LLAMA_CLI="$HOME/llama.cpp/build/bin/llama-cli"
SERVER_URL="${2:-http://localhost:8080}"

# Test prompts
declare -a TEST_QUERIES=(
    "Turn on the living room light"
    "Set the temperature to 25 degrees"
    "What is the temperature in the kitchen?"
    "Turn off all lights"
)

FUNCTION_DECLARATIONS='
<start_function_declaration>
{"name": "turn_on_light", "description": "Turn on a light in a specific room", "parameters": {"type": "object", "properties": {"room": {"type": "string"}}, "required": ["room"]}}
<end_function_declaration>

<start_function_declaration>
{"name": "turn_off_light", "description": "Turn off a light in a specific room", "parameters": {"type": "object", "properties": {"room": {"type": "string"}}, "required": ["room"]}}
<end_function_declaration>

<start_function_declaration>
{"name": "set_temperature", "description": "Set the thermostat temperature", "parameters": {"type": "object", "properties": {"temperature": {"type": "number"}, "unit": {"type": "string"}}, "required": ["temperature"]}}
<end_function_declaration>

<start_function_declaration>
{"name": "get_temperature", "description": "Get the current temperature in a room", "parameters": {"type": "object", "properties": {"room": {"type": "string"}}, "required": ["room"]}}
<end_function_declaration>
'

build_prompt() {
    local query="$1"
    echo "<start_of_turn>user
You are a helpful assistant with access to the following functions.
Use them if required:
${FUNCTION_DECLARATIONS}
${query}
<end_of_turn>
<start_of_turn>model
"
}

test_cli() {
    local model_path="$2"

    if [ ! -f "$model_path" ]; then
        echo "Error: Model not found at $model_path"
        exit 1
    fi

    echo "=============================="
    echo "Testing CLI mode"
    echo "Model: $model_path"
    echo "=============================="
    echo ""

    for query in "${TEST_QUERIES[@]}"; do
        echo "--- Query: $query ---"
        PROMPT=$(build_prompt "$query")

        "$LLAMA_CLI" \
            -m "$model_path" \
            -c 512 \
            -n 64 \
            --temp 0.1 \
            -p "$PROMPT" \
            --no-display-prompt \
            2>/dev/null

        echo ""
        echo ""
    done
}

test_server() {
    echo "=============================="
    echo "Testing Server mode"
    echo "Server: $SERVER_URL"
    echo "=============================="
    echo ""

    # Health check
    echo "--- Health check ---"
    if curl -s "$SERVER_URL/health" | grep -q "ok"; then
        echo "Server is healthy"
    else
        echo "Error: Server not responding at $SERVER_URL"
        echo "Start the server first:"
        echo "  ~/llama.cpp/build/bin/llama-server -m ~/models/<model>.gguf -c 1024 --host 0.0.0.0 --port 8080"
        exit 1
    fi
    echo ""

    for query in "${TEST_QUERIES[@]}"; do
        echo "--- Query: $query ---"
        PROMPT=$(build_prompt "$query")

        RESPONSE=$(curl -s "$SERVER_URL/completion" \
            -H "Content-Type: application/json" \
            -d "$(jq -n \
                --arg prompt "$PROMPT" \
                '{
                    prompt: $prompt,
                    n_predict: 64,
                    temperature: 0.1,
                    stop: ["<end_of_turn>"]
                }'
            )")

        echo "$RESPONSE" | jq -r '.content // "Error: no content"'
        echo ""
    done
}

case "$MODE" in
    cli)
        if [ -z "${2:-}" ]; then
            echo "Usage: bash test_inference.sh cli /path/to/model.gguf"
            exit 1
        fi
        test_cli "$@"
        ;;
    server)
        test_server
        ;;
    *)
        echo "Usage:"
        echo "  bash test_inference.sh cli /path/to/model.gguf"
        echo "  bash test_inference.sh server [url]"
        exit 1
        ;;
esac

echo "=============================="
echo "All tests complete!"
echo "=============================="
