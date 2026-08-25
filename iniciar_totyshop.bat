@echo off
setlocal enabledelayedexpansion
title TotyShop Automacao - Inicializador
set ROOT=%~dp0
set LOGDIR=%ROOT%logs
set MONGOD="C:\Program Files\MongoDB\Server\7.0\bin\mongod.exe"
set MONGODATA=%USERPROFILE%\.totyshop\mongo-data
set MONGOLOG=%USERPROFILE%\.totyshop\mongod.log
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if not exist "%USERPROFILE%\.totyshop" mkdir "%USERPROFILE%\.totyshop"

echo ============================================
echo   Iniciando TotyShop Automacao...
echo ============================================

REM ---------- Banco de dados (MongoDB, sem servico e sem admin) ----------
netstat -ano | findstr ":27017 " | findstr LISTENING >nul
if errorlevel 1 (
    REM Garante que o servico antigo nao dispute a porta com dados desatualizados
    sc query MongoDB | find "RUNNING" >nul
    if not errorlevel 1 (
        echo Parando servico MongoDB antigo...
        sc stop MongoDB >nul 2>&1
        timeout /t 5 /nobreak >nul
    )
    if not exist "%MONGODATA%" mkdir "%MONGODATA%"
    del "%MONGODATA%\mongod.lock" >nul 2>&1
    echo Iniciando banco de dados...
    start "TotyShop MongoDB" /min %MONGOD% --dbpath "%MONGODATA%" --port 27017 --bind_ip 127.0.0.1 --logpath "%MONGOLOG%" --logappend
) else (
    echo Banco de dados ja esta rodando.
)

REM ---------- Backend (porta 8000) ----------
netstat -ano | findstr ":8000 " | findstr LISTENING >nul
if errorlevel 1 (
    echo Iniciando backend...
    start "TotyShop Backend" /min cmd /c "cd /d %ROOT%backend && python -m uvicorn server:app --host 127.0.0.1 --port 8000 > "%LOGDIR%\backend.log" 2>&1"
) else (
    echo Backend ja esta rodando.
)

REM ---------- Frontend (porta 3000) ----------
netstat -ano | findstr ":3000 " | findstr LISTENING >nul
if errorlevel 1 (
    echo Iniciando painel...
    start "TotyShop Frontend" /min cmd /c "cd /d %ROOT%frontend && set BROWSER=none&& npm start > "%LOGDIR%\frontend.log" 2>&1"
) else (
    echo Painel ja esta rodando.
)

if "%~1"=="/silencioso" exit

echo.
echo Aguardando o painel ficar pronto (a primeira vez leva 3 a 5 minutos)...

REM Espera ate 10 minutos (120 tentativas x 5s)
set /a TENTATIVAS=0
:aguardar
timeout /t 5 /nobreak >nul
set /a TENTATIVAS+=1
curl -s -o nul http://127.0.0.1:3000/ 2>nul
if not errorlevel 1 goto pronto
REM Se a janela do painel morreu, aborta em vez de esperar para sempre
tasklist /fi "windowtitle eq TotyShop Frontend*" 2>nul | find /i "cmd.exe" >nul
if errorlevel 1 goto falhou
if !TENTATIVAS! GEQ 120 goto falhou
goto aguardar

:pronto
echo.
echo ============================================
echo   Tudo pronto! Abrindo o painel...
echo ============================================
start http://127.0.0.1:3000/
timeout /t 3 /nobreak >nul
exit

:falhou
echo.
echo ============================================
echo   [ERRO] O painel nao ficou disponivel.
echo ============================================
echo Registros de erro:
echo   %LOGDIR%\frontend.log
echo   %LOGDIR%\backend.log
echo   %MONGOLOG%
echo.
echo Ultimas linhas do painel:
if exist "%LOGDIR%\frontend.log" powershell -NoProfile -Command "Get-Content '%LOGDIR%\frontend.log' -Tail 15"
echo.
pause
exit /b 1
