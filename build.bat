@echo off
echo ================================================
echo  교사용 계산기 - .exe 빌드
echo ================================================
cd /d "%~dp0"

pyinstaller --onefile ^
  --windowed ^
  --name "교사용계산기" ^
  --icon NONE ^
  --hidden-import keyboard ^
  --hidden-import pynput ^
  --hidden-import PIL ^
  --hidden-import PIL.Image ^
  --hidden-import PIL.ImageTk ^
  --add-data "논산여상 로고.png;." ^
  --add-data "갓쌤에듀 로고.png;." ^
  main.py

echo.
if exist dist\교사용계산기.exe (
    echo [완료] dist\교사용계산기.exe 생성 성공!
    echo  - 이 파일 하나만 복사해서 쓰면 됩니다
    echo  - NumLock 두 번 연속으로 계산기 열기
) else (
    echo [오류] 빌드 실패. 위 오류 메시지를 확인하세요.
)
pause
