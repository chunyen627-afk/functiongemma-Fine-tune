# FunctionGemma 270M on Raspberry Pi 5 - Complete Guide

## Overview

This guide covers the complete pipeline:

1. **Phase A** - Fine-tune FunctionGemma 270M IT (Google Colab)
2. **Phase B** - Convert & Quantize to GGUF INT8 (Colab or PC)
3. **Phase C** - Build llama.cpp on RPI5
4. **Phase D** - Deploy & Run on RPI5
5. **Phase F** - Voice Input (語音輸入) on RPI5

```
[Google Colab]                    [RPI5]
Fine-tune -> SafeTensors          Build llama.cpp
     |                                |
     v                                v
Convert to GGUF (F16)            Download GGUF
     |                                |
     v                                v
Quantize to Q8_0                 Run inference
     |                                |
     v                                v
Upload to HuggingFace/GDrive --> Copy to RPI5
                                      |
                                      v
                                 Voice Chat
                                 [Mic] -> [whisper.cpp] -> [text] -> [llama.cpp]
```

---

## Phase A: Fine-tune FunctionGemma 270M IT (Google Colab)

> 你已經有這個步驟的經驗 (參考 flutter_gemma 的 colab notebook)。
> 這裡提供關鍵注意事項。

### A.1 Base Model

- HuggingFace ID: `google/functiongemma-270m-it`
- Architecture: Gemma 3 270M (decoder-only transformer)
- Context Window: 32K tokens

### A.2 Fine-tuning Flow (Colab)

直接使用 `scripts/01_finetune_functiongemma.ipynb`，這是根據你參考的
flutter_gemma notebook 修改的版本，主要差異：

- 輸出格式改為 SafeTensors (供後續 GGUF 轉換使用)
- 移除了 TFLite 轉換步驟
- 新增模型驗證步驟

### A.3 訓練資料格式 (官方 FunctionGemma 規格)

FunctionGemma 使用**特殊的 chat format**，不要用 HuggingFace 的
`apply_chat_template`。官方格式以 `<start_of_turn>developer` 開頭，
宣告使用 `declaration:name{...}` 搭配 `<escape>...<escape>` 包裹字串：

```
<start_of_turn>developer
You are a model that can do function calling with the following functions
<start_function_declaration>declaration:change_background_color{description:<escape>Changes the app background color<escape>,parameters:{properties:{color:{description:<escape>The color name (red, green, blue, yellow, purple, orange)<escape>,type:<escape>STRING<escape>}},required:[<escape>color<escape>],type:<escape>OBJECT<escape>}}<end_function_declaration>
<end_of_turn>
<start_of_turn>user
Change the background to blue
<end_of_turn>
<start_of_turn>model
<start_function_call>
{"name": "change_background_color", "arguments": {"color": "blue"}}
<end_function_call>
<end_of_turn>
```

> **重要：** 本專案已在 `rpi5/system_prompt.txt` 備妥官方格式的 developer
> block。所有推論腳本 (`voice_chat.py`、`function_call_client.py`、
> `llama-cli` 範例) 都直接讀這個檔，**不要**在程式裡自行組 declaration 字串。

### A.4 訓練完成後的輸出

訓練完成後，模型會儲存為 HuggingFace 標準格式：
```
finetuned_model/
  ├── config.json
  ├── model.safetensors (或 model-00001-of-XXXXX.safetensors)
  ├── tokenizer.json
  ├── tokenizer_config.json
  ├── special_tokens_map.json
  └── generation_config.json
```

**重要**: 這個 notebook 使用的是 Full SFT（不是 LoRA），所以輸出的就是
完整權重，不需要額外 merge 步驟。

---

## Phase B: Convert & Quantize to GGUF (Colab or PC)

### B.1 方法一：在 Colab 中完成轉換 (推薦)

使用 `scripts/02_convert_to_gguf.ipynb` (v3 版)，這個 notebook 會：
1. Clone 並編譯 llama.cpp
2. 把 `llama-quantize` 與 `convert_hf_to_gguf.py` / `gguf-py/` 一起持久化到 Google Drive (`llama_cpp_tools/`)
3. 將 SafeTensors 轉換為 GGUF F16 格式
4. 量化為 Q8_0 (INT8)
5. 把最終 GGUF 輸出到 Drive (`functiongemma_gguf/`)

> **v3 的兩個關鍵修正：** (1) 以 `-DBUILD_SHARED_LIBS=OFF` 產生「靜態」`llama-quantize`，
> 避免 `libllama.so.0` 載入失敗；(2) 同步複製 llama.cpp 原始碼中的 `gguf-py/` 到 Drive 並以
> `PYTHONPATH` 指向它，解決新版 `convert_hf_to_gguf.py` 參考 `gguf.MODEL_ARCH.GEMMA4` 但 pip 版
> `gguf` 沒有該屬性的錯誤。第二次執行 notebook 會偵測舊版工具並自動重編。

### B.2 方法二：在本機 PC 完成轉換

```bash
# 1. Clone llama.cpp
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp

# 2. 安裝 Python 依賴
pip install -r requirements.txt

# 3. 轉換為 GGUF F16 (中間格式)
python convert_hf_to_gguf.py /path/to/finetuned_model \
    --outtype f16 \
    --outfile functiongemma-270m-finetuned-f16.gguf

# 4. 量化為 Q8_0 (INT8)
#    需要先編譯 llama-quantize (見下方)
./build/bin/llama-quantize \
    functiongemma-270m-finetuned-f16.gguf \
    functiongemma-270m-finetuned-q8_0.gguf \
    Q8_0
```

### B.3 也可以一步直接轉換為 Q8_0

```bash
python convert_hf_to_gguf.py /path/to/finetuned_model \
    --outtype q8_0 \
    --outfile functiongemma-270m-finetuned-q8_0.gguf
```

### B.4 量化類型選擇指南

| 類型 | 位元 | 模型大小 (約) | 品質 | 建議用途 |
|------|------|---------------|------|----------|
| F16  | 16-bit | ~540 MB | 原始品質 | 基準/測試 |
| Q8_0 | 8-bit  | ~280 MB | 接近原始 | **推薦 - RPI5 最佳選擇** |
| Q6_K | 6-bit  | ~220 MB | 很高 | 品質/大小平衡 |
| Q4_K_M | 4-bit | ~170 MB | 良好 | 記憶體受限時 |
| Q4_0 | 4-bit  | ~150 MB | 可接受 | 最快最小 |

> **建議**: 270M 模型已經很小了，Q8_0 只有 ~280MB，不需要過度壓縮。
> Q8_0 是品質與效能的最佳平衡點。

---

## Phase C: Build llama.cpp on RPI5

### C.1 系統準備

```bash
# 更新系統
sudo apt update && sudo apt upgrade -y

# 安裝編譯依賴
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
```

### C.2 編譯 llama.cpp

```bash
# Clone llama.cpp 到 ~/llama/ 底下
mkdir -p ~/llama && cd ~/llama
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp

# 使用 CMake 編譯 (啟用 OpenBLAS 加速)
cmake -B build \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_BLAS=ON \
    -DGGML_BLAS_VENDOR=OpenBLAS

# 編譯 (使用 -j3 避免 OOM，Pi 5 只有 8GB)
cmake --build build --config Release -j3
```

> **注意**: 使用 `-j3` 而非 `-j4`，因為 `-j4` 在 linking 階段可能耗盡
> 8GB RAM。編譯大約需要 5-8 分鐘 (NVMe) 或更長 (microSD)。

### C.3 驗證編譯成功

```bash
# 確認執行檔存在
ls -la build/bin/llama-cli
ls -la build/bin/llama-server
ls -la build/bin/llama-quantize

# 測試 help
./build/bin/llama-cli --help
```

### C.4 (Optional) 安裝到系統路徑

```bash
sudo cmake --install build
```

---

## Phase D: Deploy & Run on RPI5

### D.1 下載預訓練 GGUF 模型 (官方版本，用於測試)

```bash
# 在 RPI5 上建立工作目錄 (所有 llama 相關的東西都放在 ~/llama/ 底下)
mkdir -p ~/llama ~/llama/models ~/llama/models/whisper ~/llama/scripts

# 方法一：使用 huggingface-cli (推薦)
pip install huggingface-hub
huggingface-cli download \
    ggml-org/functiongemma-270m-it-GGUF \
    --local-dir ~/llama/models/functiongemma-official

# 方法二：直接下載特定量化版本
wget -O ~/llama/models/functiongemma-270m-it-q8_0.gguf \
    "https://huggingface.co/Edge-Quant/functiongemma-270m-it-Q8_0-GGUF/resolve/main/functiongemma-270m-it-q8_0.gguf"
```

### D.2 複製微調後的模型到 RPI5

```bash
# 從你的 PC/Google Drive 複製 GGUF 檔到 RPI5
# 方法一：SCP
scp functiongemma-270m-finetuned-q8_0.gguf pi@<RPI5_IP>:~/llama/models/

# 方法二：從 Google Drive (如果用 Colab 轉換)
# 先在 RPI5 上安裝 rclone 或手動下載
```

### D.3 CLI 模式運行

先把 `system_prompt.txt` (官方格式 developer block + 所有 declaration) 複製到 RPI5 的 `~/` 下，
所有測試都從這個檔組 prompt。

```bash
# 確認檔案存在；開頭應為 <start_of_turn>developer
ls -la ~/llama/system_prompt.txt
head -1 ~/llama/system_prompt.txt

# 以 system_prompt.txt + user turn 組出完整 prompt
USER_MSG="把背景換成藍色"
PROMPT="$(cat ~/llama/system_prompt.txt)
<start_of_turn>user
${USER_MSG}
<end_of_turn>
<start_of_turn>model
"

~/llama/llama.cpp/build/bin/llama-cli \
    -m ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf \
    -c 1024 -n 256 --temp 0.1 \
    -p "$PROMPT"

# 互動模式 (按 Ctrl+C 離開)
#   --system-prompt-file : 把 developer block 塞進 context 當「系統訊息」，
#                          全對話期間持續有效，user turn 仍由你鍵入
#   -co on               : 對話著色 (user / model / system 分色)
# 注意：不要用 -f，那會把檔案內容當成「初始 user prompt」灌進去，
#       模型會把 developer block 當成使用者訊息直接反應，行為錯誤。
~/llama/llama.cpp/build/bin/llama-cli \
    -m ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf \
    -c 1024 -co on \
    --system-prompt-file ~/llama/system_prompt.txt
```

### D.4 Server 模式運行 (推薦用於整合應用)

```bash
# 啟動 HTTP API server
~/llama/llama.cpp/build/bin/llama-server \
    -m ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf \
    -c 1024 \
    --host 0.0.0.0 \
    --port 8080
```

測試 API (用 `jq` 安全地把多行 prompt 包成 JSON)：
```bash
USER_MSG="把背景換成藍色"
FULL_PROMPT="$(cat ~/llama/system_prompt.txt)
<start_of_turn>user
${USER_MSG}
<end_of_turn>
<start_of_turn>model
"
curl http://localhost:8080/completion \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg p "$FULL_PROMPT" \
        '{prompt:$p, n_predict:128, temperature:0.1, stop:["<end_of_turn>"]}')"
```

### D.5 使用 Python 呼叫 (整合到你的應用)

```bash
pip install requests
```

推薦直接使用 `scripts/function_call_client.py`，它會自動讀取 `~/llama/system_prompt.txt`，
不需要在程式內維護 function 宣告：

```python
# 見 scripts/function_call_client.py 完整範例
import os, requests

SERVER_URL = "http://localhost:8080"

def load_system_prompt(path="~/llama/system_prompt.txt"):
    with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
        return f.read().rstrip()

def call_function_gemma(user_message, system_prompt=None):
    if system_prompt is None:
        system_prompt = load_system_prompt()
    prompt = (
        f"{system_prompt}\n"
        f"<start_of_turn>user\n{user_message}\n<end_of_turn>\n"
        f"<start_of_turn>model\n"
    )
    response = requests.post(f"{SERVER_URL}/completion", json={
        "prompt": prompt, "n_predict": 128,
        "temperature": 0.1, "stop": ["<end_of_turn>"]
    })
    return response.json()["content"]
```

### D.6 設定為系統服務 (開機自啟動)

```bash
# 使用提供的安裝腳本
sudo bash scripts/install_service.sh
```

或手動建立 systemd service：
```bash
sudo tee /etc/systemd/system/functiongemma.service << 'EOF'
[Unit]
Description=FunctionGemma LLM Server
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/llama
ExecStart=/home/pi/llama/llama.cpp/build/bin/llama-server \
    -m /home/pi/llama/models/functiongemma-270m-finetuned-q8_0.gguf \
    -c 1024 \
    --host 0.0.0.0 \
    --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable functiongemma
sudo systemctl start functiongemma

# 確認狀態
sudo systemctl status functiongemma
```

---

## Phase E: 替換模型

### E.1 替換為新的微調模型

整個流程設計為可重複執行。當你有新的微調模型時：

1. 在 Colab 中重新執行 Phase A (微調)
2. 在 Colab 中重新執行 Phase B (轉換+量化)
3. 將新的 `.gguf` 檔複製到 RPI5 的 `~/llama/models/`
4. 重啟服務：
   ```bash
   sudo systemctl restart functiongemma
   ```

### E.2 切換不同模型

只需修改 `-m` 參數指向不同的 GGUF 檔：
```bash
# 官方版本
~/llama/llama.cpp/build/bin/llama-cli -m ~/llama/models/functiongemma-270m-it-q8_0.gguf ...

# 你的微調版本
~/llama/llama.cpp/build/bin/llama-cli -m ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf ...
```

---

## Phase F: Voice Input (語音輸入) on RPI5

使用 **whisper.cpp** 在 RPI5 本地進行語音轉文字，再餵入 FunctionGemma 進行推論。
完全離線運作，不需要雲端 API。

### 架構

```
[USB Microphone]
      |
      v
 [arecord / sox]      錄音 (16kHz, 16-bit, mono WAV)
      |
      v
 [whisper.cpp]         語音轉文字 (本地推論, ~75-466MB 模型)
      |
      v
 [FunctionGemma]       文字轉 function call (llama.cpp)
      |
      v
 [Your Application]    執行對應動作 (GPIO, MQTT, HTTP...)
```

### F.1 硬體需求

| 項目 | 需求 |
|------|------|
| 麥克風 | USB 麥克風 (任何 UAC 相容即可，推薦帶降噪的) |
| RPI5 RAM | 8GB (Whisper base + FunctionGemma Q8_0 共約 420MB) |

> 如果使用 4GB 版本 RPI5，建議用 Whisper tiny 模型 (75MB)。

### F.2 安裝 whisper.cpp

```bash
# 使用一鍵安裝腳本
chmod +x scripts/setup_whisper.sh
./scripts/setup_whisper.sh
```

腳本會自動完成：
1. 安裝音頻依賴 (ALSA, SDL2, sox)
2. Clone 並編譯 whisper.cpp
3. 下載 Whisper 模型 (可選 tiny/base/small)
4. 測試麥克風錄音
5. 測試語音轉文字

### F.3 Whisper 模型選擇

| 模型 | 大小 | RPI5 轉錄速度 (5秒音頻) | 準確度 | 建議用途 |
|------|------|-------------------------|--------|----------|
| tiny | ~75 MB | ~1-2 秒 | 基本 | 4GB RPI5 / 簡單英文指令 |
| base | ~142 MB | ~2-4 秒 | 良好 | **推薦 - 英文/中文皆可** |
| small | ~466 MB | ~8-15 秒 | 很好 | 需要高準確度時 |

> **注意**: Whisper 支援多語言，包括中文、日文、英文等。
> 使用 `--language zh` 可指定中文以提升準確度。

### F.4 測試麥克風

```bash
# 列出錄音設備
arecord -l

# 錄製 5 秒測試音頻
arecord -d 5 -r 16000 -c 1 -f S16_LE /tmp/test.wav

# 播放 (確認有錄到聲音)
aplay /tmp/test.wav

# 轉錄測試
~/llama/whisper.cpp/build/bin/whisper-cli \
    -m ~/llama/models/whisper/ggml-base.bin \
    -f /tmp/test.wav \
    -l auto \
    --no-timestamps
```

### F.5 Voice Chat 互動模式 (語音 / 文字即時切換)

`voice_chat.py` 預設讀 `~/llama/system_prompt.txt`，並支援**語音/文字兩種輸入模式即時切換**——
擔心語音辨識失準時，按一個 `t` Enter 就能改用鍵盤打字。

```bash
# 方式一：CLI 模式 (推薦，不需啟動 server)，從語音模式開始
python3 ~/llama/scripts/voice_chat.py --mode cli \
    --model ~/llama/models/functiongemma-270m-finetuned-q8_0.gguf \
    --system-prompt ~/llama/system_prompt.txt \
    --start-mode voice

# 方式二：Server 模式
python3 ~/llama/scripts/voice_chat.py --mode server \
    --server-url http://localhost:8080 \
    --system-prompt ~/llama/system_prompt.txt

# 一開始就用文字模式 (聊天中還能按 v Enter 切回語音)
python3 ~/llama/scripts/voice_chat.py --start-mode text

# 中文語音 + small 模型 (C930 推薦)
python3 ~/llama/scripts/voice_chat.py --language zh \
    --whisper-model ~/llama/models/whisper/ggml-small.bin

# 自動靜音偵測 (說完自動停)
python3 ~/llama/scripts/voice_chat.py --input auto
```

### F.6 Voice Chat 操作方式 (每回合熱鍵)

| 按鍵 | 行為 |
|---|---|
| `Enter` | 依目前模式送出 (語音 → 錄音 / 文字 → `You:`) |
| `v` + Enter | 切到語音模式 |
| `t` + Enter | 切到文字模式 |
| 直接打一句話 | 當場用文字送出 (不改變模式，最快的 fallback) |
| `q` + Enter | 離開 (結束時自動重置終端機背景色) |

典型輸出 (顯示**原始官方格式**、解析結果、**真實執行**)：
```
--- Turn 1 ---
[語音🎤] Enter=送出 / v=語音 / t=文字 / q=離開 / 或直接輸入文字 > 把背景換成藍色
  [🤔 Thinking...]
  ┌─ 原始輸出 (official format) ──────────────
  │ <start_function_call>
  │ {"name": "change_background_color", "arguments": {"color": "blue"}}
  │ <end_function_call>
  └──────────────────────────────────────────
  🔧 解析: change_background_color({"color": "blue"})
  ⚙  執行結果: [UI] ✅ 終端機背景已改為 blue
```

> `parse_function_call()` 同時支援兩種模型輸出：純 JSON，以及
> `system_prompt.txt` 的 declaration 風格 (`name{key:<escape>value<escape>,...}`)。

### F.7 內建函式真實執行效果

`voice_chat.py` 對 `system_prompt.txt` 中宣告的 4 個函式提供了**真實副作用**的 handler：

| Function | 實際行為 |
|---|---|
| `change_background_color` | ANSI `\033[4Xm` + 清畫面，終端機背景**真的**換色 (red/green/blue/yellow/purple/cyan/white/black/orange) |
| `change_app_title` | OSC `\033]0;TITLE\007`，**真的**改終端機視窗標題 |
| `show_alert` | 印出 Unicode 邊框對話方塊 + 終端機 bell |
| `get_current_weather` | 呼叫 wttr.in HTTP API (免 API key) 拿**真實天氣** |

要新增 / 替換函式，做兩件事：

1. 在 `~/llama/system_prompt.txt` 加入新的 `<start_function_declaration>...<end_function_declaration>` 宣告 (官方格式)
2. 在 `voice_chat.py` 的 `FUNCTION_HANDLERS` 字典加上對應的 Python 函式

```python
# voice_chat.py
def _fn_turn_on_light(args):
    room = args.get("room", "?")
    import paho.mqtt.publish as publish
    publish.single(f"home/{room}/light", "ON", hostname="localhost")
    return f"[MQTT] Light ON in {room}"

FUNCTION_HANDLERS["turn_on_light"] = _fn_turn_on_light
```

> **不要**在腳本裡硬寫 function JSON — 宣告只出現在 `system_prompt.txt`，腳本只負責「執行」。

### F.8 Troubleshooting

#### 麥克風沒聲音
```bash
# 檢查設備
arecord -l

# 檢查音量 (確保 Capture 沒有被 mute)
alsamixer
# 按 F4 切換到 Capture，用方向鍵調整音量

# 如果用 PulseAudio
pactl list sources short
pactl set-source-volume @DEFAULT_SOURCE@ 100%
```

#### Whisper 轉錄不準確
- 使用更大的模型: `ggml-base.bin` -> `ggml-small.bin`
- 指定語言: `--language zh` (不要用 auto)
- 確保環境安靜，麥克風離嘴近一點
- 確保錄音是 16kHz (`-r 16000`)

#### 延遲太高
- Whisper tiny 最快 (~1-2 秒轉錄)
- FunctionGemma 270M 本身很快 (~0.5 秒推論)
- 主要瓶頸在 Whisper，整體延遲 ~2-5 秒 (base 模型)

---

## Performance Expectations (RPI5)

| 指標 | 預期值 |
|------|--------|
| FunctionGemma Q8_0 模型大小 | ~280 MB |
| Whisper base 模型大小 | ~142 MB |
| RAM 使用量 (兩者合計) | ~500-700 MB |
| FunctionGemma 推論速度 | ~20-50 tokens/sec |
| Whisper 轉錄速度 (5秒音頻, base) | ~2-4 秒 |
| 語音指令端到端延遲 | ~3-6 秒 |
| 首次模型載入時間 | 1-3 秒 |

> RPI5 (Cortex-A76 @ 2.4GHz) 跑 270M 模型綽綽有餘。
> 建議使用 8GB RAM 版本的 RPI5。

---

## Troubleshooting

### 編譯失敗: OOM
```bash
# 減少並行 jobs
cmake --build build --config Release -j2
```

### 模型載入失敗: "unknown model architecture"
```bash
# 確認 llama.cpp 版本夠新 (需要支援 Gemma 3)
cd ~/llama/llama.cpp && git pull && cmake --build build --config Release -j3
```

### 轉換失敗: "missing tokenizer files"
確保 finetuned_model 資料夾包含完整的 tokenizer 檔案：
```bash
ls finetuned_model/tokenizer.json
ls finetuned_model/tokenizer_config.json
```

### 轉換失敗: `libllama.so.0: cannot open shared object file`
`llama_cpp_tools/` 內的 `llama-quantize` 是舊的 dynamic build。執行
`02_convert_to_gguf.ipynb` (v3) 尾段的「強制重編」cell，或直接把 Drive 上的
`llama_cpp_tools/` 整個刪掉再跑一次 notebook；v3 會自動以
`-DBUILD_SHARED_LIBS=OFF` 重編出靜態版本。

### 轉換失敗: `MODEL_ARCH has no attribute 'GEMMA4'`
`convert_hf_to_gguf.py` 版本新，但 pip 的 `gguf` 套件版本舊。
`02_convert_to_gguf.ipynb` (v3) 已修正：把 `llama.cpp/gguf-py/` 整個目錄一併存到
Drive，並於執行轉換時用 `PYTHONPATH` 指向它，同時卸載 pip 版 `gguf` 避免衝突。

### 推論結果不正確
- 確認 prompt 格式正確：system prompt 直接讀 `~/llama/system_prompt.txt`，**不要**自己組 declaration
- 降低 temperature (`--temp 0.1` 或 `--temp 0`)
- 確認量化沒有過度壓縮 (用 Q8_0 而非 Q4_0)

---

## File Structure

```
rpi5/
├── README.md                              # 本文件 (完整教學)
├── FunctionGemma_RPI5_Guide.docx          # 英文版 Word 文件
├── FunctionGemma_RPI5_教學文件_繁中.docx   # 繁體中文版 Word 文件
├── RPI5_指令速查.txt                       # RPI5 端純文字指令速查
├── system_prompt.txt                       # *** 官方格式 developer block + function 宣告，所有推論腳本都讀這個 ***
├── scripts/
│   ├── 01_finetune_functiongemma.ipynb    # Phase A: Colab 微調
│   ├── 02_convert_to_gguf.ipynb          # Phase B: Colab 轉換+量化 (v3: 靜態編譯 + 綁 gguf-py)
│   ├── setup_rpi5.sh                      # Phase C: RPI5 一鍵安裝 llama.cpp
│   ├── setup_whisper.sh                   # Phase F: RPI5 一鍵安裝 whisper.cpp
│   ├── install_service.sh                 # Phase D: 安裝 systemd 服務
│   ├── function_call_client.py            # Python 文字客戶端 (自動讀 system_prompt.txt)
│   ├── voice_chat.py                      # Phase F: 語音/文字雙模式對話 + 真實函式副作用
│   └── test_inference.sh                  # 推論測試腳本
├── llama_cpp_tools/                        # Phase B 產出 (Colab 持久化)
│   ├── llama-quantize                      # 靜態編譯，不需 libllama.so
│   ├── convert_hf_to_gguf.py
│   ├── gguf-py/                            # 與 convert 腳本相符的 gguf 模組 (避免 GEMMA4 錯誤)
│   └── BUILD_INFO.txt                      # 含 build_version: v3 標記
├── functiongemma_gguf/                     # Phase B 最終輸出
│   └── functiongemma-270m-finetuned-q8_0.gguf
└── sample_data/
    └── training_data_sample.jsonl         # 訓練資料範例
```
