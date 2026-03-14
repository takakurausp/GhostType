@echo off
rem ==========================================
rem GhostType 起動用バッチファイル
rem ==========================================

rem 変数の影響範囲をこのバッチファイルの中だけに限定する
setlocal

rem ↓↓↓ ここにご自身のGemini APIキーを貼り付けてください（自分だけが使うPCの場合のみ） ↓↓↓
set GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

rem 複数人で使用するPCの場合は、ユーザー環境変数GEMINI_API_KEYにAPIキーをセットしてください

rem ↓↓↓ 使用するGeminiのモデル名を指定してください ↓↓↓
rem （例: gemini-2.5-flash, gemini-3-flash-preview または gemini-3.1-flash-lite-previewなど）
rem set GEMINI_MODEL_NAME=gemini-2.5-flash
rem set GEMINI_MODEL_NAME=gemini-3-flash-preview
set GEMINI_MODEL_NAME=gemini-3.1-flash-lite-preview

rem ↓↓↓ スクリーンショットをAIに送信するかどうか (true または false) ↓↓↓
rem falseにすると、画面の文脈は読めなくなりますが、機密性が高まり処理速度が爆速になります。
set GHOSTTYPE_SEND_SCREENSHOT=false

echo ==========================================
echo GhostType を起動しています...
echo APIキー: セット完了
echo モデル  : %GEMINI_MODEL_NAME%
echo スクショ送信: %GHOSTTYPE_SEND_SCREENSHOT%
echo ==========================================

rem Pythonスクリプトの実行
python ghosttype.py

rem 終了時に変数を破棄して環境を元に戻す
endlocal

pause
