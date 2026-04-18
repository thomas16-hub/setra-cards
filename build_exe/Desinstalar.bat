@echo off
setlocal
chcp 65001 >nul 2>&1
title Setra CARDS - Desinstalador

echo.
echo ============================================
echo   SETRA CARDS - Desinstalador
echo ============================================
echo.

set "INSTALL_DIR=%LOCALAPPDATA%\SetraCARDS"
set "DATA_DIR=%LOCALAPPDATA%\SETRA\SetraCARDS"
set "EXE_NAME=Setra-CARDS.exe"
set "SHORTCUT_NAME=Setra CARDS.lnk"
set "STARTUP_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\%SHORTCUT_NAME%"
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\%SHORTCUT_NAME%"

echo Se eliminara Setra CARDS de este equipo:
echo   * App:       %INSTALL_DIR%
echo   * Datos:     %DATA_DIR%
echo   * Accesos directos + arranque automatico
echo.
echo LA BASE DE DATOS (huespedes, tarjetas, operadores) SE BORRARA.
echo Si quieres conservarla, copia la carpeta "%DATA_DIR%" ANTES de continuar.
echo.
choice /C SN /M "Continuar (S/N)"
if errorlevel 2 exit /b 0

echo.
echo [1/4] Cerrando la app...
taskkill /F /IM "%EXE_NAME%" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/4] Eliminando accesos directos...
if exist "%DESKTOP_SHORTCUT%" del /Q "%DESKTOP_SHORTCUT%"
if exist "%STARTUP_SHORTCUT%" del /Q "%STARTUP_SHORTCUT%"

echo [3/4] Eliminando la aplicacion...
if exist "%INSTALL_DIR%" rmdir /S /Q "%INSTALL_DIR%"

echo [4/4] Eliminando datos...
if exist "%DATA_DIR%" rmdir /S /Q "%DATA_DIR%"

echo.
echo Desinstalacion completada.
pause
