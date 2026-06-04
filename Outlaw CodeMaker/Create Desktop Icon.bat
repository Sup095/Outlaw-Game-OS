@echo off
REM Creates an "Outlaw CodeMaker" icon on your Desktop that launches the app
REM (no console window) and keeps the self-evolve feature fully intact.
setlocal
set "ROOT=%~dp0"

if not exist "%ROOT%assets\outlaw.ico" (
    if exist "%ROOT%.venv\Scripts\python.exe" (
        "%ROOT%.venv\Scripts\python.exe" "%ROOT%tools\make_icon.py"
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws=New-Object -ComObject WScript.Shell;" ^
  "$lnk=[IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'Outlaw CodeMaker.lnk');" ^
  "$s=$ws.CreateShortcut($lnk);" ^
  "$s.TargetPath=[IO.Path]::Combine('%ROOT%','.venv\Scripts\pythonw.exe');" ^
  "$s.Arguments='boot_restore.py';" ^
  "$s.WorkingDirectory='%ROOT%';" ^
  "$s.IconLocation=[IO.Path]::Combine('%ROOT%','assets\outlaw.ico');" ^
  "$s.Description='Outlaw CodeMaker - autonomous Godot AI';" ^
  "$s.Save();" ^
  "Write-Host 'Created:' $lnk"

echo.
echo Done. Look for "Outlaw CodeMaker" on your Desktop.
pause
