import os
import time
import wave
import io
import threading
import tkinter as tk
import ctypes
from ctypes import wintypes

import pyaudio
import pyautogui
import pyperclip
from PIL import ImageGrab
import google.generativeai as genai

# ==========================================
# 設定 (環境変数から読み込み)
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("[エラー] 環境変数 'GEMINI_API_KEY' が設定されていません。")
    print("専用のbatファイルから起動しているか確認してください。")
    os._exit(1)

MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")
# スクショ送信フラグ (文字列の "true" を真偽値に変換)
SEND_SCREENSHOT = os.environ.get("GHOSTTYPE_SEND_SCREENSHOT", "true").lower() == "true"

genai.configure(api_key=API_KEY)
model = genai.GenerativeModel(MODEL_NAME)

# 状態管理
STATE_IDLE = 0
STATE_RECORDING = 1
STATE_PROCESSING = 2
current_state = STATE_IDLE

# ==========================================
# Windows API ホットキー登録
# ==========================================
def hotkey_listener_thread():
    user32 = ctypes.windll.user32
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    VK_SPACE = 0x20
    HOTKEY_ID = 1

    if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_NOREPEAT, VK_SPACE):
        print("[エラー] ホットキー(Ctrl+Space)の登録に失敗しました。")
        return

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            on_hotkey_pressed()
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

# ==========================================
# アクティブウィンドウ取得処理
# ==========================================
def capture_active_window():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            bbox = (rect.left, rect.top, rect.right, rect.bottom)
            return ImageGrab.grab(bbox=bbox)
    except Exception as e:
        print(f"[警告] アクティブウィンドウ取得失敗: {e}")
    return pyautogui.screenshot()

# ==========================================
# GUI (tkinter) の設定とアニメーション
# ==========================================
def init_gui():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    
    window_width = 200
    window_height = 60
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    
    x = (screen_width - window_width) // 2
    y = screen_height - window_height - 100 
    
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.configure(bg='#282c34')
    
    label = tk.Label(root, text="", fg='#61afef', bg='#282c34', font=('メイリオ', 14, 'bold'))
    label.pack(expand=True, fill='both')
    
    root.withdraw()
    return root, label

def update_ui(text, color='#61afef', show=True, auto_hide=False):
    def _update():
        label.config(text=text, fg=color)
        if show:
            root.deiconify()
        else:
            root.withdraw()
        if auto_hide:
            root.after(2000, lambda: root.withdraw())
    root.after(0, _update)

anim_frames = ["⏳ 処理中", "⏳ 処理中.", "⌛ 処理中..", "⌛ 処理中..."]
anim_idx = 0

def animate_processing():
    global current_state, anim_idx
    if current_state == STATE_PROCESSING:
        text = anim_frames[anim_idx % len(anim_frames)]
        label.config(text=text, fg='#e5c07b')
        root.deiconify()
        anim_idx += 1
        root.after(400, animate_processing)

# ==========================================
# 処理ロジック
# ==========================================
def record_and_process():
    global current_state
    
    # 1. 画像データの準備 (フラグがTrueの時だけ撮影・処理して時間を節約)
    img_blob = None
    if SEND_SCREENSHOT:
        screenshot = capture_active_window()
        screenshot.thumbnail((1024, 1024))
        if screenshot.mode != 'RGB':
            screenshot = screenshot.convert('RGB')
        img_byte_arr = io.BytesIO()
        screenshot.save(img_byte_arr, format='JPEG', quality=85)
        img_blob = {
            "mime_type": "image/jpeg",
            "data": img_byte_arr.getvalue()
        }
    
    # 2. 録音処理
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000 
    
    p = pyaudio.PyAudio()
    stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
    frames = []
    
    print("\n[録音中...]")
    while current_state == STATE_RECORDING:
        data = stream.read(CHUNK)
        frames.append(data)
        
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    print("[処理中...]")
    
    # 3. 音声データのメモリ展開
    audio_io = io.BytesIO()
    with wave.open(audio_io, 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
    
    audio_blob = {
        "mime_type": "audio/wav",
        "data": audio_io.getvalue()
    }
    
    try:
        # 4. 外部プロンプトファイルの読み込み
        prompt_file_path = "prompt.txt"
        if os.path.exists(prompt_file_path):
            with open(prompt_file_path, "r", encoding="utf-8") as f:
                prompt = f.read()
        else:
            print("[警告] prompt.txt が見つかりません。デフォルトのプロンプトを使用します。")
            prompt = "ユーザーの音声指示に基づき、入力すべきテキストのみを出力してください。"
        
        # 5. APIリクエストデータの動的組み立て
        request_data = [prompt]
        if img_blob:
            request_data.append(img_blob) # 画像があれば追加
        request_data.append(audio_blob)   # 音声を追加
        
        # 6. Gemini APIへ送信
        response = model.generate_content(request_data)
        
        try:
            result_text = response.text.strip()
            if not result_text:
                raise ValueError("Empty text")
        except ValueError:
            print("\n[警告] AIがテキストを生成しませんでした。")
            update_ui("生成スキップ", color='#abb2bf', show=True, auto_hide=True)
            return
            
        print(f"[完了] 生成されたテキスト: \n{result_text}")
        
        # 7. クリップボードへコピーと貼り付け
        pyperclip.copy(result_text)
        update_ui("✨ 処理終了", color='#98c379', show=True, auto_hide=True)
        
        time.sleep(0.3) 
        pyautogui.keyUp('ctrl')
        pyautogui.keyUp('shift')
        pyautogui.keyUp('alt')
        pyautogui.hotkey('ctrl', 'v')
        
    except Exception as e:
        print(f"[エラーが発生しました]: {e}")
        update_ui("❌ エラー発生", color='#e06c75', show=True, auto_hide=True)
    finally:
        current_state = STATE_IDLE

def on_hotkey_pressed():
    global current_state, anim_idx
    
    if current_state == STATE_IDLE:
        current_state = STATE_RECORDING
        update_ui("🎙️ 録音中...", color='#ff5555', show=True)
        threading.Thread(target=record_and_process, daemon=True).start()
        
    elif current_state == STATE_RECORDING:
        current_state = STATE_PROCESSING
        anim_idx = 0
        animate_processing()
        
    elif current_state == STATE_PROCESSING:
        print("[無視] 現在処理中です。しばらくお待ちください...")

# ==========================================
# メイン実行部
# ==========================================
if __name__ == "__main__":
    print("待機中です... 【Ctrl + Space】 を押して操作してください。")
    print("終了するにはこのコンソールを閉じてください。")
    
    root, label = init_gui()
    threading.Thread(target=hotkey_listener_thread, daemon=True).start()
    root.mainloop()
