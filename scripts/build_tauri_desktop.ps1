[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,
    [string]$PnpmExecutable = 'pnpm',
    [string]$SevenZipExecutable = '7z',
    [switch]$SidecarOnly,
    [switch]$SkipInstaller,
    [switch]$AllowWindowsSymlinkSkips
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$desktop = Join-Path $repo 'desktop'
$sidecarDir = Join-Path $desktop 'src-tauri\sidecar'
$python = (Resolve-Path -LiteralPath $PythonExecutable).Path
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python executable was not found: $PythonExecutable"
}
$waiverArguments = @()
if ($AllowWindowsSymlinkSkips) { $waiverArguments += '--allow-windows-symlink-skips' }
$candidateIdentity = & $python (Join-Path $repo 'scripts\desktop_candidate.py') prepare --pnpm $PnpmExecutable @waiverArguments
if ($LASTEXITCODE -ne 0) { throw 'Candidate source identity preparation failed.' }
$candidateIdentity = $candidateIdentity.Trim()
Write-Host "Candidate identity: $candidateIdentity"
$pythonGateCommand = ConvertTo-Json -Compress -InputObject @($python, '-m', 'pytest', '--cov=allin1_sdk', '--cov-report=term-missing')
& $python (Join-Path $repo 'scripts\desktop_candidate.py') gate --identity $candidateIdentity `
    --name python --cwd . --timeout 3600 --command-json $pythonGateCommand
if ($LASTEXITCODE -ne 0) { throw 'Python test and coverage gate failed.' }
New-Item -ItemType Directory -Path $sidecarDir -Force | Out-Null
# Publish the native helper with its own .NET runtime. End users need neither
# the Launcher nor Python/.NET installed to author packages with this SDK.
$rpfPublish = Join-Path $repo ('build\tauri-rpf-' + [guid]::NewGuid().ToString('N'))
& dotnet publish (Join-Path $repo 'tools\RpfPatcher\RpfPatcher.csproj') `
    -c Release -r win-x64 --self-contained true -o $rpfPublish
if ($LASTEXITCODE -ne 0) { throw 'Self-contained RpfPatcher publish failed.' }
$nativeRpfGateCommand = ConvertTo-Json -Compress -InputObject @('dotnet', 'run', '--project', (Join-Path $repo 'tools\RpfPatcher.Tests\RpfPatcher.Tests.csproj'), '-c', 'Release')
& $python (Join-Path $repo 'scripts\desktop_candidate.py') gate --identity $candidateIdentity `
    --name native-rpf --cwd . --timeout 1200 --command-json $nativeRpfGateCommand
if ($LASTEXITCODE -ne 0) { throw 'Exact native RPF identity regression checks failed.' }
Push-Location $repo
try {
    & $python -m scripts.stage_desktop_resources --rpf-dir $rpfPublish --build-identity $candidateIdentity
    if ($LASTEXITCODE -ne 0) { throw 'Standalone resource staging failed.' }
}
finally { Pop-Location }
$excludedModules = & $python (Join-Path $repo 'scripts\frozen_desktop.py') exclusions
if ($LASTEXITCODE -ne 0) { throw 'Tk-free packaging policy could not be loaded.' }
$excludeArguments = @()
foreach ($module in $excludedModules) { $excludeArguments += @('--exclude-module', $module) }
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --console `
    --onefile `
    --name ALLIN1-SDK-Desktop-Sidecar `
    --icon (Join-Path $repo 'assets\favicon.ico') `
    --version-file (Join-Path (Split-Path -Parent $candidateIdentity) 'sidecar-version.txt') `
    --paths (Join-Path $repo 'src') `
    --add-data "$candidateIdentity;allin1_sdk" `
    --add-data "$(Join-Path $desktop 'src-tauri\standalone-resources\resource-checksums.json');allin1_sdk" `
    --distpath $sidecarDir `
    --workpath (Join-Path $repo 'build\pyinstaller-tauri-sidecar') `
    --specpath (Join-Path $repo 'build') `
    @excludeArguments `
    (Join-Path $repo 'scripts\desktop_sidecar_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller desktop sidecar build failed.' }

$sidecar = Join-Path $sidecarDir 'ALLIN1-SDK-Desktop-Sidecar.exe'
& $python (Join-Path $repo 'scripts\frozen_desktop.py') inspect $sidecar
if ($LASTEXITCODE -ne 0) { throw 'Tk/legacy UI leaked into the frozen React SDK.' }
& $python (Join-Path $repo 'scripts\smoke_desktop_sidecar.py') $sidecar `
    --resource-home (Join-Path $desktop 'src-tauri\standalone-resources') --build-identity $candidateIdentity
if ($LASTEXITCODE -ne 0) { throw 'Packaged desktop sidecar smoke test failed.' }
& $python (Join-Path $repo 'scripts\smoke_ped_desktop.py') $sidecar `
    --resource-home (Join-Path $desktop 'src-tauri\standalone-resources')
if ($LASTEXITCODE -ne 0) { throw 'Packaged ped workbench smoke test failed.' }
$testedSidecarHash = (Get-FileHash -LiteralPath $sidecar -Algorithm SHA256).Hash
$resourceManifest = Join-Path $desktop 'src-tauri\standalone-resources\resource-checksums.json'
$testedResourceManifestHash = (Get-FileHash -LiteralPath $resourceManifest -Algorithm SHA256).Hash

if ($SidecarOnly) {
    Write-Host 'ALLIN1 packaged desktop sidecar validation completed.'
    Write-Host "Sidecar: $sidecar"
    return
}

if (-not (Get-Command rustc -ErrorAction SilentlyContinue)) {
    throw 'Rust is required. Install a supported rustup toolchain before building Tauri.'
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Cargo is required. Install a supported rustup toolchain before building Tauri.'
}

Push-Location $desktop
$previousBuildIdentity = $env:ALLIN1_BUILD_IDENTITY_FILE
$previousBuildPython = $env:ALLIN1_BUILD_PYTHON
$previousCaptureScript = $env:ALLIN1_BUILD_CAPTURE_SCRIPT
$previousTestPython = $env:ALLIN1_SDK_TEST_PYTHON
$previousNativeRpfTest = $env:ALLIN1_NATIVE_RPF_TEST
$previousNativeRuntimeTest = $env:ALLIN1_NATIVE_RUNTIME_TEST
$previousBlenderExecutable = $env:ALLIN1_BLENDER_EXECUTABLE
$candidateBlender = Join-Path $repo 'build\dependencies\blender-4.5.13-windows-x64\blender.exe'
if (-not (Test-Path -LiteralPath $candidateBlender -PathType Leaf)) {
    throw "Candidate qualification requires the pinned Blender executable: $candidateBlender"
}
$env:ALLIN1_BUILD_IDENTITY_FILE = $candidateIdentity
$env:ALLIN1_BUILD_PYTHON = $python
$env:ALLIN1_BUILD_CAPTURE_SCRIPT = Join-Path $repo 'scripts\desktop_candidate.py'
$env:ALLIN1_SDK_TEST_PYTHON = $python
$env:ALLIN1_NATIVE_RPF_TEST = '1'
$env:ALLIN1_NATIVE_RUNTIME_TEST = '1'
$env:ALLIN1_BLENDER_EXECUTABLE = $candidateBlender
try {
    & $PnpmExecutable install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'pnpm install failed.' }
    $reactGateCommand = ConvertTo-Json -Compress -InputObject @($PnpmExecutable, 'test')
    & $python (Join-Path $repo 'scripts\desktop_candidate.py') gate --identity $candidateIdentity `
        --name react --cwd desktop --timeout 1200 --command-json $reactGateCommand
    if ($LASTEXITCODE -ne 0) { throw 'React component tests failed.' }
    $rustGateCommand = ConvertTo-Json -Compress -InputObject @('cargo', 'test', '--manifest-path', (Join-Path $desktop 'src-tauri\Cargo.toml'))
    & $python (Join-Path $repo 'scripts\desktop_candidate.py') gate --identity $candidateIdentity `
        --name rust --cwd desktop --timeout 1200 --command-json $rustGateCommand
    if ($LASTEXITCODE -ne 0) { throw 'Rust broker checks failed.' }
    $frontendGateCommand = ConvertTo-Json -Compress -InputObject @($PnpmExecutable, 'build')
    & $python (Join-Path $repo 'scripts\desktop_candidate.py') gate --identity $candidateIdentity `
        --name frontend --cwd desktop --timeout 1200 --command-json $frontendGateCommand
    if ($LASTEXITCODE -ne 0) { throw 'React production build gate failed.' }
    if (-not $SkipInstaller) {
        & $python (Join-Path $repo 'scripts\desktop_candidate.py') check --identity $candidateIdentity
        if ($LASTEXITCODE -ne 0) { throw 'Candidate source changed before installer build.' }
        & $python -c "from pathlib import Path; from allin1_sdk.release_identity import verify_inventory; verify_inventory(Path('src-tauri/standalone-resources'))"
        if ($LASTEXITCODE -ne 0) { throw 'Resource inventory changed after smoke testing.' }
        if ((Get-FileHash -LiteralPath $sidecar -Algorithm SHA256).Hash -ne $testedSidecarHash -or (Get-FileHash -LiteralPath $resourceManifest -Algorithm SHA256).Hash -ne $testedResourceManifestHash) {
            throw 'Staged binary/resource identity changed after smoke testing.'
        }
        & $PnpmExecutable tauri build
        if ($LASTEXITCODE -ne 0) { throw 'Tauri NSIS build failed.' }
        if ((Get-FileHash -LiteralPath $sidecar -Algorithm SHA256).Hash -ne $testedSidecarHash -or (Get-FileHash -LiteralPath $resourceManifest -Algorithm SHA256).Hash -ne $testedResourceManifestHash) {
            throw 'Staged binary/resource identity changed during packaging; do not distribute this candidate.'
        }
        $sevenzipCommand = Get-Command $SevenZipExecutable -ErrorAction SilentlyContinue
        if ($sevenzipCommand) {
            $sevenzip = $sevenzipCommand.Source
        }
        else {
            $pinnedSevenZip = Join-Path $repo 'build\release-tools\7zip-26.03\unpacked\7z.exe'
            if (-not (Test-Path -LiteralPath $pinnedSevenZip -PathType Leaf)) {
                throw "7-Zip was not found as '$SevenZipExecutable' or at the pinned release-tool path."
            }
            $sevenzip = (Resolve-Path -LiteralPath $pinnedSevenZip).Path
        }
        & $python (Join-Path $repo 'scripts\desktop_candidate.py') seal --identity $candidateIdentity --sevenzip $sevenzip
        if ($LASTEXITCODE -ne 0) { throw 'Candidate byte-for-byte package qualification failed.' }
    }
}
finally {
    $env:ALLIN1_BUILD_IDENTITY_FILE = $previousBuildIdentity
    $env:ALLIN1_BUILD_PYTHON = $previousBuildPython
    $env:ALLIN1_BUILD_CAPTURE_SCRIPT = $previousCaptureScript
    $env:ALLIN1_SDK_TEST_PYTHON = $previousTestPython
    $env:ALLIN1_NATIVE_RPF_TEST = $previousNativeRpfTest
    $env:ALLIN1_NATIVE_RUNTIME_TEST = $previousNativeRuntimeTest
    $env:ALLIN1_BLENDER_EXECUTABLE = $previousBlenderExecutable
    Pop-Location
}

Write-Host 'ALLIN1 Tauri desktop validation completed.'
Write-Host "Sidecar: $sidecar"
if (-not $SkipInstaller) {
    Write-Host "Installer root: $(Join-Path $desktop 'src-tauri\target\release\bundle\nsis')"
}
