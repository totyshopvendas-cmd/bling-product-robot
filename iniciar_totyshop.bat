@echo off
setlocal enabledelayedexpansion
title TotyShop Automacao
cd /d "%~dp0"

echo ============================================
echo   TotyShop - instalacao e painel local
echo ============================================
echo.
echo Pode demorar alguns minutos na PRIMEIRA vez.
echo Nas proximas vezes abre em poucos segundos.
echo.

rem ---------------------------------------------------------------
rem  1) Python - instala sozinho se faltar
rem ---------------------------------------------------------------
call :findpython
if not defined PY (
  echo Python nao encontrado. Instalando automaticamente...
  call :wingetinstall Python.Python.3.11
  set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%LOCALAPPDATA%\Programs\Python\Launcher;%ProgramFiles%\Python311;%PATH%"
  call :findpython
)
if not defined PY goto needrestart

rem ---------------------------------------------------------------
rem  2) Node.js - instala sozinho se faltar
rem ---------------------------------------------------------------
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js nao encontrado. Instalando automaticamente...
  call :wingetinstall OpenJS.NodeJS.LTS
  set "PATH=%ProgramFiles%\nodejs;%PATH%"
)
where node >nul 2>&1
if errorlevel 1 goto needrestart

rem ---------------------------------------------------------------
rem  3) Configuracao - criada sozinha (Bling e IA sao feitos no painel)
rem ---------------------------------------------------------------
if not exist "backend\.env" (
  echo Criando configuracao inicial...
  > "backend\.env" echo # Criado automaticamente pelo iniciar_totyshop.bat
  >> "backend\.env" echo # Client ID/Secret do Bling e chave de IA: preencha no painel, em Configuracoes.
  >> "backend\.env" echo APP_BASE_URL=http://127.0.0.1:8000
  >> "backend\.env" echo MONGO_URL=memory://local
  >> "backend\.env" echo DB_NAME=bling_robot
  >> "backend\.env" echo APP_SECRET=totyshop-!RANDOM!-!RANDOM!-!RANDOM!-!RANDOM!
  >> "backend\.env" echo APP_PASSWORD=
  >> "backend\.env" echo TZ_LOCAL=America/Sao_Paulo
)

rem ---------------------------------------------------------------
rem  4) Dependencias Python + navegador do robo
rem ---------------------------------------------------------------
if not exist "backend\.venv\Scripts\activate.bat" (
  echo Criando ambiente Python...
  %PY% -m venv "backend\.venv"
)
call "backend\.venv\Scripts\activate.bat"
if not exist "backend\.venv\.req-ok-v3" (
  echo Instalando dependencias Python - so na primeira vez...
  python -m pip install -q --upgrade pip
  python -m pip install -q -r "backend\requirements.txt"
  if errorlevel 1 (
    echo.
    echo ERRO ao instalar as dependencias Python. Verifique a internet e rode de novo.
    pause
    exit /b 1
  )
  echo. > "backend\.venv\.req-ok-v3"
)
if not exist "%LOCALAPPDATA%\ms-playwright" (
  echo Instalando o navegador do robo - so na primeira vez...
  python -m playwright install chromium
) else (
  echo Navegador do robo ja instalado.
)

rem ---------------------------------------------------------------
rem  5) Painel (tela)
rem ---------------------------------------------------------------
if not exist "frontend\node_modules" (
  echo Instalando dependencias do painel - so na primeira vez...
  pushd frontend
  call npm install --legacy-peer-deps --no-audit --no-fund
  popd
)

if not exist "frontend\build\index.html" goto dobuild
if not exist "frontend\build\.stamp-v3" goto dobuild
goto afterbuild
:dobuild
echo Montando o painel...
pushd frontend
set CI=false
set DISABLE_ESLINT_PLUGIN=true
set GENERATE_SOURCEMAP=false
call npm run build
popd
if not exist "frontend\build\index.html" (
  echo.
  echo ERRO ao montar o painel. Tire um print desta janela e envie para o suporte.
  pause
  exit /b 1
)
echo. > "frontend\build\.stamp-v3"
:afterbuild

rem Atalho na Area de Trabalho (so na primeira vez)
if not exist "%USERPROFILE%\Desktop\TotyShop.lnk" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\TotyShop.lnk');$s.TargetPath='%~f0';$s.WorkingDirectory='%~dp0';$s.Save()" >nul 2>&1
)

set "FRONTEND_BUILD=%~dp0frontend\build"
echo.
echo ============================================
echo  PRONTO! O Chrome vai abrir sozinho.
echo  Endereco: http://127.0.0.1:8000
echo.
echo  NAO FECHE ESTA JANELA PRETA.
echo  Se fechar, o painel desliga.
echo  Pode minimizar.
echo ============================================
echo.

rem Abre o navegador so quando o painel estiver respondendo
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command "for($i=0;$i -lt 90;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8000/ | Out-Null; break}catch{Start-Sleep 2}}; Start-Process 'http://127.0.0.1:8000/'"

cd /d "%~dp0backend"

:run
python -m uvicorn server:app --host 127.0.0.1 --port 8000
echo.
echo O painel parou. Religa em 3 segundos...
echo Feche esta janela so se quiser desligar de verdade.
timeout /t 3 /nobreak >nul
goto run


rem ===============================================================
rem  Sub-rotinas
rem ===============================================================
:findpython
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 (
  set "PY=py -3"
  goto :eof
)
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
goto :eof

:wingetinstall
where winget >nul 2>&1
if errorlevel 1 (
  echo.
  echo Nao consegui instalar sozinho neste Windows.
  echo Instale manualmente e rode este arquivo de novo:
  echo   Python: https://www.python.org/downloads/   - marque "Add python.exe to PATH"
  echo   Node.js: https://nodejs.org/
  pause
  exit
)
winget install -e --id %1 --silent --accept-package-agreements --accept-source-agreements
goto :eof

:needrestart
echo.
echo ============================================
echo  Instalacao do Python/Node concluida.
echo  FECHE esta janela e de dois cliques
echo  em iniciar_totyshop.bat DE NOVO.
echo ============================================
pause
exit /b 0
