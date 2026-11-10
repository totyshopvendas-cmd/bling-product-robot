@echo off
setlocal enabledelayedexpansion
title TotyShop - Atualizar
cd /d "%~dp0"

echo ============================================
echo   TotyShop - ATUALIZAR O SISTEMA
echo ============================================
echo.
echo Pasta: %~dp0
echo.
echo Isto baixa a versao mais nova do GitHub.
echo Seu arquivo .env (chaves de IA e Bling) NAO e alterado.
echo Pode demorar um pouco. Nao feche a janela.
echo.

git --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo ERRO: o Git nao esta instalado neste computador.
  echo Baixe em https://git-scm.com/download/win
  echo Instale, feche esta janela e tente de novo.
  echo.
  pause
  exit /b 1
)

echo Baixando a versao mais recente...
git fetch origin main
if errorlevel 1 (
  echo.
  echo ERRO: nao consegui falar com o GitHub. Verifique a internet.
  echo.
  pause
  exit /b 1
)

echo.
echo Aplicando a versao mais recente...
git reset --hard origin/main
if errorlevel 1 (
  echo.
  echo ERRO: nao consegui aplicar a atualizacao.
  echo Tire um print desta janela e envie para o suporte.
  echo.
  pause
  exit /b 1
)

echo.
echo ============================================
echo   ATUALIZADO COM SUCESSO!
echo.
echo   Agora abra o iniciar_totyshop.bat.
echo   Na primeira vez depois de atualizar, o
echo   painel e remontado e demora alguns minutos.
echo ============================================
echo.
pause
