param(
    [string]$DatasetRoot = "D:\HD3D_Dataset",
    [string]$ResultRoot = "D:\HD3D_Result",
    [string]$Python = "C:\Users\22499\anaconda3\python.exe",
    [string]$Method = "ges-gsp",
    [string]$MethodFolder = "ges_gsp",
    [double]$ContentWeight = 1.5,
    [double]$MaxTargetMegapixels = 80.0,
    [double]$FallbackMaxMegapixels = 1500.0,
    [string]$CondaPrefix = "C:\Users\22499\anaconda3\envs\obj-gsp-cpp",
    [string]$VsDevCmd = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat",
    [string]$CMake = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
    [string[]]$Dataset,
    [int]$RunTimeoutSeconds = 600,
    [int]$FallbackRunTimeoutSeconds = 1800,
    [switch]$Smoke,
    [switch]$SkipExistingResults,
    [switch]$ForceRerun,
    [switch]$AutoMegapixelFallback = $true,
    [switch]$SkipBuild,
    [switch]$SkipPrepare,
    [switch]$SkipRun
)

$ErrorActionPreference = "Stop"

function Invoke-GesGspDataset {
    param(
        [string]$Exe,
        [string]$Name,
        [string]$DataRootFull,
        [string]$GraphsRoot,
        [string]$WorkRootFull,
        [string]$Method,
        [double]$ContentWeight,
        [double]$MegapixelLimit,
        [string]$Stdout,
        [string]$Stderr,
        [int]$TimeoutSeconds
    )

    $processArgs = @(
        "--data-root", $DataRootFull,
        "--graph-root", $GraphsRoot,
        "--output-root", $WorkRootFull,
        "--method", $Method,
        "--content-weight", $ContentWeight.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "--max-target-megapixels", $MegapixelLimit.ToString([System.Globalization.CultureInfo]::InvariantCulture),
        "--dataset", $Name
    )
    if (Test-Path $Stdout) { Remove-Item $Stdout -Force }
    if (Test-Path $Stderr) { Remove-Item $Stderr -Force }

    $process = Start-Process -FilePath $Exe -ArgumentList $processArgs -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -NoNewWindow -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
        Add-Content -Path $Stderr -Value "Timed out after $TimeoutSeconds seconds."
        return -9999
    }
    $process.WaitForExit()
    $process.Refresh()
    if ($null -eq $process.ExitCode) {
        return -1
    }
    return [int]$process.ExitCode
}

function Test-CanvasTooLargeLog {
    param([string]$Stdout, [string]$Stderr)
    $text = @()
    if (Test-Path $Stdout) { $text += Get-Content -Path $Stdout -Raw -ErrorAction SilentlyContinue }
    if (Test-Path $Stderr) { $text += Get-Content -Path $Stderr -Raw -ErrorAction SilentlyContinue }
    return ($text -join "`n") -match "Target canvas too large"
}

function Get-FailureReason {
    param([string]$Stdout, [string]$Stderr, [int]$ExitCode)
    $text = @()
    if (Test-Path $Stdout) { $text += Get-Content -Path $Stdout -Raw -ErrorAction SilentlyContinue }
    if (Test-Path $Stderr) { $text += Get-Content -Path $Stderr -Raw -ErrorAction SilentlyContinue }
    $joined = ($text -join "`n")
    if ($joined -match "Target canvas too large:\s*(\d+x\d+)") {
        return "panorama too large: $($Matches[1])"
    }
    if ($ExitCode -eq -9999) {
        return "timed out"
    }
    if ($ExitCode -ne 0) {
        return "exit code $ExitCode"
    }
    return "missing result image"
}

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $RepoRoot

$DatasetRootFull = [System.IO.Path]::GetFullPath($DatasetRoot)
$ResultRootFull = [System.IO.Path]::GetFullPath($ResultRoot)
$WorkRootFull = Join-Path $ResultRootFull "_work\$MethodFolder"
$PairsRoot = Join-Path $ResultRootFull "_work\pairs"
$GraphsRoot = Join-Path $ResultRootFull "_work\graphs"
$ManifestPath = Join-Path $ResultRootFull "_work\manifest.json"
$DatasetsFile = Join-Path $ResultRootFull "_work\datasets.txt"
$MetadataPath = Join-Path $WorkRootFull "run_metadata.csv"
$Method = $Method.ToLowerInvariant()
$ResultSuffix = "GES-GSP_"

if ($Smoke -and (-not $Dataset -or $Dataset.Count -eq 0)) {
    $Dataset = @("Indoor_001_p12")
}

Write-Host "Repo: $RepoRoot"
Write-Host "Dataset root: $DatasetRootFull"
Write-Host "Result root: $ResultRootFull"
Write-Host "Work root: $WorkRootFull"
Write-Host "Method: $Method ($MethodFolder)"

if (-not $SkipPrepare) {
    & $Python "tools\prepare_hd3d_pairs.py" --dataset-root $DatasetRootFull --result-root $ResultRootFull
    if ($LASTEXITCODE -ne 0) {
        throw "prepare_hd3d_pairs.py failed with exit code $LASTEXITCODE"
    }
}

$Manifest = Get-Content -Path $ManifestPath -Raw | ConvertFrom-Json
$ManifestByName = @{}
foreach ($entry in $Manifest) {
    $ManifestByName[$entry.pair_name] = $entry
}

$Datasets = @(Get-Content -Path $DatasetsFile | Where-Object { $_ -and (-not $_.TrimStart().StartsWith("#")) })
if ($Dataset -and $Dataset.Count -gt 0) {
    $selected = @{}
    foreach ($name in $Dataset) { $selected[$name.Trim()] = $true }
    $Datasets = @($Datasets | Where-Object { $selected.ContainsKey($_) })
}

New-Item -ItemType Directory -Force -Path $WorkRootFull | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $WorkRootFull "0_results") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $WorkRootFull "1_debugs") | Out-Null

if ($ForceRerun) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $WorkRootFull "0_results")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $WorkRootFull "1_debugs")
    Remove-Item -Force -ErrorAction SilentlyContinue $MetadataPath
    New-Item -ItemType Directory -Force -Path (Join-Path $WorkRootFull "0_results") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $WorkRootFull "1_debugs") | Out-Null
}

if (-not $SkipBuild) {
    $buildCmd = "`"$VsDevCmd`" -arch=x64 -host_arch=x64 && set CONDA_PREFIX=$CondaPrefix && `"$CMake`" -S Code -B build -G `"Visual Studio 17 2022`" -A x64 -DCONDA_PREFIX=`"$CondaPrefix`" && `"$CMake`" --build build --config Release --target ges_gsp -j 8"
    cmd /c $buildCmd
    if ($LASTEXITCODE -ne 0) {
        throw "C++ build failed with exit code $LASTEXITCODE"
    }
}

$RunMetadata = @()
if ((Test-Path $MetadataPath) -and (-not $ForceRerun)) {
    $RunMetadata = @((Import-Csv -Path $MetadataPath))
}

if (-not $SkipRun) {
    $env:PATH = "$RepoRoot\build\Release;$CondaPrefix\Library\bin;$CondaPrefix\Library\lib;$CondaPrefix;$env:PATH"
    $Exe = Join-Path $RepoRoot "build\Release\ges_gsp.exe"
    Set-Location (Join-Path $RepoRoot "build\Release")

    for ($i = 0; $i -lt $Datasets.Count; $i++) {
        $name = $Datasets[$i].Trim()
        $entry = $ManifestByName[$name]
        if ($null -eq $entry) {
            throw "Missing manifest entry for dataset $name"
        }

        $resultImage = Join-Path $WorkRootFull "0_results\$name-result\$name-$ResultSuffix.png"
        $methodDir = Join-Path $entry.final_pair_dir $MethodFolder
        $rawPath = Join-Path $methodDir "raw.png"

        if ($SkipExistingResults -and (Test-Path $rawPath) -and (Test-Path $resultImage)) {
            Write-Host ("[{0}/{1}] Skipping {2} (existing raw.png)" -f ($i + 1), $Datasets.Count, $name)
            continue
        }

        New-Item -ItemType Directory -Force -Path $methodDir | Out-Null
        $stdout = Join-Path $methodDir "run.log"
        $stderr = Join-Path $methodDir "error.log"
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        Write-Host ("[{0}/{1}] Running {2} (primary {3} MP)" -f ($i + 1), $Datasets.Count, $name, $MaxTargetMegapixels)
        $exitCode = Invoke-GesGspDataset -Exe $Exe -Name $name -DataRootFull $PairsRoot -GraphsRoot $GraphsRoot `
            -WorkRootFull $WorkRootFull -Method $Method -ContentWeight $ContentWeight `
            -MegapixelLimit $MaxTargetMegapixels -Stdout $stdout -Stderr $stderr -TimeoutSeconds $RunTimeoutSeconds

        $usedLimit = $MaxTargetMegapixels
        $runMode = "primary"
        $canvasTooLarge = Test-CanvasTooLargeLog -Stdout $stdout -Stderr $stderr
        if ((($exitCode -ne 0) -or $canvasTooLarge) -and (Test-Path $resultImage)) {
            $exitCode = 0
        }

        if ($AutoMegapixelFallback -and (($exitCode -ne 0) -or (-not (Test-Path $resultImage)) -or $canvasTooLarge)) {
            Write-Host ("[{0}/{1}] Retrying {2} with fallback {3} MP" -f ($i + 1), $Datasets.Count, $name, $FallbackMaxMegapixels)
            $fallbackStdout = Join-Path $methodDir "run.fallback.log"
            $fallbackStderr = Join-Path $methodDir "error.fallback.log"
            $exitCode = Invoke-GesGspDataset -Exe $Exe -Name $name -DataRootFull $PairsRoot -GraphsRoot $GraphsRoot `
                -WorkRootFull $WorkRootFull -Method $Method -ContentWeight $ContentWeight `
                -MegapixelLimit $FallbackMaxMegapixels -Stdout $fallbackStdout -Stderr $fallbackStderr -TimeoutSeconds $FallbackRunTimeoutSeconds
            $stdout = $fallbackStdout
            $stderr = $fallbackStderr
            $usedLimit = $FallbackMaxMegapixels
            $runMode = "fallback"
            if (($exitCode -ne 0) -and (Test-Path $resultImage)) {
                $exitCode = 0
            }
        }

        $stopwatch.Stop()
        $runtimeSeconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 5)
        $success = ($exitCode -eq 0) -and (Test-Path $resultImage)
        $failureReason = ""
        if ($success) {
            Copy-Item -Path $resultImage -Destination $rawPath -Force
        } else {
            $failureReason = Get-FailureReason -Stdout $stdout -Stderr $stderr -ExitCode $exitCode
        }

        $statusPayload = [ordered]@{
            method = $MethodFolder
            cli_method = $Method
            pair_name = $name
            success = $success
            runtime_seconds = $runtimeSeconds
            exit_code = $exitCode
            result_image = $resultImage
            stdout = $stdout
            stderr = $stderr
            failure_reason = $failureReason
        }
        ($statusPayload | ConvertTo-Json -Depth 4) | Set-Content -Encoding UTF8 -Path (Join-Path $methodDir "method_status.json")

        $RunMetadata = @($RunMetadata | Where-Object { $_.dataset -ne $name })
        $RunMetadata += [PSCustomObject]@{
            dataset = $name
            status = if ($success) { "ok" } else { "failed" }
            run_mode = $runMode
            megapixel_limit = $usedLimit
            exit_code = $exitCode
            runtime_seconds = $runtimeSeconds
            stdout = $stdout
            stderr = $stderr
            failure_reason = $failureReason
        }

        if (-not $success) {
            Write-Warning "$name failed: $failureReason"
        }
    }

    Set-Location $RepoRoot
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $csvWriter = New-Object System.IO.StreamWriter($MetadataPath, $false, $utf8NoBom)
    try {
        $RunMetadata | ConvertTo-Csv -NoTypeInformation | ForEach-Object { $csvWriter.WriteLine($_) }
    } finally {
        $csvWriter.Close()
    }
}

Write-Host "Done. Work root: $WorkRootFull"
