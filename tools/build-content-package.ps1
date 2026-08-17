<#
    Build a MegaMod base-content package (.uqm) from the UQM-MegaMod-Content checkout.

    A .uqm file is a plain zip. The game locates it in content/packages/ by matching
    CRC32 of the FILENAME against BASE_CONTENT_NAME ("mm-<version>-content.uqm"), then
    mounts it at "/". The archive must therefore contain base/ at its root, alongside
    menu.key, uqm.key and uqm.rmp.

    IMPORTANT: the content repo must be checked out with core.autocrlf=false. Git's
    default on Windows (autocrlf=true) rewrites LF to CRLF in the ~826 text asset
    files (.ani descriptors, uqm.rmp), which the game's parser cannot read - the game
    still launches but renders corrupted fonts, menus and animations.
#>
param(
    [string]$ContentRepo    = "C:\src\github.com\GreenFuze\UQMAI\uqm-megamod-content",
    [string]$OutFile        = "C:\src\github.com\GreenFuze\UQMAI\uqm-megamod\content\packages\mm-0.8.5-content.uqm",
    [string]$ExpectedTag    = "0.8.5",
    [switch]$AllowTagMismatch
)

Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression

# Guard: refuse to build from a CRLF-mangled checkout, since the result would be
# a silently corrupt package that still boots.
$autocrlf = (git -C $ContentRepo config --get core.autocrlf)
if ($autocrlf -eq 'true') {
    throw "core.autocrlf=true in $ContentRepo - text assets are CRLF-corrupted. Run: git -C '$ContentRepo' config core.autocrlf false; git -C '$ContentRepo' rm --cached -r . ; git -C '$ContentRepo' reset --hard"
}

# Guard: the content repo must be checked out at the tag matching the game version.
# Content master runs ahead of the release, and base/gamestrings.txt is position-indexed
# by the executable - a newer string table silently shifts every menu label and shows
# the wrong strings rather than failing loudly.
$describe = (git -C $ContentRepo describe --tags --exact-match 2>$null)
if ($describe -ne $ExpectedTag) {
    $msg = "content repo is at '$describe', expected tag '$ExpectedTag'. Run: git -C '$ContentRepo' checkout $ExpectedTag"
    if ($AllowTagMismatch) { Write-Warning $msg } else { throw $msg }
}

# Collect the payload: everything under base/, plus the three loose root files.
# NOTE: explicit directory entries are REQUIRED. uio's zip reader builds its directory
# tree from zero-length entries whose names end in '/' (classified via S_ISDIR in
# src/libs/uio/zip/zip.c). Omitting them yields an archive that mounts without error but
# whose subdirectories cannot be walked - the game boots and renders corrupted assets.
$dirEntries = @()
$baseDir = Join-Path $ContentRepo "base"
$dirEntries += "base/"
foreach ($d in Get-ChildItem $baseDir -Recurse -Directory) {
    $dirEntries += "base/" + $d.FullName.Substring($baseDir.Length + 1).Replace('\', '/') + "/"
}

# Directories that are empty in the shipped package. Git cannot represent an empty
# directory, so they are absent from the content checkout and must be re-added by hand
# to match the official archive.
foreach ($d in @("base/ui/meleeatlas/")) {
    if ($dirEntries -notcontains $d) { $dirEntries += $d }
}

$items = @()
foreach ($f in Get-ChildItem $baseDir -Recurse -File) {
    $rel = "base/" + $f.FullName.Substring($baseDir.Length + 1).Replace('\', '/')
    $items += [pscustomobject]@{ Source = $f.FullName; Entry = $rel }
}
foreach ($n in @('menu.key', 'uqm.key', 'uqm.rmp')) {
    $p = Join-Path $ContentRepo $n
    if (-not (Test-Path $p)) { throw "missing required root file: $n" }
    $items += [pscustomobject]@{ Source = $p; Entry = $n }
}

Write-Host ("packaging {0} files -> {1}" -f $items.Count, $OutFile)

# Write the archive fresh; a stale partial package would be worse than none.
$outDir = Split-Path $OutFile -Parent
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
if (Test-Path $OutFile) { Remove-Item $OutFile -Force }

# MS-DOS attribute bits, as used by the official package. uio's zip reader decides
# whether an entry is a directory from the DIRECTORY bit here - NOT from the trailing
# slash alone. .NET leaves ExternalAttributes at 0, which makes the reader treat a
# "base/" entry as a file with an illegal name and skip it:
#   Warning: 'base/' is not a valid file name - skipped.
$FILE_ATTRIBUTE_DIRECTORY = 0x10
$FILE_ATTRIBUTE_ARCHIVE   = 0x20

$zip = [System.IO.Compression.ZipFile]::Open($OutFile, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    # Directory records first, so the tree exists before files are placed into it.
    foreach ($d in $dirEntries) {
        $e = $zip.CreateEntry($d, [System.IO.Compression.CompressionLevel]::NoCompression)
        $e.ExternalAttributes = $FILE_ATTRIBUTE_DIRECTORY -bor $FILE_ATTRIBUTE_ARCHIVE
    }
    foreach ($it in $items) {
        $e = [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $it.Source, $it.Entry,
            [System.IO.Compression.CompressionLevel]::Optimal)
        $e.ExternalAttributes = $FILE_ATTRIBUTE_ARCHIVE
    }
}
finally {
    $zip.Dispose()
}

$mb = [math]::Round((Get-Item $OutFile).Length / 1MB, 2)
Write-Host ("done: {0} MB" -f $mb)
