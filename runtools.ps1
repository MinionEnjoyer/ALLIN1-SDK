param([switch]$Clean)

$ErrorActionPreference = "Stop"
$sdkRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $sdkRoot "tools\RpfPatcher\RpfPatcher.csproj"
$destination = Join-Path $sdkRoot "tools\RpfPatcher"

if ($Clean) {
    dotnet clean $project -c Release --nologo
}

dotnet publish $project -c Release --nologo --self-contained true `
    -r win-x64 -o $destination
Write-Host "RpfPatcher published to $destination"
