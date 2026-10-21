@echo off
rem Noite do debate: publica no site de verdade (origin/main, GitHub Pages).
rem Duplo clique: abre a tela do operador no navegador, em "Pronto para comecar".
rem Esta janela mostra o que o programa faz; fechar no meio deixa tudo retomavel.
chcp 65001 >nul
title Noite do debate - publica no SITE DE VERDADE
cd /d "%~dp0.."
set PYTHONUTF8=1
set "PY=python"
if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
"%PY%" -P scripts\iniciar.py noite %*
echo.
echo O programa terminou. Pode fechar esta janela.
pause >nul
