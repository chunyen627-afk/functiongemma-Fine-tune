#!/usr/bin/env python3
"""
FunctionGemma Client - Python client for calling the FunctionGemma server on RPI5.

Usage:
    python function_call_client.py

Prerequisites:
    pip install requests

The llama-server must be running:
    ~/llama/llama.cpp/build/bin/llama-server \
        -m ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf \
        -c 1024 --host 0.0.0.0 --port 8080
"""

import json
import os
import re
import requests
import sys


SERVER_URL = "http://localhost:8080"

# Default location for the official FunctionGemma system prompt on RPI5.
# The file must contain the verbatim <start_of_turn>developer ... <end_of_turn>
# block with all function declarations in the official format.
DEFAULT_SYSTEM_PROMPT_PATH = os.path.expanduser("~/llama/system_prompt.txt")


def load_system_prompt(path: str = DEFAULT_SYSTEM_PROMPT_PATH) -> str:
    """Load the official FunctionGemma developer turn from disk."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"system_prompt.txt not found at {path}. "
            f"Pass its path explicitly or place it at the default location."
        )
    with open(path, "r", encoding="utf-8") as f:
        return f.read().rstrip()


def parse_function_names(system_prompt: str) -> list[str]:
    """Extract declared function names for display / dispatch."""
    return re.findall(r"declaration:([A-Za-z_][A-Za-z0-9_]*)\{", system_prompt)


def build_prompt(user_message: str, system_prompt: str) -> str:
    """Build the full prompt by prepending the loaded developer block.

    IMPORTANT: FunctionGemma uses a custom chat format with special tokens.
    Do NOT use HuggingFace's apply_chat_template - it produces a different
    format that breaks compatibility. We rely on the user-provided
    system_prompt.txt which is already in the official format.
    """
    return (
        f"{system_prompt}\n"
        f"<start_of_turn>user\n"
        f"{user_message}\n"
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def call_function_gemma(
    user_message: str,
    system_prompt: str | None = None,
    server_url: str = SERVER_URL,
    temperature: float = 0.1,
    max_tokens: int = 128,
) -> dict:
    """Send a message to FunctionGemma and parse the function call response.

    Args:
        user_message: The natural language user input
        system_prompt: The loaded developer block (auto-loaded from the default
                       path if None)
        server_url: The llama-server URL
        temperature: Sampling temperature (lower = more deterministic)
        max_tokens: Maximum tokens to generate

    Returns:
        dict with keys:
            - raw_output: The raw model output string
            - function_name: Extracted function name (or None)
            - arguments: Extracted arguments dict (or None)
            - success: Whether parsing succeeded
    """
    if system_prompt is None:
        system_prompt = load_system_prompt()

    prompt = build_prompt(user_message, system_prompt)

    try:
        response = requests.post(
            f"{server_url}/completion",
            json={
                "prompt": prompt,
                "n_predict": max_tokens,
                "temperature": temperature,
                "stop": ["<end_of_turn>"],
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.ConnectionError:
        return {
            "raw_output": "",
            "function_name": None,
            "arguments": None,
            "success": False,
            "error": f"Cannot connect to server at {server_url}. Is llama-server running?",
        }
    except requests.RequestException as e:
        return {
            "raw_output": "",
            "function_name": None,
            "arguments": None,
            "success": False,
            "error": str(e),
        }

    raw_output = response.json().get("content", "").strip()

    # Parse the function call from the output
    # Expected format: <start_function_call>\n{"name": "...", "arguments": {...}}\n<end_function_call>
    result = {
        "raw_output": raw_output,
        "function_name": None,
        "arguments": None,
        "success": False,
    }

    try:
        # Extract JSON between function call tags
        text = raw_output
        if "<start_function_call>" in text:
            text = text.split("<start_function_call>")[1]
        if "<end_function_call>" in text:
            text = text.split("<end_function_call>")[0]

        text = text.strip()
        parsed = json.loads(text)

        result["function_name"] = parsed.get("name")
        result["arguments"] = parsed.get("arguments", {})
        result["success"] = True
    except (json.JSONDecodeError, IndexError):
        result["error"] = f"Failed to parse function call from output: {raw_output}"

    return result


def execute_function(function_name: str, arguments: dict) -> str:
    """Execute a function call locally (stub implementation).

    Replace these stubs with your actual device control logic.
    """
    handlers = {
        "change_background_color": lambda a: f"[UI] background color -> {a.get('color', '?')}",
        "change_app_title":        lambda a: f"[UI] app title -> {a.get('title', '?')}",
        "show_alert":              lambda a: f"[UI] alert: {a.get('title', '?')} / {a.get('message', '?')}",
        "get_current_weather":     lambda a: f"[Weather] {a.get('location', '?')}: 26°C 晴",
    }

    handler = handlers.get(function_name)
    if handler:
        return handler(arguments)
    return f"Unknown function: {function_name}"


def interactive_mode():
    """Run an interactive loop for testing function calls."""
    system_prompt = load_system_prompt()
    func_names = parse_function_names(system_prompt)

    print("=" * 60)
    print("FunctionGemma Interactive Client")
    print(f"Server:     {SERVER_URL}")
    print(f"SysPrompt:  {DEFAULT_SYSTEM_PROMPT_PATH}")
    print(f"Functions:  {func_names}")
    print("Type 'quit' to exit")
    print("=" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        result = call_function_gemma(user_input, system_prompt=system_prompt)

        if "error" in result:
            print(f"Error: {result['error']}")
            continue

        print(f"Raw output: {result['raw_output']}")

        if result["success"]:
            print(f"Function: {result['function_name']}")
            print(f"Arguments: {json.dumps(result['arguments'], indent=2)}")

            # Execute the function
            execution_result = execute_function(
                result["function_name"], result["arguments"]
            )
            print(f"Result: {execution_result}")
        else:
            print("Could not parse function call from model output")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Single query mode
        query = " ".join(sys.argv[1:])
        result = call_function_gemma(query)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Interactive mode
        interactive_mode()
