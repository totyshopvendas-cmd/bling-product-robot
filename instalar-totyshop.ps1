$ErrorActionPreference = 'Stop'
$InstallDir = Join-Path $env:USERPROFILE 'TotyShop\bling-product-robot'
$ZipFile = Join-Path $env:TEMP 'bling-product-robot-main.zip'
$RepoZip = 'https://github.com/totyshopvendas-cmd/bling-product-robot/archive/refs/heads/main.zip'

function Info($text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nERRO: $text" -ForegroundColor Red; Read-Host 'Pressione Enter para fechar'; exit 1 }

try {
    Info 'Instalador TotyShop - versão independente do Emergent'
    Write-Host "Pasta de instalação: $InstallDir"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host '`nO Docker Desktop não foi encontrado neste computador.' -ForegroundColor Yellow
        $answer = Read-Host 'Deseja abrir a página oficial para instalar o Docker Desktop? (S/N)'
        if ($answer -match '^[Ss]') {
            Start-Process 'https://www.docker.com/products/docker-desktop/'
            Write-Host 'Instale o Docker Desktop, reinicie o Windows se ele solicitar e execute este instalador novamente.' -ForegroundColor Yellow
        }
        Read-Host 'Pressione Enter para fechar'
        exit 0
    }

    Info 'Verificando se o Docker Desktop está funcionando...'
    $dockerReady = $false
    try {
        docker info *> $null
        $dockerReady = ($LASTEXITCODE -eq 0)
    } catch {
        $dockerReady = $false
    }
    if (-not $dockerReady) {
        $dockerPaths = @(
            (Join-Path ${env:ProgramFiles} 'Docker\Docker\Docker Desktop.exe'),
            (Join-Path ${env:LOCALAPPDATA} 'Docker\Docker Desktop.exe')
        )
        $dockerExe = $dockerPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $dockerExe) {
            throw 'O comando Docker existe, mas o Docker Desktop não foi localizado. Abra o Docker Desktop manualmente e execute novamente.'
        }
        Write-Host 'O Docker Desktop está fechado. Abrindo agora...' -ForegroundColor Yellow
        Start-Process $dockerExe
        $deadline = (Get-Date).AddMinutes(3)
        do {
            Start-Sleep -Seconds 5
            docker info *> $null
            $dockerReady = ($LASTEXITCODE -eq 0)
            if ($dockerReady) { break }
            Write-Host 'Aguardando o motor do Docker iniciar...'
        } while ((Get-Date) -lt $deadline)
        if (-not $dockerReady) {
            throw 'O Docker Desktop abriu, mas o motor ainda não ficou pronto. Aguarde o ícone da baleia ficar estável e execute novamente.'
        }
    }

    if (-not (Test-Path (Split-Path $InstallDir))) {
        New-Item -ItemType Directory -Path (Split-Path $InstallDir) -Force | Out-Null
    }

    Info 'Baixando o projeto atualizado do GitHub...'
    Invoke-WebRequest -Uri $RepoZip -OutFile $ZipFile -UseBasicParsing

    if (Test-Path $InstallDir) {
        $backup = "$InstallDir-backup-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Rename-Item $InstallDir $backup
        Write-Host "Versão anterior preservada em: $backup"
    }

    Expand-Archive -Path $ZipFile -DestinationPath (Split-Path $InstallDir) -Force
    Rename-Item (Join-Path (Split-Path $InstallDir) 'bling-product-robot-main') 'bling-product-robot'
    Remove-Item $ZipFile -Force -ErrorAction SilentlyContinue

    Info 'Criando configuração local segura...'
    $envExample = Join-Path $InstallDir 'backend\.env.example'
    $envFile = Join-Path $InstallDir '.env'
    $secret = [guid]::NewGuid().ToString() + [guid]::NewGuid().ToString()
    $envText = Get-Content $envExample -Raw
    $envText = $envText -replace '(?m)^APP_BASE_URL=.*$', 'APP_BASE_URL=http://127.0.0.1:8000'
    $envText = $envText -replace '(?m)^APP_SECRET=.*$', "APP_SECRET=$secret"
    $envText = $envText -replace '(?m)^MONGO_URL=.*$', 'MONGO_URL=mongodb://mongo:27017'
    Set-Content -Path $envFile -Value $envText -Encoding UTF8

    Info 'Iniciando o painel e o banco de dados. A primeira instalação pode demorar alguns minutos...'
    Push-Location $InstallDir
    try {
        docker compose up -d --build
        if ($LASTEXITCODE -ne 0) { throw 'Docker Compose não conseguiu iniciar os serviços.' }
    } finally {
        Pop-Location
    }

    Start-Sleep -Seconds 5
    Start-Process 'http://127.0.0.1:8000'
    Write-Host '`nInstalação concluída. O painel foi aberto no navegador.' -ForegroundColor Green
    Write-Host 'Para usar novamente depois, abra: http://127.0.0.1:8000'
    Read-Host 'Pressione Enter para fechar'
}
catch {
    Fail $_.Exception.Message
}
