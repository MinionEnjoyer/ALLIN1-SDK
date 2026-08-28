[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][string]$BuildId,
    [switch]$Unsigned
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path (Join-Path $PSScriptRoot '..\runtime\VehicleWorkbenchAxles')).Path
$cmakeText = Get-Content -LiteralPath (Join-Path $source 'CMakeLists.txt') -Raw
if ($cmakeText -notmatch 'project\(VehicleWorkbenchAxles\s+VERSION\s+([0-9]+\.[0-9]+\.[0-9]+)') {
    throw 'Could not derive the native axle runtime version from CMakeLists.txt.'
}
$runtimeVersion = $Matches[1]
$output = [IO.Path]::GetFullPath($OutputDirectory)
$build = Join-Path $output 'build'
$stage = Join-Path $output 'stage'
Remove-Item -LiteralPath $build,$stage -Recurse -Force -ErrorAction SilentlyContinue

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'vswhere.exe was not found.' }
$install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $install -or -not (Test-Path -LiteralPath $install)) {
    throw 'A supported Visual Studio C++ x64 toolchain was not found.'
}
if ($install -notmatch '[\\/](16|17|18|2019|2022|2026)[\\/]') {
    throw "Could not identify the Visual Studio product version from $install."
}
$generator = switch ($Matches[1]) {
    { $_ -in '16','2019' } { 'Visual Studio 16 2019'; break }
    { $_ -in '17','2022' } { 'Visual Studio 17 2022'; break }
    { $_ -in '18','2026' } { 'Visual Studio 18 2026'; break }
}

cmake -S $source -B $build -G $generator -A x64 `
    -DVWA_BUILD_STORY_HOSTS=ON -DVWA_BUILD_TESTS=ON
if ($LASTEXITCODE -ne 0) { throw 'Native ASI configure failed.' }
cmake --build $build --config Release --parallel
if ($LASTEXITCODE -ne 0) { throw 'Native ASI build failed.' }
ctest --test-dir $build -C Release --output-on-failure
if ($LASTEXITCODE -ne 0) { throw 'Native ASI CTest suite failed.' }

$devShell = Join-Path $install 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll'
Import-Module $devShell
Enter-VsDevShell -VsInstallPath $install -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'

$expectedExports = @(
    'VehicleWorkbenchAxles_GetDescriptor',
    'VehicleWorkbenchAxles_HasValidatedProfile',
    'VehicleWorkbenchAxles_HasScriptHookHost'
)
$forbiddenImports = @('VCRUNTIME140.dll','VCRUNTIME140_1.dll','MSVCP140.dll','ucrtbase.dll')
$editions = @('Legacy','Enhanced')
$editionHashes = @{}
foreach ($edition in $editions) {
    $asi = Join-Path $build "story-$edition\Release\VehicleWorkbenchAxles.asi"
    if (-not (Test-Path -LiteralPath $asi)) {
        # Ninja and single-config generators place the file one level higher.
        $asi = Join-Path $build "story-$edition\VehicleWorkbenchAxles.asi"
    }
    if (-not (Test-Path -LiteralPath $asi)) { throw "$edition ASI was not produced." }

    $headers = (& dumpbin /headers $asi 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0 -or $headers -notmatch 'machine \(x64\)') {
        throw "$edition ASI is not a valid x64 PE image."
    }
    $exports = (& dumpbin /exports $asi 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "$edition ASI export inspection failed." }
    foreach ($name in $expectedExports) {
        if ($exports -notmatch [regex]::Escape($name)) { throw "$edition ASI is missing export $name." }
    }
    $imports = (& dumpbin /imports $asi 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "$edition ASI import inspection failed." }
    foreach ($name in $forbiddenImports) {
        if ($imports -match [regex]::Escape($name)) { throw "$edition ASI imports dynamic CRT $name." }
    }
    $descriptorTarget = "story-$($edition.ToLowerInvariant())"
    $imageText = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($asi))
    if (-not $imageText.Contains($descriptorTarget)) {
        throw "$edition ASI does not contain its expected descriptor target $descriptorTarget."
    }

    $editionStage = Join-Path $stage $edition
    New-Item -ItemType Directory -Force $editionStage | Out-Null
    Copy-Item -LiteralPath $asi -Destination (Join-Path $editionStage 'VehicleWorkbenchAxles.asi')
    $runtimeStage = Join-Path $editionStage 'VehicleWorkbenchAxles'
    $profileStage = Join-Path $runtimeStage 'profiles'
    $schemaStage = Join-Path $runtimeStage 'schemas'
    New-Item -ItemType Directory -Force $profileStage,$schemaStage | Out-Null
    Copy-Item -LiteralPath (Join-Path $source 'examples\runtime.json') `
        -Destination (Join-Path $runtimeStage 'runtime.json')
    Copy-Item -LiteralPath (Join-Path $source 'profiles\compatibility.json') `
        -Destination (Join-Path $profileStage 'compatibility.json')
    Copy-Item -LiteralPath (Join-Path $source 'profiles\runtime-package.json') `
        -Destination (Join-Path $profileStage 'runtime-package.json')
    Copy-Item -LiteralPath (Join-Path $source 'schemas\axle-config.schema.json') `
        -Destination (Join-Path $schemaStage 'axle-config.schema.json')
    Copy-Item -LiteralPath (Join-Path $source 'schemas\story-runtime-profile.schema.json') `
        -Destination (Join-Path $schemaStage 'story-runtime-profile.schema.json')
    Copy-Item -LiteralPath (Join-Path $source 'schemas\story-runtime-receipt.schema.json') `
        -Destination (Join-Path $schemaStage 'story-runtime-receipt.schema.json')
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $asi).Hash.ToLowerInvariant()
    $editionHashes[$edition] = $hash
    $receipt = [ordered]@{
        schema_version = 1
        artifact = 'VehicleWorkbenchAxles.asi'
        edition = $edition.ToLowerInvariant()
        descriptor_target = $descriptorTarget
        architecture = 'x64'
        toolchain = 'msvc'
        runtime_library = 'static'
        build_id = $BuildId
        runtime_version = $runtimeVersion
        sha256 = $hash
        ctest_passed = $true
        pe_validated = $true
        exports_validated = $expectedExports
        dynamic_crt_imports_rejected = $true
        game_acceptance = 'not-tested'
        supported = $false
        unsigned = [bool]$Unsigned
        notice = 'Build validation is not an in-game acceptance receipt and does not mark this edition supported.'
    }
    $receipt | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $editionStage 'build-validation-receipt.json') -Encoding utf8
    $metadata = [ordered]@{
        schema_version = 1
        runtime_name = 'VehicleWorkbenchAxles'
        runtime_version = $runtimeVersion
        target = $descriptorTarget
        build_id = $BuildId
        binary_sha256 = $hash
        game_acceptance = 'not-tested'
        supported = $false
        configuration_directory = 'VehicleWorkbenchAxles/configs'
        log_file = 'VehicleWorkbenchAxles/logs/VehicleWorkbenchAxles.log'
    }
    $metadata | ConvertTo-Json -Depth 4 | Set-Content `
        (Join-Path $runtimeStage 'runtime-metadata.json') -Encoding utf8
}

if ($editionHashes['Legacy'] -eq $editionHashes['Enhanced']) {
    throw 'Legacy and Enhanced ASIs are byte-identical; edition specialization failed.'
}

Write-Output $stage
