@echo off
chcp 65001 >nul
title SyncNb

echo.
echo  ███████╗██╗   ██╗███╗   ██╗ ██████╗███╗   ██╗██████╗
echo  ██╔════╝╚██╗ ██╔╝████╗  ██║██╔════╝████╗  ██║██╔══██╗
echo  ███████╗ ╚████╔╝ ██╔██╗ ██║██║     ██╔██╗ ██║██████╔╝
echo  ╚════██║  ╚██╔╝  ██║╚██╗██║██║     ██║╚██╗██║██╔══██╗
echo  ███████║   ██║   ██║ ╚████║╚██████╗██║ ╚████║██████╔╝
echo  ╚══════╝   ╚═╝   ╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═══╝╚═════╝
echo.
echo  Garmin -^> Strava Sync for MyNB
echo  ─────────────────────────────────────────────────────
echo.

REM ── 1. Python 확인 ────────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo        https://www.python.org/downloads/ 에서 Python 3.11 이상을 설치하세요.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python %PY_VER% 감지

REM ── 2. .env 파일 확인 ─────────────────────────────────────────────────────────
if not exist .env (
    echo.
    echo [경고] .env 파일이 없습니다.
    echo        .env.example 을 복사해 .env 를 만들고 Strava 설정을 입력하세요.
    echo.
    echo        copy .env.example .env
    echo        notepad .env
    echo.
    set /p CREATE_ENV="지금 .env.example 을 복사해서 열까요? [Y/N]: "
    if /i "!CREATE_ENV!"=="Y" (
        copy .env.example .env
        notepad .env
    )
)

REM ── 3. 가상환경 생성 (없는 경우) ─────────────────────────────────────────────
if not exist .venv (
    echo.
    echo [설정] 가상환경 생성 중...
    python -m venv .venv
    if errorlevel 1 (
        echo [오류] 가상환경 생성 실패. Python 설치를 확인하세요.
        pause
        exit /b 1
    )
    echo [OK] 가상환경 생성 완료
)

REM ── 4. 의존성 설치 ─────────────────────────────────────────────────────────────
echo.
echo [설정] 패키지 설치 확인 중...
.venv\Scripts\python.exe -c "import flask, garminconnect, flask_limiter" >nul 2>&1
if errorlevel 1 (
    echo [설정] 패키지 설치 중 (최초 1회, 잠시 기다려주세요)...
    .venv\Scripts\pip.exe install -r requirements.txt
    if errorlevel 1 (
        echo [오류] 패키지 설치 실패. 인터넷 연결을 확인하세요.
        pause
        exit /b 1
    )
    echo [OK] 패키지 설치 완료
) else (
    echo [OK] 패키지 이미 설치됨
)

REM ── 5. 서버 시작 ───────────────────────────────────────────────────────────────
echo.
echo [시작] SyncNb 서버 시작 중...
echo        브라우저에서 http://localhost:5000 을 열어주세요.
echo        종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo.
echo  ─────────────────────────────────────────────────────
echo.

.venv\Scripts\python.exe app.py

echo.
echo [종료] 서버가 중지되었습니다.
pause
