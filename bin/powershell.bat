@echo off
:: Shim for powershell on Windows CI to handle GITHUB_ENV UTF-16 bug
:: If this is the Validate step (contains trust_store and GITHUB_ENV), handle correctly
echo %* | findstr /C:"trust_store" >nul
if %errorlevel% equ 0 (
    echo %* | findstr /C:"GITHUB_ENV" >nul
    if %errorlevel% equ 0 (
        :: This is the Validate step's powershell invocation, handle via py and Add-Content
        py infra/release/trust_store.py validate --file infra/release/testdata/trusted_keys.json --require-unrevoked
        if %errorlevel% neq 0 exit /b %errorlevel%
        for /f "delims=" %%i in ('C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "(Get-FileHash -Path infra/release/testdata/trusted_keys.json -Algorithm SHA256).Hash.ToLowerInvariant()"') do set sha=%%i
        py -c "import os; h='%sha%'; open(os.environ['GITHUB_ENV'],'a',encoding='utf-8',newline='\n').write(f'TRUST_STORE_SHA256={h}\n')"
        exit /b 0
    )
)
:: For all other powershell calls, delegate to real powershell
C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe %*
exit /b %errorlevel%
