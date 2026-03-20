@echo off
setlocal
:: 09_backup.bat
:: Creates a mysqldump backup of biblio_db for the local MariaDB/MySQL pipeline.
:: Defaults match etl/config.py unless you edit them here first.
:: Run: etl\09_backup.bat
:: Restore later with:
::   mysql -u root -P 3307 biblio_db < data\backups\biblio_db_backup_YYYY-MM-DD_HH-MM-SS.sql

set "DB_HOST=localhost"
set "DB_PORT=3307"
set "DB_USER=root"
set "DB_PASS="
set "DB_NAME=biblio_db"
set "BACKUP_DIR=%~dp0..\data\backups"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set "TIMESTAMP=%%I"
set "OUT_FILE=%BACKUP_DIR%\%DB_NAME%_backup_%TIMESTAMP%.sql"

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo Creating backup of %DB_NAME% on %DB_HOST%:%DB_PORT%...
if defined DB_PASS (
    mysqldump --host=%DB_HOST% --port=%DB_PORT% -u%DB_USER% --password=%DB_PASS% --routines --triggers --single-transaction --default-character-set=utf8mb4 --result-file="%OUT_FILE%" %DB_NAME%
) else (
    mysqldump --host=%DB_HOST% --port=%DB_PORT% -u%DB_USER% --routines --triggers --single-transaction --default-character-set=utf8mb4 --result-file="%OUT_FILE%" %DB_NAME%
)

if errorlevel 1 goto :dump_failed
if not exist "%OUT_FILE%" goto :missing_output
for %%F in ("%OUT_FILE%") do set "OUT_SIZE=%%~zF"
if "%OUT_SIZE%"=="0" goto :empty_output

echo Backup saved to: %OUT_FILE%
echo Restore with:
echo   mysql -u %DB_USER% -P %DB_PORT% %DB_NAME% ^< "%OUT_FILE%"
goto :done

:dump_failed
if exist "%OUT_FILE%" del "%OUT_FILE%"
echo ERROR: mysqldump failed. Check your credentials and that mysqldump is in PATH.
goto :done

:missing_output
echo ERROR: mysqldump reported success but no dump file was created.
goto :done

:empty_output
del "%OUT_FILE%"
echo ERROR: dump file was empty and has been removed.

:done
endlocal
pause
