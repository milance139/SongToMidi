@echo off
rem One-time setup: Python 3.11 (official, via winget if missing) + libraries in .venv inside this folder.
rem Needs internet; about 4 GB with graphics-card support.
setlocal
cd /d "%~dp0"

set "BASEPY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%BASEPY%" goto have_py
echo Installing Python 3.11 ...
winget install --id Python.Python.3.11 -e --scope user --silent --accept-package-agreements --accept-source-agreements
if not exist "%BASEPY%" goto fail
:have_py

echo.
echo === Creating environment ===
if not exist ".venv\Scripts\python.exe" "%BASEPY%" -m venv .venv || goto fail
set "PY=.venv\Scripts\python.exe"
%PY% -m pip install --upgrade pip || goto fail

set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
nvidia-smi >nul 2>nul && set "TORCH_INDEX=https://download.pytorch.org/whl/cu121"
echo.
echo === Installing PyTorch from %TORCH_INDEX% ===
%PY% -m pip install torch==2.5.1 torchaudio==2.5.1 --index-url %TORCH_INDEX% || goto fail

echo.
echo === Installing libraries ===
%PY% -m pip install -r requirements.txt || goto fail

echo.
echo === Downloading AI models ===
%PY% -c "from demucs.pretrained import get_model; get_model('htdemucs'); get_model('htdemucs_6s'); print('models ok')" || goto fail

echo.
echo === Self-test ===
%PY% -m tests.selftest || goto fail

echo.
echo Setup finished. Start the app with SongToMidi.bat
pause
exit /b 0

:fail
echo.
echo SETUP FAILED - see the messages above.
pause
exit /b 1
