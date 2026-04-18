@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Setra CARDS - Instalador

echo.
echo ============================================
echo   SETRA CARDS v2 - Instalador
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "INSTALL_DIR=%LOCALAPPDATA%\SetraCARDS"
set "EXE_NAME=Setra-CARDS.exe"
set "SHORTCUT_NAME=Setra CARDS.lnk"
set "STARTUP_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\%SHORTCUT_NAME%"
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\%SHORTCUT_NAME%"

if not exist "%SCRIPT_DIR%%EXE_NAME%" (
    echo ERROR: No se encuentra %EXE_NAME% junto a este instalador.
    pause
    exit /b 1
)

echo Selecciona el HOTEL para este PC:
echo.
echo   [1] Caney Turbo
echo   [2] Confort Turbo
echo   [3] Plaza Apartado
echo   [4] Porton Sabaneta
echo   [5] Ruisenor Itagui
echo   [6] Torre Primavera Laureles
echo   [7] Vincci Chigorodo
echo.
choice /C 1234567 /M "Hotel"
set "C=%errorlevel%"

if %C%==1 set "HOTEL_SLUG=caney-turbo" & set "HOTEL_NAME=Caney Turbo"
if %C%==2 set "HOTEL_SLUG=confort-turbo" & set "HOTEL_NAME=Confort Turbo"
if %C%==3 set "HOTEL_SLUG=plaza-apartado" & set "HOTEL_NAME=Plaza Apartado"
if %C%==4 set "HOTEL_SLUG=porton-sabaneta" & set "HOTEL_NAME=Porton Sabaneta"
if %C%==5 set "HOTEL_SLUG=ruisenor" & set "HOTEL_NAME=Ruisenor"
if %C%==6 set "HOTEL_SLUG=torre-primavera" & set "HOTEL_NAME=Torre Primavera"
if %C%==7 set "HOTEL_SLUG=vincci-chigorodo" & set "HOTEL_NAME=Vincci Chigorodo"

if not exist "%SCRIPT_DIR%hoteles\%HOTEL_SLUG%.json" (
    echo ERROR: No existe el archivo de configuracion del hotel %HOTEL_SLUG%.
    pause
    exit /b 1
)

echo.
echo Hotel seleccionado: %HOTEL_NAME%
echo Destino: %INSTALL_DIR%
echo.
choice /C SN /M "Confirmar instalacion (S/N)"
if errorlevel 2 exit /b 0

echo.
echo [1/5] Copiando archivos...
if exist "%INSTALL_DIR%" (
    taskkill /F /IM "%EXE_NAME%" >nul 2>&1
    timeout /t 2 /nobreak >nul
)
robocopy "%SCRIPT_DIR%." "%INSTALL_DIR%" /E /NFL /NDL /NJH /NJS /NP /XD hoteles /XF Instalar.bat Desinstalar.bat LEEME.txt >nul
if errorlevel 8 (
    echo ERROR copiando archivos.
    pause
    exit /b 1
)

echo [2/5] Configurando hotel %HOTEL_NAME%...
copy /Y "%SCRIPT_DIR%hoteles\%HOTEL_SLUG%.json" "%INSTALL_DIR%\hotel.json" >nul
if errorlevel 1 (
    echo ERROR copiando hotel.json.
    pause
    exit /b 1
)

echo [3/5] Creando acceso directo en el Escritorio...
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%DESKTOP_SHORTCUT%');" ^
  "$s.TargetPath = '%INSTALL_DIR%\%EXE_NAME%';" ^
  "$s.WorkingDirectory = '%INSTALL_DIR%';" ^
  "$s.IconLocation = '%INSTALL_DIR%\%EXE_NAME%,0';" ^
  "$s.Description = 'Setra CARDS - %HOTEL_NAME%';" ^
  "$s.Save()"

echo [4/5] Configurando arranque automatico...
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP_SHORTCUT%');" ^
  "$s.TargetPath = '%INSTALL_DIR%\%EXE_NAME%';" ^
  "$s.WorkingDirectory = '%INSTALL_DIR%';" ^
  "$s.IconLocation = '%INSTALL_DIR%\%EXE_NAME%,0';" ^
  "$s.Save()"

echo [5/5] Registrando instalacion...
> "%INSTALL_DIR%\.installed" echo hotel=%HOTEL_SLUG%
>> "%INSTALL_DIR%\.installed" echo fecha=%DATE% %TIME%

echo.
echo ============================================
echo   INSTALACION COMPLETADA
echo   Hotel: %HOTEL_NAME%
echo ============================================
echo.
echo   * Acceso directo en el Escritorio
echo   * Arranca automaticamente al prender el PC
echo   * Datos en: %LOCALAPPDATA%\SETRA\SetraCARDS\
echo   * Admin inicial:  Admin / PIN 1234  (cambiar al primer login)
echo.
pause
