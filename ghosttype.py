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

from google import genai
from google.genai import types

# ==========================================
# 設定 (環境変数から読み込み)
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY")

if not API_KEY:
    print("[エラー] 環境変数 'GEMINI_API_KEY' が設定されていません。")
    print("専用のbatファイルから起動しているか確認してください。")
    os._exit(1)

MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")
SEND_SCREENSHOT = os.environ.get("GHOSTTYPE_SEND_SCREENSHOT", "true").lower() == "true"

client = genai.Client(api_key=API_KEY)

# 状態管理
STATE_IDLE = 0
STATE_RECORDING = 1
STATE_PROCESSING = 2
current_state = STATE_IDLE

# モード管理
MODES = [
    ("🤖", "自動(合言葉)", ""),
    ("✉️", "強制: メール", "\n\n【強制指示】今回は音声の冒頭の合言葉の有無に関わらず、強制的に「モード1：メールモード」として処理してください。"),
    ("📝", "強制: 箇条書き", "\n\n【強制指示】今回は音声の冒頭の合言葉の有無に関わらず、強制的に「モード2：箇条書きモード」として処理してください。"),
    ("❓", "強制: 質問回答", "\n\n【強制指示】今回は音声の冒頭の合言葉の有無に関わらず、強制的に「モード3：質問回答モード」として処理してください。")
]
current_mode_idx = 0  

# ==========================================
# Windows API ホットキー登録
# ==========================================
def hotkey_listener_thread():
    user32 = ctypes.windll.user32
    MOD_ALT = 0x0001           # ★ Altキー
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008           # ★ Winキーを追加
    MOD_NOREPEAT = 0x4000
    VK_SPACE = 0x20
    
    HOTKEY_RECORD = 1    
    HOTKEY_MODE = 2      

    # 録音：Ctrl + Space (そのまま)
    if not user32.RegisterHotKey(None, HOTKEY_RECORD, MOD_CONTROL | MOD_NOREPEAT, VK_SPACE):
        print("[エラー] 録音ホットキー(Ctrl+Space)の登録に失敗しました。")
        
    # ★モード切替：Win + Alt + Space に変更
    if not user32.RegisterHotKey(None, HOTKEY_MODE, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_SPACE):
        print("[エラー] モード切替ホットキー(Win+Alt+Space)の登録に失敗しました。")

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  
            if msg.wParam == HOTKEY_RECORD:
                on_hotkey_pressed()
            elif msg.wParam == HOTKEY_MODE:
                on_mode_hotkey_pressed()
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
        pass
    return pyautogui.screenshot()

# ==========================================
# GUI (tkinter) の設定とアニメーション
# ==========================================
def init_gui():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.85)
    
    window_width = 160
    window_height = 40
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    
    x = (screen_width - window_width) // 2
    y = screen_height - window_height - 60 
    
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")
    root.configure(bg='#282c34')
    
    label = tk.Label(root, text="", fg='#61afef', bg='#282c34', font=('メイリオ', 10, 'bold'))
    label.pack(expand=True, fill='both')
    
    root.withdraw()
    return root, label

hide_timer_id = None

def update_ui(text, color='#61afef', show=True, auto_hide=False):
    def _update():
        global hide_timer_id
        if hide_timer_id is not None:
            root.after_cancel(hide_timer_id)
            hide_timer_id = None
            
        label.config(text=text, fg=color)
        
        if show:
            root.deiconify()
        else:
            root.withdraw()
            
        if auto_hide:
            hide_timer_id = root.after(2000, lambda: root.withdraw())
            
    root.after(0, _update)

anim_frames = ["⏳ 処理中", "⏳ 処理中.", "⌛ 処理中..", "⌛ 処理中..."]
anim_idx = 0

def animate_processing():
    global current_state, anim_idx
    if current_state == STATE_PROCESSING:
        text = anim_frames[anim_idx % len(anim_frames)]
        label.config(text=text, fg='#e5c07b')
        anim_idx += 1
        root.after(400, animate_processing)

# ==========================================
# 処理ロジック
# ==========================================
def record_and_process():
    global current_state
    
    try:
        # 1. 録音処理
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000 
        
        p = pyaudio.PyAudio()
        stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
        frames = []
        
        print("\n[録音中...]")
        
        # ★ ここで録音開始時間を記録
        record_start_time = time.time()
        
        while current_state == STATE_RECORDING:
            data = stream.read(CHUNK)
            frames.append(data)
            
        stream.stop_stream()
        stream.close()
        p.terminate()
        
        # ★ 録音にかかった時間を計算
        record_duration = time.time() - record_start_time
        
        # ★ 1秒未満ならキャンセル扱いにして終了（画像処理やAPI送信に行かせない）
        if record_duration < 1.0:
            print(f"[キャンセル] 録音時間が短すぎます（{record_duration:.2f}秒）。")
            update_ui("🚫 キャンセル", color='#abb2bf', show=True, auto_hide=True)
            return

        print("[処理中...]")
        
        # 2. 画像データの準備 (キャンセルされなかった場合のみ実行)
        img_part = None
        if SEND_SCREENSHOT:
            screenshot = capture_active_window()
            screenshot.thumbnail((1024, 1024))
            if screenshot.mode != 'RGB':
                screenshot = screenshot.convert('RGB')
            img_byte_arr = io.BytesIO()
            screenshot.save(img_byte_arr, format='JPEG', quality=85)
            
            img_part = types.Part.from_bytes(
                data=img_byte_arr.getvalue(),
                mime_type="image/jpeg"
            )
        
        # 3. 音声データのメモリ展開
        audio_io = io.BytesIO()
        with wave.open(audio_io, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
        
        audio_part = types.Part.from_bytes(
            data=audio_io.getvalue(),
            mime_type="audio/wav"
        )
        
        # 4. 外部プロンプトファイルの読み込み
        prompt_file_path = "prompt.txt"
        if os.path.exists(prompt_file_path):
            with open(prompt_file_path, "r", encoding="utf-8") as f:
                base_prompt = f.read()
        else:
            print("[警告] prompt.txt が見つかりません。デフォルトのプロンプトを使用します。")
            base_prompt = "ユーザーの音声指示に基づき、入力すべきテキストのみを出力してください。"
        
        forced_instruction = MODES[current_mode_idx][2]
        final_prompt = base_prompt + forced_instruction

        request_data = [final_prompt]
        if img_part:
            request_data.append(img_part)
        request_data.append(audio_part)
        
        # 5. Gemini APIへの送信
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=request_data
        )
        
        try:
            result_text = response.text.strip()
            if not result_text:
                raise ValueError("Empty text")
        except ValueError:
            print("\n[警告] AIがテキストを生成しませんでした。")
            update_ui("生成スキップ", color='#abb2bf', show=True, auto_hide=True)
            return
            
        print(f"[完了] 生成されたテキスト: \n{result_text}")
        
        # 6. クリップボードへのコピーと貼り付け
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
        # キャンセルされた場合もエラーの場合も、確実に状態をIDLEに戻す
        current_state = STATE_IDLE

def on_mode_hotkey_pressed():
    global current_mode_idx
    if current_state != STATE_IDLE:
        return 
    
    current_mode_idx = (current_mode_idx + 1) % len(MODES)
    icon, mode_name, _ = MODES[current_mode_idx]
    
    update_ui(f"➔ {icon} {mode_name}", color='#e6e6e6', show=True, auto_hide=True)

def on_hotkey_pressed():
    global current_state, anim_idx
    
    if current_state == STATE_IDLE:
        current_state = STATE_RECORDING
        current_icon = MODES[current_mode_idx][0]
        update_ui(f"🎙️ [{current_icon}] 録音中...", color='#ff5555', show=True, auto_hide=False)
        threading.Thread(target=record_and_process, daemon=True).start()
        
    elif current_state == STATE_RECORDING:
        current_state = STATE_PROCESSING
        anim_idx = 0
        animate_processing()
        
    elif current_state == STATE_PROCESSING:
        print("[無視] 現在処理中です。しばらくお待ちください...")

if __name__ == "__main__":
    print("==================================================")
    print(" GhostType 待機中...")
    print(" 【Ctrl + Space】  : 録音の開始 / 停止")
    print(" 【Win + Alt + Space】 : モードの切り替え（自動・強制）")
    print(" 終了するにはこのコンソールを閉じてください。")
    print("==================================================")
    
    root, label = init_gui()
    threading.Thread(target=hotkey_listener_thread, daemon=True).start()
    root.mainloop()
