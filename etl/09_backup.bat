@echo off
:: 09_backup.bat
:: Creates a mysqldump backup of biblio_db.
:: Edit DB_USER and DB_PASS before running.
:: Run: 09_backup.bat

SET DB_USER=root
SET DB_PASS=root
SET DB_NAME=biblio_db
SET BACKUP_DIR=%~dp0..\data\backups
SET TIMESTAMP=%DATE:~-4%-%DATE:~3,2%-%DATE:~0,2%_%TIME:~0,2%-%TIME:~3,2%

:: Create backup folder if missing
IF NOT EXIST "%BACKUP_DIR%" MKDIR "%BACKUP_DIR%"

SET OUT_FILE=%BACKUP_DIR%\%DB_NAME%_backup_%TIMESTAMP%.sql

echo Creating backup of %DB_NAME%...
mysqldump -u%DB_USER% -P 3307 --routines --triggers --single-transaction %DB_NAME% > "%OUT_FILE%"

IF %ERRORLEVEL% EQU 0 (
    echo Backup saved to: %OUT_FILE%
) ELSE (
    echo ERROR: mysqldump failed. Check your credentials and that mysqldump is in PATH.
)
pause
