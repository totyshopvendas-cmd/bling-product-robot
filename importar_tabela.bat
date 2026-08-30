@echo off
title TotyShop — importar tabela de precos
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Rode primeiro iniciar_totyshop.bat
  pause
  exit /b 1
)
echo Baixando / procurando a tabela JohnDrop e importando...
cd /d "%~dp0backend"
".venv\Scripts\python.exe" -c "import asyncio, pricing_service; r=asyncio.run(pricing_service.load_now()); print(r)"
echo.
echo Se apareceu imported com um numero grande, deu certo.
pause
