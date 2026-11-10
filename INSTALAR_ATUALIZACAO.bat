@echo off
setlocal enabledelayedexpansion
title TotyShop - Instalar atualizacao
cd /d "%~dp0"

set "SHA=cfafaf5814872c58aa6e5c7c25ce0818e6e8a93c"
set "URL1=https://github.com/totyshopvendas-cmd/bling-product-robot/raw/%SHA%/atualizacao_robo_dropper_planilha.zip"
set "URL2=https://api.github.com/repos/totyshopvendas-cmd/bling-product-robot/contents/atualizacao_robo_dropper_planilha.zip?ref=%SHA%"
set "ZIP=%TEMP%\totyshop_att.zip"

echo ============================================
echo   TotyShop - INSTALAR A ATUALIZACAO
echo ============================================
echo.
echo Este arquivo encontra sozinho a pasta do
echo projeto e instala a versao nova.
echo.

rem ---------- 1) achar a pasta do projeto ----------
set "PROJ="
if exist "%~dp0backend\server.py" set "PROJ=%~dp0"
if defined PROJ goto achou

echo Procurando a pasta do projeto no seu usuario...
echo (pode levar alguns segundos)
echo.
for /f "delims=" %%F in ('dir /s /b "%USERPROFILE%\iniciar_totyshop.bat" 2^>nul') do (
  if not defined PROJ (
    if exist "%%~dpFbackend\server.py" set "PROJ=%%~dpF"
  )
)
if defined PROJ goto achou

echo.
echo Nao achei sozinho. Sem problema:
echo.
echo ARRASTE a pasta do projeto para esta janela
echo (a pasta que tem o iniciar_totyshop.bat)
echo e aperte ENTER.
echo.
set /p "PROJ=Cole ou arraste aqui: "
if not defined PROJ goto erro
set PROJ=%PROJ:"=%

:achou
if not "%PROJ:~-1%"=="\" set "PROJ=%PROJ%\"
if not exist "%PROJ%backend\server.py" (
  echo.
  echo ERRO: nesta pasta nao encontrei o projeto
  echo (falta backend\server.py).
  echo Pasta escolhida: %PROJ%
  goto erro
)

echo Projeto encontrado:
echo   %PROJ%
echo.

rem ---------- 2) aviso do painel aberto ----------
echo IMPORTANTE: se a janela preta do painel estiver
echo aberta, feche ela agora pelo X.
echo.
pause

rem ---------- 3) baixar ----------
echo Baixando a versao nova (753 KB)...
if exist "%ZIP%" del /q "%ZIP%" >nul 2>&1

set "OK="
for /l %%T in (1,1,3) do (
  if not defined OK (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri '%URL1%' -OutFile '%ZIP%' -UseBasicParsing; exit 0 } catch { exit 1 }" >nul 2>&1
    if exist "%ZIP%" set "OK=1"
  )
  if not defined OK (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $r=Invoke-RestMethod -Uri '%URL2%' -Headers @{'User-Agent'='TotyShop'}; [IO.File]::WriteAllBytes('%ZIP%', [Convert]::FromBase64String($r.content)); exit 0 } catch { exit 1 }" >nul 2>&1
    if exist "%ZIP%" set "OK=1"
  )
  if not defined OK timeout /t 3 /nobreak >nul
)

if not exist "%ZIP%" (
  echo.
  echo ERRO: nao consegui baixar.
  echo Verifique a internet e tente de novo.
  goto erro
)
for %%S in ("%ZIP%") do echo Baixado: %%~zS bytes
echo.

rem ---------- 4) extrair ----------
echo Instalando os arquivos...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%PROJ%' -Force; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo.
  echo ERRO: nao consegui extrair o arquivo.
  goto erro
)

if not exist "%PROJ%backend\dropper_reader.py" (
  echo.
  echo ERRO: os arquivos nao entraram.
  goto erro
)

echo.
echo ============================================
echo   INSTALADO COM SUCESSO
echo ============================================
echo.
echo Arquivos novos no lugar. O painel sera
echo remontado agora (2 a 5 minutos).
echo.
echo Depois que o Chrome abrir: menu esquerdo,
echo "Robo Dropper", e clique em Diagnostico.
echo.
pause

rem ---------- 5) abrir o painel ----------
if exist "%PROJ%frontend\build\.stamp-v4" del /q "%PROJ%frontend\build\.stamp-v4"
cd /d "%PROJ%"
call "%PROJ%iniciar_totyshop.bat"
goto fim

:erro
echo.
echo ============================================
echo   NAO DEU CERTO
echo ============================================
echo.
echo Tire um print desta janela e envie.
echo.
pause
exit /b 1

:fim
endlocal
