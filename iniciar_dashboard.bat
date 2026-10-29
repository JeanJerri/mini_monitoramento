@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   MINI MONITORAMENTO DE REDE - INICIALIZACAO
echo ============================================================
echo.

:: Desativa o prompt de e-mail e telemetria do Streamlit
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"

:: Se o ambiente virtual ja existe, usa direto
if exist ".venv\Scripts\python.exe" (
    goto :RUN_VENV
)

:: Localiza o executavel do Python
set "PYTHON_EXE="

where py >nul 2>&1
if not errorlevel 1 (
    py -3.13 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py -3.13"
    ) else (
        set "PYTHON_EXE=py"
    )
)

if "!PYTHON_EXE!"=="" (
    where python >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=python"
    )
)

if "!PYTHON_EXE!"=="" (
    if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
        set "PYTHON_EXE="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
    )
)

if "!PYTHON_EXE!"=="" (
    echo [ERRO] O Python 3.13 nao foi encontrado no sistema.
    echo Por favor, instale o Python 3.13 para Windows:
    echo https://www.python.org/downloads/windows/
    echo.
    pause
    exit /b 1
)

echo [*] Utilizando interpretador: !PYTHON_EXE!
echo [*] Criando ambiente virtual do projeto (.venv)...
!PYTHON_EXE! -m venv .venv
if errorlevel 1 (
    echo [ERRO] Nao foi possivel criar o ambiente virtual.
    pause
    exit /b 1
)

:RUN_VENV
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERRO] Nao foi possivel ativar o ambiente virtual.
    pause
    exit /b 1
)

echo.
echo [*] Verificando dependencias em requirements.txt...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [AVISO] Falha ao verificar/instalar dependencias. Verifique sua conexao.
)

:: Garante que as credenciais do Streamlit estejam registradas para evitar o prompt interativo de boas-vindas
python -c "from streamlit.runtime import credentials; c = credentials.Credentials.get_current(); c.activation = credentials._Activation('', True); c.save()" >nul 2>&1


echo.
echo ============================================================
echo  Iniciando o Dashboard (http://localhost:8501)...
echo  O navegador sera aberto automaticamente.
echo  Para encerrar o servidor, pressione Ctrl+C nesta janela.
echo ============================================================
echo.

:: Abre o navegador no dashboard
start "" "http://localhost:8501"

:: Inicia o servidor Streamlit
python -m streamlit run app.py --server.port 8501 --server.address localhost --browser.gatherUsageStats false

pause
