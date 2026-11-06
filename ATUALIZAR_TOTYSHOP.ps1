param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [string]$SourcePath = ""
)

$ErrorActionPreference = 'Stop'
$TargetPath = [System.IO.Path]::GetFullPath($TargetPath)
if (-not (Test-Path (Join-Path $TargetPath 'iniciar_totyshop.bat'))) {
    throw "A pasta indicada não contém iniciar_totyshop.bat: $TargetPath"
}

$workDir = Join-Path $env:TEMP ("totyshop-core-update-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir -Force | Out-Null

try {
    if ($SourcePath) {
        $sourceRoot = (Resolve-Path $SourcePath).Path
    } else {
        $archive = Join-Path $workDir 'project.zip'
        $extractDir = Join-Path $workDir 'source'
        New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
        $downloadUrl = 'https://codeload.github.com/totyshopvendas-cmd/bling-product-robot/zip/refs/heads/arena/d07074e0-bling-product-robot'
        Write-Host 'Baixando a versão focada do projeto...'
        Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -UseBasicParsing
        Expand-Archive -Path $archive -DestinationPath $extractDir -Force
        $candidate = Get-ChildItem -Path $extractDir -Directory | Where-Object {
            Test-Path (Join-Path $_.FullName 'iniciar_totyshop.bat')
        } | Select-Object -First 1
        if (-not $candidate) { throw 'Não encontrei a pasta do projeto no ZIP baixado.' }
        $sourceRoot = $candidate.FullName
    }

    $protectedDirs = @('.git', '.venv', 'venv', 'node_modules', 'build', 'data', '.secrets', 'exports', 'memory', '__pycache__')
    $files = Get-ChildItem -Path $sourceRoot -Recurse -File -Force
    $copied = 0
    foreach ($file in $files) {
        if ($file.Name -eq '.env') { continue }
        $relative = $file.FullName.Substring($sourceRoot.Length).TrimStart([char[]]@('\', '/'))
        $parts = $relative -split '\\|/'
        if (@($parts | Where-Object { $protectedDirs -contains $_ }).Count -gt 0) { continue }
        $destination = Join-Path $TargetPath $relative
        $parent = Split-Path -Parent $destination
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        $copied++
    }

    Write-Host "Atualização aplicada ($copied arquivos). Dados, credenciais, ambientes locais e arquivos .env foram preservados." -ForegroundColor Green
    Write-Host 'Agora dê dois cliques em iniciar_totyshop.bat. O painel reconstruirá a interface e abrirá no navegador.' -ForegroundColor Cyan
}
finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
}
