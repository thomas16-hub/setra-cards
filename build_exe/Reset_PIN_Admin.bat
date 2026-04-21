@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Setra CARDS - Recuperar PIN Admin

echo.
echo ============================================
echo   SETRA CARDS - Recuperar PIN del Admin
echo ============================================
echo.
echo  Este script resetea el PIN del operador
echo  Admin (o el super_manager) a: 1234
echo.
echo  Al abrir la app, te pedira cambiar el PIN
echo  inmediatamente.
echo.
echo  Ningun dato (habitaciones, huespedes,
echo  tarjetas, logs) se pierde.
echo.

set "EXE=%LOCALAPPDATA%\SetraCARDS\Setra-CARDS.exe"
if not exist "%EXE%" (
    echo ERROR: No se encuentra Setra-CARDS.exe en:
    echo   %EXE%
    echo.
    echo Verifica que Setra CARDS este instalado.
    pause
    exit /b 1
)

choice /C SN /M "Confirmar reset (S/N)"
if errorlevel 2 exit /b 0

echo.
echo Cerrando Setra CARDS si esta abierto...
taskkill /F /IM "Setra-CARDS.exe" >nul 2>&1
timeout /t 2 /nobreak >nul

echo Reseteando PIN...
"%EXE%" --reset-admin

echo.
pause
