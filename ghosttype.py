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

client = genai.Client(api_key=API_KEY)

# 状態管理
STATE_IDLE = 0
STATE_RECORDING = 1
STATE_PROCESSING = 2
current_state = STATE_IDLE

# ==========================================
# モード管理 (アイコン, 表示名, プロンプト指示, クリップボード使用, スクショ使用, 表示カラー)
# ==========================================
MODES = [
    # --- 口述グループ (白: #ffffff) ---
    ("💬", "口述のみ", 
     "\n\n【指示】ユーザーの音声を文字起こししてください。フィラー（えー、あの等）を除去し、適切な句読点を補ってください。ただし、AIとしての回答や文章の創作は絶対にせず、発話内容の清書のみを行ってください。", 
     False, False, "#ffffff"),
     
    ("🗯", "口述(口語)", 
     "\n\n【指示・重要】ユーザーの音声を文字起こししてください。フィラーや言い間違えを除去し、適切な句読点を補いますが、語尾を丁寧語（です・ます等）に変換したり整えすぎたりせず、できる限り発話された口語表現（「なんだよね」など）に忠実に文字起こしをしてください。AIとしての回答はしないでください。", 
     False, False, "#ffffff"),
     
    ("💬📋", "口述+クリップ", 
     "\n\n【指示】ユーザーの音声を文字起こしし、フィラー除去と句読点補正を行ってください。以下の【クリップボードの内容】を参照情報として活用し、専門用語や文脈の正確性を高めてください。AIとしての回答はしないでください。", 
     True, False, "#ffffff"),
     
    ("💬🖼", "口述+スクショ", 
     "\n\n【指示】ユーザーの音声を文字起こしし、フィラー除去と句読点補正を行ってください。添付のスクリーンショット画像を参照情報として活用し、専門用語や文脈の正確性を高めてください。AIとしての回答はしないでください。", 
     False, True, "#ffffff"),
     
    # --- メールグループ (明るい黄色: #ffeb3b) ---
    ("✉", "メール", 
     "\n\n【指示】ユーザーの音声内容をもとに、ビジネスメールの形式（挨拶、結びなど）を補完して出力してください。AIとしての直接の回答は含めず、メール本文のみを出力してください。", 
     False, False, "#ffeb3b"),
     
    ("✉📋", "メール+クリップ", 
     "\n\n【指示】以下の【クリップボードの内容】を受信メールとみなし、ユーザーの音声内容をもとに、その受信メールに対する返信メールを作成してください。ビジネスメールの形式を補完し、メール本文のみを出力してください。", 
     True, False, "#ffeb3b"),
     
    # --- AIグループ (明るいグリーン: #4ade80) ---
    ("🤖", "AI", 
     "\n\n【指示】ユーザーの音声はあなた（AI）への質問・要求です。これに対する回答のみを出力してください。", 
     False, False, "#4ade80"),
     
    ("🤖📋", "AI+クリップ", 
     "\n\n【指示】ユーザーの音声はあなた（AI）への質問・要求です。以下の【クリップボードの内容】を参照情報として利用し、回答のみを出力してください。", 
     True, False, "#4ade80"),
     
    ("🤖🖼", "AI+スクショ", 
     "\n\n【指示】ユーザーの音声はあなた（AI）への質問・要求です。添付のスクリーンショット画像を参照情報として利用し、回答のみを出力してください。", 
     False, True, "#4ade80")
]
current_mode_idx = 0  
mode_ui_visible_until = 0

# ==========================================
# Windows API ホットキー登録
# ==========================================
def hotkey_listener_thread():
    user32 = ctypes.windll.user32
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_WIN = 0x0008
    MOD_NOREPEAT = 0x4000
    VK_SPACE = 0x20
    
    HOTKEY_RECORD = 1    
    HOTKEY_MODE = 2      

    if not user32.RegisterHotKey(None, HOTKEY_RECORD, MOD_CONTROL | MOD_NOREPEAT, VK_SPACE):
        print("[エラー] 録音ホットキー(Ctrl+Space)の登録に失敗しました。")
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
    
    window_width = 180  
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
    
    # リストから6つの要素（色を含む）を取り出す
    icon, mode_name, instruction, use_clip, use_screen, mode_color = MODES[current_mode_idx]
    
    try:
        CHUNK = 1024
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000 
        
        p = pyaudio.PyAudio()
        stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)
        frames = []
        
        print("\n[録音中...]")
        record_start_time = time.time()
        
        while current_state == STATE_RECORDING:
            data = stream.read(CHUNK)
            frames.append(data)
            
        stream.stop_stream()
        stream.close()
        p.terminate()
        
        record_duration = time.time() - record_start_time
        if record_duration < 1.0:
            print(f"[キャンセル] 録音時間が短すぎます（{record_duration:.2f}秒）。")
            update_ui("🚫 キャンセル", color='#abb2bf', show=True, auto_hide=True)
            return

        print("[処理中...]")
        
        img_part = None
        if use_screen:
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
            
        clip_text_appended = ""
        if use_clip:
            try:
                clip_text = pyperclip.paste()
                if clip_text.strip():
                    clip_text_appended = f"\n\n【クリップボードの内容】\n{clip_text}"
            except Exception as e:
                print(f"[警告] クリップボードの取得に失敗: {e}")
        
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
        
        prompt_file_path = "prompt.txt"
        if os.path.exists(prompt_file_path):
            with open(prompt_file_path, "r", encoding="utf-8") as f:
                base_prompt = f.read()
        else:
            base_prompt = ""
            
        final_prompt = base_prompt + instruction + clip_text_appended

        request_data = [final_prompt]
        if img_part:
            request_data.append(img_part)
        request_data.append(audio_part)
        
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

def on_mode_hotkey_pressed():
    global current_mode_idx, mode_ui_visible_until
    if current_state != STATE_IDLE:
        return 
    
    now = time.time()
    
    if now < mode_ui_visible_until:
        current_mode_idx = (current_mode_idx + 1) % len(MODES)
        prefix = "➔ "
    else:
        prefix = "👀 "
        
    mode_ui_visible_until = now + 2.0
    
    # 色も取り出してUI更新に渡す
    icon, mode_name, _, _, _, mode_color = MODES[current_mode_idx]
    update_ui(f"{prefix}{icon} {mode_name}", color=mode_color, show=True, auto_hide=True)

def on_hotkey_pressed():
    global current_state, anim_idx
    
    if current_state == STATE_IDLE:
        current_state = STATE_RECORDING
        current_icon = MODES[current_mode_idx][0]
        # 録音中は目立つように赤色のままにしています
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
    print(" 【Ctrl + Space】      : 録音の開始 / 停止")
    print(" 【Win + Alt + Space】 : モード確認（続けて押すと切替）")
    print(" 終了するにはこのコンソールを閉じてください。")
    print("==================================================")
    
    root, label = init_gui()
    threading.Thread(target=hotkey_listener_thread, daemon=True).start()
    root.mainloop()
