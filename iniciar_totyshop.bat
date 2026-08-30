@echo off
setlocal enabledelayedexpansion
title TotyShop Automacao
cd /d "%~dp0"

echo ============================================
echo   TotyShop — instalacao e painel local
echo ============================================
echo.

where py >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Instale Python 3.11+ em https://www.python.org/downloads/
    echo Marque "Add python.exe to PATH".
    pause
    exit /b 1
  )
  set "PY=python"
)

where node >nul 2>&1
if errorlevel 1 (
  echo Instale Node.js 18+ em https://nodejs.org/
  pause
  exit /b 1
)

if not exist "backend\.env" (
  copy "backend\.env.example" "backend\.env" >nul
  echo Arquivo backend\.env criado. Preencha Client ID / Secret do Bling.
  echo APP_BASE_URL=http://127.0.0.1:8000
  echo MONGO_URL=memory://local
  notepad "backend\.env"
  echo Salve o arquivo e pressione uma tecla...
  pause >nul
)

findstr /b "APP_BASE_URL=" "backend\.env" | findstr /i "127.0.0.1 localhost" >nul
if errorlevel 1 (
  echo Dica: para este atalho use APP_BASE_URL=http://127.0.0.1:8000 no .env
)

if not exist "backend\.venv" (
  echo Criando ambiente Python...
  %PY% -m venv "backend\.venv"
)
call "backend\.venv\Scripts\activate.bat"
echo Instalando dependencias Python...
python -m pip install -q -r "backend\requirements.txt"
echo Instalando Chromium do robô (primeira vez demora)...
python -m playwright install chromium

where npm >nul 2>&1
if errorlevel 1 (
  echo npm nao encontrado. Reinstale o Node.js.
  pause
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo Instalando dependencias do painel...
  pushd frontend
  call npm install --legacy-peer-deps
  popd
)

if not exist "frontend\build\index.html" (
  echo Compilando o painel...
  pushd frontend
  set CI=false
  set DISABLE_ESLINT_PLUGIN=true
  set GENERATE_SOURCEMAP=false
  call npm run build
  popd
)

set "FRONTEND_BUILD=%~dp0frontend\build"
echo.
echo Abrindo http://127.0.0.1:8000
echo A tabela de precos e lida sozinha de data\ ou da pasta da calculadora.
echo No Bling, Dados basicos, cole:
echo   http://127.0.0.1:8000/api/bling/callback
echo.
start "" "http://127.0.0.1:8000/"
cd /d "%~dp0backend"
python -m uvicorn server:app --host 127.0.0.1 --port 8000
