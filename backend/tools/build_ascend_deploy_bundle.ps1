param(
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path $root ('dist/yolo-backend-ascend-310p3-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
}
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $OutputPath) {
    throw "Refusing to overwrite: $OutputPath"
}

$directories = @('backend/src', 'backend/configs', 'backend/tools', 'backend/migrations', 'docker/ascend')
$files = @('backend/requirements-runtime.txt', 'backend/requirements-ascend.txt', 'docs/ascend-310p-deployment.md')
$entries = @{}
foreach ($directory in $directories) {
    $path = Join-Path $root $directory
    foreach ($file in (Get-ChildItem -LiteralPath $path -Recurse -File -Force)) {
        $relative = [IO.Path]::GetRelativePath($root, $file.FullName).Replace('\', '/')
        if ($relative -match '(^|/)(__pycache__|\.pytest_cache)(/|$)' -or
            $file.Extension -in @('.pyc', '.pyo', '.log') -or $file.Name -eq '.env') {
            continue
        }
        $entries[$relative] = $file.FullName
    }
}
foreach ($relative in $files) {
    $path = Join-Path $root $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required file missing: $relative"
    }
    $entries[$relative] = $path
}

Add-Type -AssemblyName System.IO.Compression
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($OutputPath)) | Out-Null
$stream = [IO.File]::Open($OutputPath, [IO.FileMode]::CreateNew)
$archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($relative in ($entries.Keys | Sort-Object)) {
        $entry = $archive.CreateEntry($relative, [IO.Compression.CompressionLevel]::Optimal)
        $target = $entry.Open()
        try {
            if ($relative.EndsWith('.sh')) {
                # Keep Linux entrypoints runnable even from a CRLF checkout.
                $content = [IO.File]::ReadAllText($entries[$relative]).Replace("`r`n", "`n")
                $bytes = [Text.UTF8Encoding]::new($false).GetBytes($content)
            } else {
                $bytes = [IO.File]::ReadAllBytes($entries[$relative])
            }
            $target.Write($bytes, 0, $bytes.Length)
        } finally {
            $target.Dispose()
        }
    }
} finally {
    $archive.Dispose()
    $stream.Dispose()
}

Get-Item -LiteralPath $OutputPath | Select-Object FullName, Length
Get-FileHash -LiteralPath $OutputPath -Algorithm SHA256
Write-Output "Files: $($entries.Count). Original configuration is included; treat this archive as confidential."
