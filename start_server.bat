@echo off
chcp 65001 >nul
title AI Video Server - ARABIAN AI SCHOOL
cd /d "%~dp0"

echo ============================================
echo   تشغيل / إعادة تشغيل السيرفر
echo   http://127.0.0.1:8000
echo ============================================
echo.
echo ملاحظة: اترك هذه النافذة مفتوحة طوال استخدام البرنامج.
echo.

echo [1/3] التحقق من المنفذ 8000...
set KILLED=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo       ايقاف السيرفر القديم ^(PID %%a^)...
  taskkill /PID %%a /F >nul 2>&1
  set KILLED=1
)
if "%KILLED%"=="0" echo       لا يوجد سيرفر قديم على المنفذ 8000.
timeout /t 2 /nobreak >nul

echo.
echo [2/3] البحث عن Python...
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py -3.12"
if not defined PY_CMD where python >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD (
  echo.
  echo [X] لم يتم العثور على Python.
  echo     ثبّت Python 3.12 من python.org ثم حاول مرة أخرى.
  echo.
  pause
  exit /b 1
)
echo       يستخدم: %PY_CMD%

echo.
echo [3/3] تشغيل السيرفر...
echo       افتح المتصفح: http://127.0.0.1:8000
echo ============================================
echo.

%PY_CMD% server_enhanced.py
set EXIT_CODE=%ERRORLEVEL%

echo.
echo ============================================
if %EXIT_CODE% NEQ 0 (
  echo [X] توقف السيرفر بخطأ ^(كود %EXIT_CODE%^).
  echo     اقرأ الرسالة أعلاه ثم اضغط أي مفتاح.
) else (
  echo [!] السيرفر توقف.
  echo     لإعادة التشغيل: شغّل start_server.bat مرة أخرى.
)
echo ============================================
pause
