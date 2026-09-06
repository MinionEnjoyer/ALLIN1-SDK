param([switch]$Clean)

$ErrorActionPreference = "Stop"
$sdkRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $sdkRoot "tools\RpfPatcher\RpfPatcher.csproj"
$destination = Join-Path $sdkRoot "tools\RpfPatcher"

if ($Clean) {
    dotnet clean $project -c Release --nologo
    if ($LASTEXITCODE -ne 0) { throw "RpfPatcher clean failed (exit $LASTEXITCODE)" }
}

dotnet publish $project -c Release --nologo --self-contained true `
    -r win-x64 -o $destination
if ($LASTEXITCODE -ne 0) { throw "RpfPatcher publish failed (exit $LASTEXITCODE)" }
Write-Host "RpfPatcher published to $destination"
