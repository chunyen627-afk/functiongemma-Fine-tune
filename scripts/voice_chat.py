#!/usr/bin/env python3
"""
voice_chat.py - Voice-powered FunctionGemma chat on Raspberry Pi 5

Architecture:
    [Microphone] -> [arecord] -> [whisper.cpp] -> [text] -> [llama.cpp] -> [response]

Two modes:
    1. Push-to-talk: Press Enter to start recording, Enter again to stop
    2. Auto-detect:  Records until silence is detected (requires sox)

Prerequisites:
    - whisper.cpp built with setup_whisper.sh
    - llama.cpp built with setup_rpi5.sh
    - USB microphone connected
    - pip install requests  (only if using server mode)

Usage:
    # Mode 1: CLI mode (direct llama-cli invocation, no server needed)
    python3 voice_chat.py --mode cli --model ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf

    # Mode 2: Server mode (requires llama-server running)
    python3 voice_chat.py --mode server --server-url http://localhost:8080

    # Custom whisper model:
    python3 voice_chat.py --whisper-model ~/llama/models/whisper/ggml-base.bin

    # Use Chinese language for whisper:
    python3 voice_chat.py --language zh
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


# ===================== Configuration =====================

DEFAULT_CONFIG = {
    # Whisper
    "whisper_cli": os.path.expanduser("~/llama/whisper.cpp/build/bin/whisper-cli"),
    "whisper_model": os.path.expanduser("~/llama/models/whisper/ggml-base.bin"),
    "language": "auto",  # "auto", "en", "zh", "ja", etc.

    # llama.cpp
    "llama_cli": os.path.expanduser("~/llama/llama.cpp/build/bin/llama-cli"),
    "llama_model": os.path.expanduser("~/llama/models/functiongemma-270m-finetuned-q8_0.gguf"),
    "server_url": "http://localhost:8080",

    # System prompt file (official FunctionGemma format with function declarations)
    # This file should contain a <start_of_turn>developer ... <end_of_turn> block.
    # It is prepended to EVERY user turn; do NOT build function declarations inside this script.
    "system_prompt_file": os.path.expanduser("~/llama/system_prompt.txt"),

    # Audio recording
    "sample_rate": 16000,   # Whisper requires 16kHz
    "channels": 1,          # Mono
    "format": "S16_LE",     # 16-bit signed little-endian
    "silence_threshold": 3,  # Seconds of silence before auto-stop (sox mode)
    "max_record_seconds": 30,  # Maximum recording duration

    # FunctionGemma
    "context_size": 1024,
    "max_tokens": 128,
    "temperature": 0.1,
}

# Function names parsed out of the loaded system prompt (for display + execution dispatch).
# Populated at runtime by load_system_prompt().
PARSED_FUNCTION_NAMES: list[str] = []


def load_system_prompt(path: str) -> str:
    """Load the official FunctionGemma system prompt (developer turn + declarations).

    The file is expected to already contain the full
        <start_of_turn>developer
        ...declarations...
        <end_of_turn>
    block in the official format. We return it verbatim (stripped of trailing
    whitespace) so it can be prepended to every user turn.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"system_prompt.txt not found at {path}\n"
            f"Pass --system-prompt <path> or place the file at the default location."
        )
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().rstrip()
    # Extract function names for display / dispatch.
    import re
    PARSED_FUNCTION_NAMES.clear()
    for m in re.finditer(r"declaration:([A-Za-z_][A-Za-z0-9_]*)\{", text):
        PARSED_FUNCTION_NAMES.append(m.group(1))
    return text


# ===================== Audio Recording =====================

class AudioRecorder:
    """Record audio from microphone using arecord."""

    def __init__(self, config):
        self.config = config
        self._process = None
        self._recording = False

    def _check_microphone(self):
        """Check if a microphone is available."""
        try:
            result = subprocess.run(
                ["arecord", "-l"],
                capture_output=True, text=True, timeout=5
            )
            if "card" not in result.stdout:
                print("[ERROR] No microphone detected!")
                print("  Connect a USB microphone and check with: arecord -l")
                return False
            return True
        except FileNotFoundError:
            print("[ERROR] arecord not found. Install with: sudo apt install alsa-utils")
            return False

    def record_push_to_talk(self) -> str | None:
        """Record audio with push-to-talk (Enter to start/stop).

        Returns path to recorded WAV file, or None on failure.
        """
        if not self._check_microphone():
            return None

        wav_path = tempfile.mktemp(suffix=".wav")

        print("  [Press Enter to START recording...]", end="", flush=True)
        input()

        # Start recording in background
        cmd = [
            "arecord",
            "-r", str(self.config["sample_rate"]),
            "-c", str(self.config["channels"]),
            "-f", self.config["format"],
            "-t", "wav",
            "-d", str(self.config["max_record_seconds"]),
            wav_path
        ]

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        self._recording = True

        print("  [Recording... Press Enter to STOP]", end="", flush=True)
        input()

        # Stop recording
        if self._process and self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=5)
        self._recording = False

        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000:
            duration = os.path.getsize(wav_path) / (self.config["sample_rate"] * 2)
            print(f"  [Recorded {duration:.1f}s]")
            return wav_path
        else:
            print("  [Recording failed or too short]")
            return None

    def record_with_silence_detection(self) -> str | None:
        """Record audio until silence is detected (requires sox/rec).

        Returns path to recorded WAV file, or None on failure.
        """
        if not self._check_microphone():
            return None

        wav_path = tempfile.mktemp(suffix=".wav")
        silence_sec = self.config["silence_threshold"]

        print(f"  [Listening... (auto-stops after {silence_sec}s silence)]")

        # Use sox's rec command with silence detection
        # silence 1 0.1 1% = start after any sound
        # 1 {silence_sec} 1% = stop after silence_sec seconds of silence
        cmd = [
            "rec",
            "-r", str(self.config["sample_rate"]),
            "-c", str(self.config["channels"]),
            "-b", "16",
            wav_path,
            "trim", "0", str(self.config["max_record_seconds"]),
            "silence", "1", "0.1", "1%",
            "1", str(silence_sec), "1%",
        ]

        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.config["max_record_seconds"] + 5
            )
        except subprocess.TimeoutExpired:
            pass
        except FileNotFoundError:
            print("  [ERROR] 'rec' (sox) not found. Falling back to push-to-talk mode.")
            return self.record_push_to_talk()

        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000:
            duration = os.path.getsize(wav_path) / (self.config["sample_rate"] * 2)
            print(f"  [Recorded {duration:.1f}s]")
            return wav_path
        else:
            print("  [No speech detected]")
            return None

    def cleanup(self):
        """Stop any ongoing recording."""
        if self._process and self._process.poll() is None:
            self._process.terminate()


# ===================== Speech-to-Text =====================

class WhisperSTT:
    """Speech-to-text using whisper.cpp."""

    def __init__(self, config):
        self.config = config
        self.cli_path = config["whisper_cli"]
        self.model_path = config["whisper_model"]

        # Validate paths
        if not os.path.exists(self.cli_path):
            raise FileNotFoundError(
                f"whisper-cli not found at {self.cli_path}\n"
                f"Run setup_whisper.sh first."
            )
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"Whisper model not found at {self.model_path}\n"
                f"Run setup_whisper.sh to download a model."
            )

    def transcribe(self, wav_path: str) -> str:
        """Transcribe a WAV file to text.

        Args:
            wav_path: Path to 16kHz mono WAV file

        Returns:
            Transcribed text string
        """
        cmd = [
            self.cli_path,
            "-m", self.model_path,
            "-f", wav_path,
            "--no-timestamps",
            "-l", self.config["language"],
            "--print-special", "false",
            "-t", "4",  # 4 threads for RPI5
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )

            # whisper-cli outputs to stdout, logs to stderr
            text = result.stdout.strip()

            # Clean up whisper output (remove leading/trailing whitespace per line)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = " ".join(lines)

            # Remove common whisper artifacts
            for artifact in ["[BLANK_AUDIO]", "(blank audio)", "[silence]"]:
                text = text.replace(artifact, "")

            return text.strip()

        except subprocess.TimeoutExpired:
            return ""
        except Exception as e:
            print(f"  [Whisper error: {e}]")
            return ""


# ===================== LLM Inference =====================

def build_functiongemma_prompt(user_message: str, system_prompt: str) -> str:
    """Build FunctionGemma prompt by prepending the official developer block.

    The `system_prompt` must already be the verbatim
        <start_of_turn>developer ... <end_of_turn>
    block loaded from system_prompt.txt.
    """
    return (
        f"{system_prompt}\n"
        f"<start_of_turn>user\n"
        f"{user_message}\n"
        f"<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )


def parse_function_call(text: str) -> dict | None:
    """Parse a function call from model output.

    Accepts two formats FunctionGemma may emit:
      1. JSON between <start_function_call>...<end_function_call> tags:
         {"name": "foo", "arguments": {...}}
      2. Declaration-style echo matching system_prompt.txt:
         <start_function_call>functioncall:foo{key:<escape>value<escape>,...}<end_function_call>
    """
    import re

    content = text
    if "<start_function_call>" in content:
        content = content.split("<start_function_call>", 1)[1]
    if "<end_function_call>" in content:
        content = content.split("<end_function_call>", 1)[0]
    content = content.strip()
    if not content:
        return None

    # Try JSON first.
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "name" in parsed:
            return {
                "name": parsed.get("name"),
                "arguments": parsed.get("arguments", {}) or {},
            }
    except json.JSONDecodeError:
        pass

    # Fallback: declaration-style  [functioncall:]name{key:<escape>val<escape>,...}
    m = re.match(
        r"(?:functioncall:|declaration:|call:)?([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*)\}\s*$",
        content,
        flags=re.DOTALL,
    )
    if not m:
        return None
    name, body = m.group(1), m.group(2)
    args: dict = {}
    # Each arg: key:<escape>value<escape>  (value may contain commas, so use non-greedy)
    for km in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*<escape>(.*?)<escape>",
        body,
        flags=re.DOTALL,
    ):
        args[km.group(1)] = km.group(2)
    # Also accept bare numeric / boolean values (no <escape>)
    for km in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?\d+(?:\.\d+)?|true|false)\b",
        body,
    ):
        k, v = km.group(1), km.group(2)
        if k in args:
            continue
        if v == "true":
            args[k] = True
        elif v == "false":
            args[k] = False
        elif "." in v:
            args[k] = float(v)
        else:
            args[k] = int(v)
    return {"name": name, "arguments": args}


class LLMInferenceCLI:
    """Inference using llama-cli directly (no server needed)."""

    def __init__(self, config):
        self.config = config
        self.cli_path = config["llama_cli"]
        self.model_path = config["llama_model"]

        if not os.path.exists(self.cli_path):
            raise FileNotFoundError(
                f"llama-cli not found at {self.cli_path}\n"
                f"Run setup_rpi5.sh first."
            )
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(
                f"Model not found at {self.model_path}\n"
                f"Copy your GGUF model to ~/llama/models/"
            )

    def generate(self, user_message: str) -> str:
        """Generate a response using llama-cli."""
        prompt = build_functiongemma_prompt(user_message, self.config["_system_prompt"])

        cmd = [
            self.cli_path,
            "-m", self.model_path,
            "-c", str(self.config["context_size"]),
            "-n", str(self.config["max_tokens"]),
            "--temp", str(self.config["temperature"]),
            "-p", prompt,
            "--no-display-prompt",
            "-e",  # Enable escape sequences
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            output = result.stdout.strip()
            # Remove everything after <end_of_turn> if present
            if "<end_of_turn>" in output:
                output = output.split("<end_of_turn>")[0]
            return output.strip()
        except subprocess.TimeoutExpired:
            return "[Timeout]"
        except Exception as e:
            return f"[Error: {e}]"


class LLMInferenceServer:
    """Inference using llama-server HTTP API."""

    def __init__(self, config):
        self.config = config
        self.server_url = config["server_url"]

        # Test connection
        try:
            import requests
            resp = requests.get(f"{self.server_url}/health", timeout=5)
            if resp.status_code != 200:
                print(f"[WARN] Server at {self.server_url} returned {resp.status_code}")
        except ImportError:
            raise ImportError("requests package required for server mode: pip install requests")
        except Exception:
            print(f"[WARN] Cannot connect to {self.server_url}. Is llama-server running?")

    def generate(self, user_message: str) -> str:
        """Generate a response using llama-server API."""
        import requests

        prompt = build_functiongemma_prompt(user_message, self.config["_system_prompt"])

        try:
            resp = requests.post(
                f"{self.server_url}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": self.config["max_tokens"],
                    "temperature": self.config["temperature"],
                    "stop": ["<end_of_turn>"],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("content", "").strip()
        except Exception as e:
            return f"[Error: {e}]"


# ===================== Function Execution (real side-effects) =====================

# ANSI background color codes. "orange" uses the 256-color palette.
_ANSI_BG = {
    "red":     "\033[41m",
    "green":   "\033[42m",
    "yellow":  "\033[43m",
    "blue":    "\033[44m",
    "purple":  "\033[45m",
    "magenta": "\033[45m",
    "cyan":    "\033[46m",
    "white":   "\033[47m",
    "black":   "\033[40m",
    "orange":  "\033[48;5;208m",
}
_ANSI_RESET = "\033[0m"

# Track the currently applied background so new lines inherit it.
_current_bg = ""


def _fn_change_background_color(args: dict) -> str:
    """Actually change the terminal background color via ANSI escapes."""
    global _current_bg
    color = str(args.get("color", "")).lower().strip()
    code = _ANSI_BG.get(color)
    if not code:
        return f"[UI] 不支援的顏色 '{color}'，可用: {', '.join(_ANSI_BG)}"
    _current_bg = code
    # Apply color + clear screen so whole terminal repaints with the new bg.
    sys.stdout.write(code + "\033[2J\033[H")
    sys.stdout.flush()
    return f"[UI] ✅ 終端機背景已改為 {color}"


def _fn_change_app_title(args: dict) -> str:
    """Actually change the terminal window title via OSC escape."""
    title = str(args.get("title", ""))
    # OSC 0 sets both icon name + window title (most terminals support this).
    sys.stdout.write(f"\033]0;{title}\007")
    sys.stdout.flush()
    return f"[UI] ✅ 終端機標題已改為: {title}"


def _fn_show_alert(args: dict) -> str:
    """Actually draw an alert box + emit a terminal bell."""
    title = str(args.get("title", ""))
    message = str(args.get("message", ""))
    # Width based on widest line (crude but works for ASCII + CJK).
    def _vwidth(s: str) -> int:
        # CJK chars ~2 cells, ASCII ~1
        return sum(2 if ord(c) > 127 else 1 for c in s)
    inner = max(_vwidth(title), _vwidth(message), 20)
    sep = "═" * (inner + 4)
    def _pad(s: str) -> str:
        return s + " " * (inner - _vwidth(s))
    print()
    print(f"╔{sep}╗")
    print(f"║  {_pad(title)}  ║")
    print(f"╠{sep}╣")
    print(f"║  {_pad(message)}  ║")
    print(f"╚{sep}╝")
    sys.stdout.write("\a")  # bell
    sys.stdout.flush()
    return f"[UI] ✅ alert shown"


def _fn_get_current_weather(args: dict) -> str:
    """Really fetch weather via wttr.in (no API key needed)."""
    location = str(args.get("location", "")).strip()
    if not location:
        return "[Weather] ❌ location 為空"
    try:
        import urllib.request
        import urllib.parse
        url = (
            "https://wttr.in/"
            + urllib.parse.quote(location)
            + "?format=%l:+%C+%t&lang=zh"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=8) as r:
            text = r.read().decode("utf-8", errors="replace").strip()
        return f"[Weather] {text}"
    except Exception as e:
        return f"[Weather] ❌ 無法取得 {location} 的天氣: {e}"


FUNCTION_HANDLERS = {
    "change_background_color": _fn_change_background_color,
    "change_app_title":        _fn_change_app_title,
    "show_alert":              _fn_show_alert,
    "get_current_weather":     _fn_get_current_weather,
}


def execute_function(func_call: dict) -> str:
    """Dispatch a parsed function call to its real handler."""
    name = func_call.get("name", "")
    args = func_call.get("arguments", {}) or {}
    handler = FUNCTION_HANDLERS.get(name)
    if handler:
        try:
            return handler(args)
        except Exception as e:
            return f"[Error] 執行 {name} 失敗: {e}"
    return f"Unknown function: {name}"


def reset_terminal():
    """Restore default terminal colors on exit."""
    if _current_bg:
        sys.stdout.write(_ANSI_RESET + "\033[2J\033[H")
        sys.stdout.flush()


# ===================== Main Chat Loop =====================

def _get_user_input(recorder, stt, current_mode: str, voice_submode: str):
    """Prompt once; return (text, new_mode, quit_flag).

    current_mode: "voice" or "text"
    voice_submode: "push" (push-to-talk) or "auto" (silence detection)

    Each turn the user gets a command line:
      [Enter]  = 依當前模式直接進行
      v        = 切換到語音輸入 (下一回合生效)
      t        = 切換到文字輸入
      q        = 離開
      其他文字 = 直接當作訊息送出 (不管目前是什麼模式)
    """
    prompt_tag = "語音🎤" if current_mode == "voice" else "文字⌨ "
    hint = f"[{prompt_tag}] Enter=送出 / v=語音 / t=文字 / q=離開 / 或直接輸入文字 > "
    try:
        line = input(hint)
    except (EOFError, KeyboardInterrupt):
        return None, current_mode, True

    cmd = line.strip().lower()
    if cmd in ("q", "quit", "exit"):
        return None, current_mode, True
    if cmd == "v":
        return "", "voice", False     # switched, no text yet → next loop iteration
    if cmd == "t":
        return "", "text", False

    # Direct typed message (override current mode for this turn).
    if line.strip():
        return line.strip(), current_mode, False

    # Empty line → use current mode.
    if current_mode == "text":
        try:
            typed = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None, current_mode, True
        if not typed:
            return "", current_mode, False
        return typed, current_mode, False

    # Voice mode
    try:
        if voice_submode == "push":
            wav_path = recorder.record_push_to_talk()
        else:
            wav_path = recorder.record_with_silence_detection()
    except KeyboardInterrupt:
        return None, current_mode, True

    if not wav_path:
        print("  [錄音失敗或太短，請再試一次，或按 t 切到文字模式]")
        return "", current_mode, False

    print("  [轉錄中...]")
    text = stt.transcribe(wav_path)
    try:
        os.unlink(wav_path)
    except OSError:
        pass

    if not text:
        print("  [無法辨識語音，請再試一次，或按 t 切到文字模式]")
        return "", current_mode, False

    print(f"  🗣  You said: \"{text}\"")
    return text, current_mode, False


def run_voice_chat(config: dict, mode: str, voice_submode: str, start_input_mode: str):
    """Main chat loop with voice/text toggle."""
    print("=" * 60)
    print("  FunctionGemma Chat (語音 + 文字)")
    print("=" * 60)
    print(f"  Inference:   {mode} mode")
    print(f"  Start mode:  {start_input_mode}   (v/t 隨時切換)")
    print(f"  Voice sub:   {voice_submode}")
    print(f"  Language:    {config['language']}")
    print(f"  Whisper:     {os.path.basename(config['whisper_model'])}")
    if mode == "cli":
        print(f"  LLM:         {os.path.basename(config['llama_model'])}")
    else:
        print(f"  Server:      {config['server_url']}")
    print(f"  SysPrompt:   {config['system_prompt_file']}")
    print(f"  Functions:   {PARSED_FUNCTION_NAMES}")
    print("=" * 60)
    print()
    print("操作說明:")
    print("  Enter       - 依當前模式進行 (語音: 錄音 / 文字: 要求輸入)")
    print("  v + Enter   - 切到語音模式")
    print("  t + Enter   - 切到文字模式")
    print("  直接打字    - 當場用文字模式送出這句")
    print("  q + Enter   - 離開")
    print()

    recorder = AudioRecorder(config)
    stt = WhisperSTT(config)
    llm = LLMInferenceCLI(config) if mode == "cli" else LLMInferenceServer(config)

    current_mode = start_input_mode  # "voice" / "text"
    turn = 0
    try:
        while True:
            turn += 1
            print(f"--- Turn {turn} ---")
            text, current_mode, quit_flag = _get_user_input(
                recorder, stt, current_mode, voice_submode
            )
            if quit_flag:
                print("\nBye!")
                break
            if not text:
                # Mode switch or empty input — loop again.
                continue

            print("  [🤔 Thinking...]")
            raw_output = llm.generate(text)

            # Show the raw official output exactly as the model emitted.
            print("  ┌─ 原始輸出 (official format) ──────────────")
            for line in raw_output.splitlines() or [raw_output]:
                print(f"  │ {line}")
            print("  └──────────────────────────────────────────")

            func_call = parse_function_call(raw_output)
            if func_call:
                print(f"  🔧 解析: {func_call['name']}"
                      f"({json.dumps(func_call['arguments'], ensure_ascii=False)})")
                result = execute_function(func_call)
                print(f"  ⚙  執行結果: {result}")
            else:
                print("  ⚠  未偵測到 function call")
            print()
    finally:
        reset_terminal()


# ===================== CLI Arguments =====================

def main():
    parser = argparse.ArgumentParser(
        description="Voice-powered FunctionGemma chat on RPI5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # CLI mode with push-to-talk (default)
  python3 voice_chat.py

  # Server mode
  python3 voice_chat.py --mode server

  # Auto-silence detection
  python3 voice_chat.py --input auto

  # Chinese language
  python3 voice_chat.py --language zh

  # Custom model paths
  python3 voice_chat.py --model ~/llama/models/my_model.gguf --whisper-model ~/llama/models/whisper/ggml-small.bin
        """
    )

    parser.add_argument(
        "--mode", choices=["cli", "server"], default="cli",
        help="Inference mode: 'cli' uses llama-cli directly, 'server' uses HTTP API (default: cli)"
    )
    parser.add_argument(
        "--input", choices=["push", "auto"], default="push",
        dest="input_mode",
        help="Voice sub-mode: 'push' for push-to-talk, 'auto' for silence detection (default: push)"
    )
    parser.add_argument(
        "--start-mode", choices=["voice", "text"], default="voice",
        help="Starting input mode; 'v'/'t' inside the chat switches on the fly (default: voice)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to FunctionGemma GGUF model"
    )
    parser.add_argument(
        "--whisper-model", type=str, default=None,
        help="Path to Whisper GGML model"
    )
    parser.add_argument(
        "--whisper-cli", type=str, default=None,
        help="Path to whisper-cli binary"
    )
    parser.add_argument(
        "--server-url", type=str, default=None,
        help="llama-server URL (default: http://localhost:8080)"
    )
    parser.add_argument(
        "--language", type=str, default=None,
        help="Whisper language code: 'auto', 'en', 'zh', 'ja', etc. (default: auto)"
    )
    parser.add_argument(
        "--system-prompt", type=str, default=None,
        help="Path to system_prompt.txt (official FunctionGemma developer block). "
             "Default: ~/llama/system_prompt.txt"
    )

    args = parser.parse_args()

    # Build config
    config = DEFAULT_CONFIG.copy()
    if args.model:
        config["llama_model"] = os.path.expanduser(args.model)
    if args.whisper_model:
        config["whisper_model"] = os.path.expanduser(args.whisper_model)
    if args.whisper_cli:
        config["whisper_cli"] = os.path.expanduser(args.whisper_cli)
    if args.server_url:
        config["server_url"] = args.server_url
    if args.language:
        config["language"] = args.language
    if args.system_prompt:
        config["system_prompt_file"] = os.path.expanduser(args.system_prompt)

    # Load the official system prompt (developer block + declarations).
    config["_system_prompt"] = load_system_prompt(config["system_prompt_file"])

    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

    try:
        run_voice_chat(config, args.mode, args.input_mode, args.start_mode)
    except FileNotFoundError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        reset_terminal()


if __name__ == "__main__":
    main()
