@echo off
rem Admin Bar setup - double-click this file. Installs for this Windows user only, needs no administrator
rem rights and downloads nothing: the Python it uses is in the runtime folder next to this file.
setlocal EnableExtensions DisableDelayedExpansion
title Admin Bar - setup
set "SELF=%~f0"
set "HERE=%~dp0."
pushd "%HERE%" 2>nul || goto :badfolder
echo.
echo   ADMIN BAR - setup
echo   =================
echo.

rem ---- Windows: 64-bit Windows 10 or 11 --------------------------------------------------------------
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 goto :old_windows
set "BUILD="
for /f "tokens=3" %%b in ('reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CurrentBuildNumber /reg:64 2^>nul ^| find "CurrentBuildNumber"') do set "BUILD=%%b"
if defined ADMINBAR_SANDBOX if defined ADMINBAR_SANDBOX_BUILD set "BUILD=%ADMINBAR_SANDBOX_BUILD%"
if not defined BUILD goto :windows_ok
if %BUILD% LSS 10240 goto :old_windows
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" if %BUILD% LSS 22000 goto :old_windows
:windows_ok

rem ---- the bundled Python: runtime\ (unpacked zip) or .build\runtime\ (the source folder) -------------
set "PY="
if exist "%HERE%\runtime\python.exe" set "PY=%HERE%\runtime\python.exe"
if not defined PY if exist "%HERE%\.build\runtime\python.exe" set "PY=%HERE%\.build\runtime\python.exe"
if not defined PY if not exist "%HERE%\app\" if not exist "%HERE%\runtime\" goto :find_zip
if not defined PY goto :broken
for %%P in ("%PY%") do set "PYDIR=%%~dpP"
set "SETUP=%HERE%\app\adminbar\install.py"
set "SHORT="
rem Windows' old 260-character path limit: Python can't start from a folder this deep, so setup runs from a
rem short copy of the runtime (robocopy copies long paths) that is deleted again afterwards
if not "%HERE:~170,1%"=="" call :short_copy
if not exist "%PYDIR%pythonw.exe" goto :broken
if not exist "%PYDIR%python313.dll" goto :broken
if not exist "%PYDIR%python313.zip" goto :broken
if not exist "%SETUP%" goto :broken
if not defined SHORT if not exist "%HERE%\app\Admin Bar.pyw" goto :broken

"%PY%" -I -B -X utf8 "%SETUP%" install --src "%HERE%"
set "RC=%ERRORLEVEL%"
if defined SHORT rmdir /s /q "%SHORT%" >nul 2>nul
if "%RC%"=="0" goto :done
if "%RC%"=="3" goto :hold
echo.
echo   Setup did not finish. The reason is shown above - nothing else was changed.
goto :hold

:done
echo.
echo   You can close this window (it closes by itself in 30 seconds).
timeout /t 30 >nul 2>nul
popd
exit /b 0

:short_copy
set "SHORT=%LOCALAPPDATA%\AdminBar-setup-runtime"
echo   (the folder path is very long: setup runs from a short copy of its files)
rmdir /s /q "%SHORT%" >nul 2>nul
robocopy "%PYDIR%." "%SHORT%\runtime" /E /R:2 /W:1 /NJH /NJS /NFL /NDL /NP >nul 2>nul
if errorlevel 8 exit /b
robocopy "%HERE%\app\adminbar" "%SHORT%" install.py /R:2 /W:1 /NJH /NJS /NFL /NDL /NP >nul 2>nul
if errorlevel 8 exit /b
set "PY=%SHORT%\runtime\python.exe"
set "PYDIR=%SHORT%\runtime\"
set "SETUP=%SHORT%\install.py"
exit /b

rem ---- only Install.cmd is here: it was opened straight from inside the zip ----------------------------
:find_zip
echo   Install.cmd was opened from inside the zip, without the rest of Admin Bar.
echo   Looking for AdminBar.zip to unpack it ...
set "ZIPNAME="
set "ZIP="
set "T="
for %%A in ("%HERE%\..") do set "T=%%~nxA"
call :zip_from_temp
if defined ZIPNAME goto :search
for %%A in ("%HERE%") do set "T=%%~nxA"
call :zip_from_temp
:search
set "DL="
if defined ADMINBAR_SANDBOX goto :search_dirs
for /f "tokens=2,*" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders" /v "{374DE290-123F-4565-9164-39C4925E467B}" 2^>nul ^| find "REG_"') do set "DL=%%b"
if defined DL call set "DL=%DL%"
:search_dirs
for %%D in ("%USERPROFILE%\Downloads" "%DL%" "%USERPROFILE%\Desktop" "%OneDrive%\Desktop" "%OneDrive%\Downloads" "%USERPROFILE%\Documents" "%OneDrive%\Documents") do if not defined ZIP (set "D=%%~D"& call :look)
if not defined ZIP goto :manual
for %%Z in ("%ZIP%") do echo   Found %%~Z
for %%Z in ("%ZIP%") do set "OUT=%%~dpZAdminBar"
call :unpack
if not defined UNPACKED set "OUT=%LOCALAPPDATA%\AdminBar setup"
if not defined UNPACKED call :unpack
if not defined UNPACKED goto :manual
for %%O in ("%OUT%") do echo   Unpacked to %%~O
echo.
popd
rem hand over to the unpacked copy (no "call": the path is used as it is)
"%OUT%\Install.cmd"

:zip_from_temp
rem Windows unpacks a file opened from a zip to %TEMP%\Temp1_<zip name>\...: that name says which zip it was
set "N="
if not defined T exit /b
if /i not "%T:~0,4%"=="Temp" exit /b
set "N=%T:*_=%"
if /i not "%N:~-4%"==".zip" exit /b
set "ZIPNAME=%N%"
exit /b

:look
rem one folder: the zip named by the temp folder, else every AdminBar*.zip, newest first; it must hold this Install.cmd
if not defined D exit /b
if not exist "%D%\" exit /b
if defined ZIPNAME if exist "%D%\%ZIPNAME%" set "C=%D%\%ZIPNAME%"& call :same
if defined ZIP exit /b
pushd "%D%" 2>nul || exit /b
for /f "delims=" %%F in ('dir /b /a-d /o-d "AdminBar*.zip" 2^>nul') do if not defined ZIP (set "C=%D%\%%F"& call :same)
popd
exit /b

:same
call :tar
if not defined TAR set "ZIP=%C%"& exit /b
set "CHECK=%TEMP%\AdminBar-check-%RANDOM%.cmd"
"%TAR%" -xOf "%C%" "AdminBar/Install.cmd" > "%CHECK%" 2>nul
fc /b "%CHECK%" "%SELF%" >nul 2>nul && set "ZIP=%C%"
del /q "%CHECK%" >nul 2>nul
exit /b

:unpack
set "UNPACKED="
call :tar
if not defined TAR exit /b
if not exist "%OUT%\" mkdir "%OUT%" >nul 2>nul
if not exist "%OUT%\" exit /b
"%TAR%" -xf "%ZIP%" -C "%OUT%" --strip-components 1 >nul 2>nul
if errorlevel 1 exit /b
if exist "%OUT%\Install.cmd" if exist "%OUT%\runtime\python.exe" set "UNPACKED=1"
exit /b

:tar
rem Windows' own tar.exe (Windows 10 1803 and newer) unpacks zips; Sysnative = the 64-bit one from a 32-bit cmd
set "TAR="
if exist "%SystemRoot%\Sysnative\tar.exe" set "TAR=%SystemRoot%\Sysnative\tar.exe"& exit /b
if exist "%SystemRoot%\System32\tar.exe" set "TAR=%SystemRoot%\System32\tar.exe"
exit /b

rem ---- messages --------------------------------------------------------------------------------------
:manual
echo.
echo   Admin Bar has to be unpacked first:
echo     1. Right-click AdminBar.zip and choose "Extract All...", then "Extract".
echo     2. In the folder that opens, double-click Install.cmd.
goto :hold

:broken
echo   Some Admin Bar files are missing in this folder:
for %%H in ("%HERE%") do echo     %%~fH
echo   The zip was not fully unpacked, or a file was removed.
echo     1. Delete this folder.
echo     2. Right-click AdminBar.zip, choose "Extract All...", then "Extract".
echo     3. In the new folder, double-click Install.cmd.
goto :hold

:old_windows
echo   Admin Bar needs 64-bit Windows 10 or Windows 11.
goto :hold

:badfolder
echo.
echo   This folder can't be opened. Unpack AdminBar.zip to your Desktop or Downloads and try again.
goto :hold

:hold
echo.
echo   Press any key to close this window.
pause >nul
popd 2>nul
exit /b 1
