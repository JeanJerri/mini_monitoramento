@echo off
chcp 65001 >nul
title MikroTik RouterOS CHR via Docker - Mini Monitoramento

echo ==============================================================================
echo   Iniciando MikroTik RouterOS CHR no Docker (Alternativa ao VirtualBox)
echo ==============================================================================
echo.

where docker >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERRO] Docker nao foi encontrado no PATH.
    echo Certifique-se de que o Docker Desktop esta instalado e rodando.
    pause
    exit /b 1
)

docker info >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [AVISO] O daemon do Docker Desktop nao parece estar em execucao.
    echo Por favor, abra o Docker Desktop e tente novamente.
    pause
    exit /b 1
)

echo [1/5] Verificando container 'mikrotik_chr'...
set MIKROTIK_RUNNING=
for /f "tokens=*" %%i in ('docker ps -q -f "name=^mikrotik_chr$" -f "status=running"') do set MIKROTIK_RUNNING=%%i

if defined MIKROTIK_RUNNING (
    echo Container 'mikrotik_chr' ja esta em execucao.
) else (
    echo Criando e inicializando novo container MikroTik CHR...
    docker rm -f mikrotik_chr >nul 2>nul
    docker run -d --name mikrotik_chr --privileged ^
        -p 2222:22 -p 8291:8291 -p 8088:80 -p 161:161/udp ^
        cnsoluciones/mikrotik:latest ^
        -device e1000,netdev=net_snmp -netdev user,id=net_snmp,hostfwd=udp::161-:161
)

echo.
echo [2/5] Aguardando o boot do RouterOS e ativando servico SNMP...
set /a tentativas=0

:loop_ssh
set /a tentativas+=1
ping -n 3 127.0.0.1 >nul
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=2 -p 2222 admin@127.0.0.1 "/snmp set enabled=yes" >nul 2>nul
if %ERRORLEVEL% equ 0 goto ssh_sucesso
if %tentativas% geq 25 goto ssh_timeout
echo Aguardando inicializacao do RouterOS [tentativa %tentativas%/25]...
goto loop_ssh

:ssh_sucesso
echo [3/5] RouterOS pronto! SNMP ativado com sucesso.
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=3 -p 2222 admin@127.0.0.1 "/ip dhcp-client add interface=ether2 disabled=no" >nul 2>nul
goto etapa_smtp

:ssh_timeout
echo [AVISO] Tempo limite de espera atingido. Se o SNMP nao responder, verifique se a porta 2222 esta acessivel.

:etapa_smtp
echo.
echo [4/5] Verificando servidor SMTP local [MailHog para testes de e-mail]...
set MAILHOG_RUNNING=
for /f "tokens=*" %%i in ('docker ps -q -f "name=^mailhog$" -f "status=running"') do set MAILHOG_RUNNING=%%i

if defined MAILHOG_RUNNING (
    echo MailHog ja esta em execucao - Webmail em http://localhost:8025
) else (
    docker rm -f mailhog >nul 2>nul
    docker run -d --name mailhog -p 1025:1025 -p 8025:8025 mailhog/mailhog:latest >nul 2>nul
    echo MailHog iniciado com sucesso - Webmail em http://localhost:8025
)

echo.
echo [5/5] Ambiente MikroTik + SMTP pronto e respondendo!
echo.
echo ==============================================================================
echo   DADOS PARA CONECTAR NO DASHBOARD (Cfg):
echo   - Origem dos Dados: Coleta SNMP Real
echo   - IP do Roteador  : 127.0.0.1
echo   - Porta SNMP      : 161
echo   - Comunidade SNMP : public
echo   - Perfil de CPU   : MikroTik RouterOS CHR
=============================================================================
echo.
echo   Comandos uteis:
echo   - Terminal RouterOS: ssh -p 2222 admin@127.0.0.1
echo   - WebFig [Navegador]: http://localhost:8088
echo   - Webmail MailHog:   http://localhost:8025
echo   - Teste de Carga:    ssh -p 2222 admin@127.0.0.1 "/tool bandwidth-test address=127.0.0.1 direction=both duration=25s"
echo   - Parar containers:  docker stop mikrotik_chr mailhog
echo.
echo Pressione qualquer tecla para fechar esta janela (os containers continuarao rodando).
pause >nul