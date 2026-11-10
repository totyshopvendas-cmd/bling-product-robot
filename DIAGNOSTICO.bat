@echo off
setlocal enabledelayedexpansion
title TotyShop - Diagnostico
cd /d "%~dp0"

echo ============================================
echo   TotyShop - DIAGNOSTICO
echo ============================================
echo.
echo Isto NAO abre o painel. So descobre por que ele nao abre.
echo Vai levar de 1 a 2 minutos. Nao feche a janela.
echo.

set "PY="
if exist "backend\.venv\Scripts\python.exe" (
  set "PY=%~dp0backend\.venv\Scripts\python.exe"
  echo Usando o Python do projeto (.venv)
) else (
  py -3 --version >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo Nao encontrei Python. Rode o iniciar_totyshop.bat primeiro.
  pause
  exit /b 1
)

echo Python: !PY!
echo.

"!PY!" -u "%~dp0backend\diagnostico.py"

echo.
echo ============================================
echo  PRONTO.
echo  O resultado foi salvo em:
echo    %~dp0diagnostico.log
echo.
echo  Abra esse arquivo (ou envie o texto) para
echo  saber exatamente o que esta impedindo
echo  o painel de abrir.
echo ============================================
echo.
if exist "%~dp0diagnostico.log" start "" notepad "%~dp0diagnostico.log"
pause
