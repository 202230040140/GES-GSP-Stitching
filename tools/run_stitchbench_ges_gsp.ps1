param(
    [string]$DataRoot = "D:\StitchBench\General",
    [string]$ExperimentRoot = "experiments\stitchbench_general\ges_gsp",
    [string]$Python = "C:\Users\22499\anaconda3\python.exe",
    [string]$Method = "ges-gsp",
    [double]$ContentWeight = 1.5,
    [double]$MaxTargetMegapixels = 80.0,
    [double]$FallbackMaxMegapixels = 1500.0,
    [string]$CondaPrefix = "C:\Users\22499\anaconda3\envs\obj-gsp-cpp",
    [string]$VsDevCmd = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat",
    [string]$CMake = "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe",
    [string[]]$Dataset,
    [string]$DatasetListFile = "",
    [int]$RunTimeoutSeconds = 180,
    [int]$FallbackRunTimeoutSeconds = 900,
    [switch]$Smoke,
    [switch]$SkipExistingResults,
    [switch]$ForceRerun,
    [switch]$AutoMegapixelFallback = $false,
    [switch]$SkipBuild,
    [switch]$SkipRun,
    [switch]$SkipEval,
    [switch]$SkipNIQE
)

$ErrorActionPreference = "Stop"

function Invoke-GesGspDataset {
    param(
        [string]$Exe,
        [string]$Name,
        [string]$DataRootFull,
        [string]$GraphsRoot,
        [string]$ExperimentRootFull,
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
        "--output-root", $ExperimentRootFull,
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

function Test-PrimaryResultValid {
    param(
        [string]$ResultImage,
        [string]$MetadataPath,
        [string]$DatasetName,
        [double]$PrimaryMegapixelLimit
    )
    if (-not (Test-Path $ResultImage)) {
        return $false
    }
    if (-not (Test-Path $MetadataPath)) {
        return $true
    }
    $rows = Import-Csv -Path $MetadataPath
    $row = $rows | Where-Object { $_.dataset -eq $DatasetName } | Select-Object -First 1
    if ($null -eq $row) {
        return $true
    }
    return ($row.status -eq "ok") -and ([double]$row.megapixel_limit -le $PrimaryMegapixelLimit)
}

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location $RepoRoot

$DataRootFull = [System.IO.Path]::GetFullPath($DataRoot)
if ([System.IO.Path]::IsPathRooted($ExperimentRoot)) {
    $ExperimentRootFull = [System.IO.Path]::GetFullPath($ExperimentRoot)
} else {
    $ExperimentRootFull = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $ExperimentRoot))
}
$Method = $Method.ToLowerInvariant()
$GraphsRoot = Join-Path $ExperimentRootFull "graphs"
$LogsRoot = Join-Path $ExperimentRootFull "logs"
$DatasetsFile = Join-Path $ExperimentRootFull "datasets.txt"
$MetadataPath = Join-Path $ExperimentRootFull "run_metadata.csv"
$ExcludedDatasetsFile = Join-Path $PSScriptRoot "excluded_datasets.txt"

function Read-ExcludedDatasets {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return @()
    }
    return @(
        Get-Content -Path $Path |
            Where-Object { $_ -and (-not $_.TrimStart().StartsWith("#")) } |
            ForEach-Object { $_.Trim() }
    )
}

switch ($Method) {
    "gsp" { $ResultSuffix = "GSP_" }
    "ges-gsp" { $ResultSuffix = "GES-GSP_" }
    default { throw "Unknown method: $Method" }
}

if (-not [string]::IsNullOrWhiteSpace($DatasetListFile)) {
    $Dataset = @(Get-Content -Path $DatasetListFile | Where-Object { $_ -and (-not $_.TrimStart().StartsWith("#")) })
}

if ($Smoke -and (-not $Dataset -or $Dataset.Count -eq 0)) {
    $Dataset = @("AANAP-01_skyline")
}

Write-Host "Repo: $RepoRoot"
Write-Host "Data root: $DataRootFull"
Write-Host "Experiment root: $ExperimentRootFull"
Write-Host "Method: $Method"
Write-Host "Primary megapixel limit: $MaxTargetMegapixels"
Write-Host "Fallback megapixel limit: $FallbackMaxMegapixels (enabled=$AutoMegapixelFallback)"

$prepareArgs = @(
    "tools\prepare_stitchbench_general.py",
    "--data-root", $DataRootFull,
    "--experiment-root", $ExperimentRootFull
)
if ($Dataset -and $Dataset.Count -gt 0) {
    foreach ($name in $Dataset) {
        $prepareArgs += @("--dataset", $name)
        $prepareArgs += @("--allow-count-mismatch")
    }
}
& $Python @prepareArgs
if ($LASTEXITCODE -ne 0) {
    throw "prepare_stitchbench_general.py failed with exit code $LASTEXITCODE"
}

$Datasets = @(Get-Content -Path $DatasetsFile | Where-Object { $_ -and (-not $_.TrimStart().StartsWith("#")) })
$ExcludedDatasets = @(Read-ExcludedDatasets -Path $ExcludedDatasetsFile)
if ($ExcludedDatasets.Count -gt 0) {
    Write-Host ("Excluded datasets ({0}): {1}" -f $ExcludedDatasets.Count, ($ExcludedDatasets -join ", "))
}
New-Item -ItemType Directory -Force -Path $LogsRoot | Out-Null
Copy-Item -Path $ExcludedDatasetsFile -Destination (Join-Path $ExperimentRootFull "excluded_datasets.txt") -Force -ErrorAction SilentlyContinue

if ($ForceRerun) {
    Write-Host "ForceRerun: clearing previous results and metadata."
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ExperimentRootFull "0_results")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ExperimentRootFull "1_debugs")
    Remove-Item -Force -ErrorAction SilentlyContinue $MetadataPath
    New-Item -ItemType Directory -Force -Path (Join-Path $ExperimentRootFull "0_results") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $ExperimentRootFull "1_debugs") | Out-Null
}

if ((-not $SkipEval) -and (-not $SkipNIQE)) {
    & $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pyiqa') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing pyiqa for NIQE evaluation..."
        & $Python -m pip install pyiqa
        if ($LASTEXITCODE -ne 0) {
            throw "pip install pyiqa failed with exit code $LASTEXITCODE"
        }
    }
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
    $Failures = @()

    for ($i = 0; $i -lt $Datasets.Count; $i++) {
        $name = $Datasets[$i].Trim()
        $resultImage = Join-Path $ExperimentRootFull "0_results\$name-result\$name-$ResultSuffix.png"

        if ($ExcludedDatasets -contains $name) {
            Write-Host ("[{0}/{1}] Skipping {2} (excluded)" -f ($i + 1), $Datasets.Count, $name)
            $RunMetadata = @($RunMetadata | Where-Object { $_.dataset -ne $name })
            $RunMetadata += [PSCustomObject]@{
                dataset = $name
                status = "excluded"
                run_mode = "excluded"
                megapixel_limit = ""
                exit_code = ""
                stdout = ""
                stderr = "Excluded by tools/excluded_datasets.txt"
            }
            continue
        }

        if ($SkipExistingResults -and (Test-PrimaryResultValid -ResultImage $resultImage -MetadataPath $MetadataPath -DatasetName $name -PrimaryMegapixelLimit $MaxTargetMegapixels)) {
            Write-Host ("[{0}/{1}] Skipping {2} (valid primary result)" -f ($i + 1), $Datasets.Count, $name)
            if (-not ($RunMetadata | Where-Object { $_.dataset -eq $name })) {
                $RunMetadata += [PSCustomObject]@{
                    dataset = $name
                    status = "ok"
                    run_mode = "primary"
                    megapixel_limit = $MaxTargetMegapixels
                    exit_code = 0
                    stdout = ""
                    stderr = ""
                }
            }
            continue
        }

        Write-Host ("[{0}/{1}] Running {2} (primary {3} MP)" -f ($i + 1), $Datasets.Count, $name, $MaxTargetMegapixels)
        $stdout = Join-Path $LogsRoot "$name.out.log"
        $stderr = Join-Path $LogsRoot "$name.err.log"
        $exitCode = Invoke-GesGspDataset -Exe $Exe -Name $name -DataRootFull $DataRootFull -GraphsRoot $GraphsRoot `
            -ExperimentRootFull $ExperimentRootFull -Method $Method -ContentWeight $ContentWeight `
            -MegapixelLimit $MaxTargetMegapixels -Stdout $stdout -Stderr $stderr -TimeoutSeconds $RunTimeoutSeconds

        $usedLimit = $MaxTargetMegapixels
        $runMode = "primary"
        $canvasTooLarge = Test-CanvasTooLargeLog -Stdout $stdout -Stderr $stderr
        if ((($exitCode -ne 0) -or $canvasTooLarge) -and (Test-Path $resultImage)) {
            $exitCode = 0
        }

        if ($AutoMegapixelFallback -and (($exitCode -ne 0) -or (-not (Test-Path $resultImage)) -or $canvasTooLarge)) {
            Write-Host ("[{0}/{1}] Retrying {2} with fallback {3} MP" -f ($i + 1), $Datasets.Count, $name, $FallbackMaxMegapixels)
            $fallbackStdout = Join-Path $LogsRoot "$name.fallback.out.log"
            $fallbackStderr = Join-Path $LogsRoot "$name.fallback.err.log"
            $exitCode = Invoke-GesGspDataset -Exe $Exe -Name $name -DataRootFull $DataRootFull -GraphsRoot $GraphsRoot `
                -ExperimentRootFull $ExperimentRootFull -Method $Method -ContentWeight $ContentWeight `
                -MegapixelLimit $FallbackMaxMegapixels -Stdout $fallbackStdout -Stderr $fallbackStderr -TimeoutSeconds $FallbackRunTimeoutSeconds
            $stdout = $fallbackStdout
            $stderr = $fallbackStderr
            $usedLimit = $FallbackMaxMegapixels
            $runMode = "fallback"
            if (($exitCode -ne 0) -and (Test-Path $resultImage)) {
                $exitCode = 0
            }
        }

        $status = if (($exitCode -eq 0) -and (Test-Path $resultImage)) { "ok" } else { "failed" }
        $RunMetadata = @($RunMetadata | Where-Object { $_.dataset -ne $name })
        $RunMetadata += [PSCustomObject]@{
            dataset = $name
            status = $status
            run_mode = $runMode
            megapixel_limit = $usedLimit
            exit_code = $exitCode
            stdout = $stdout
            stderr = $stderr
        }

        if ($status -ne "ok") {
            $Failures += [PSCustomObject]@{
                dataset = $name
                exit_code = $exitCode
                stdout = $stdout
                stderr = $stderr
            }
            Write-Warning "$name failed with exit code $exitCode"
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

    $failedPath = Join-Path $ExperimentRootFull "failed_runs.csv"
    if ($Failures.Count -gt 0) {
        $failedWriter = New-Object System.IO.StreamWriter($failedPath, $false, $utf8NoBom)
        try {
            $Failures | ConvertTo-Csv -NoTypeInformation | ForEach-Object { $failedWriter.WriteLine($_) }
        } finally {
            $failedWriter.Close()
        }
    }
    else {
        "dataset,exit_code,stdout,stderr" | Set-Content -Encoding UTF8 -Path $failedPath
    }
}

if (-not $SkipEval) {
    $evalArgs = @(
        "tools\evaluate_stitchbench_ges_gsp.py",
        "--experiment-root", $ExperimentRootFull,
        "--datasets-file", $DatasetsFile,
        "--device", "cpu",
        "--result-suffix", $ResultSuffix
    )
    if ($SkipNIQE) {
        $evalArgs += "--skip-niqe"
    }
    & $Python @evalArgs
    if ($LASTEXITCODE -ne 0) {
        throw "evaluate_stitchbench_ges_gsp.py failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Done. Report: $(Join-Path $ExperimentRootFull 'report.md')"
