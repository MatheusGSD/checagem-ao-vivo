@echo off
rem Ensaio: publica so numa copia local do site; retoma o ensaio anterior, se houver.
rem Duplo clique: abre a tela do operador no navegador, em "Pronto para comecar".
rem Esta janela mostra o que o programa faz; fechar no meio deixa tudo retomavel.
chcp 65001 >nul
title Ensaio - copia local do site, nada vai para o GitHub
cd /d "%~dp0.."
set PYTHONUTF8=1
set "PY=python"
if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
"%PY%" -P scripts\iniciar.py ensaio %*
echo.
echo O programa terminou. Pode fechar esta janela.
pause >nul
