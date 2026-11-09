# Postavlja lozinku za dashboard patika i šalje šifrovanu bazu na GitHub (repozitorijum run-marko/patike).
# Pokreće je Marko u terminalu; lozinku kuca samo on, ona se nigde ne ispisuje i ne čuva na disku.
#   & "D:\Provera patika\cloud\postavi-lozinku.ps1"
$ErrorActionPreference = "Stop"
$repo = "run-marko/patike"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& $gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Prvo prijava na GitHub. Kad terminal pita, samo pritisni Enter." -ForegroundColor Cyan
    & $gh auth login --hostname github.com --web --git-protocol https
    if ($LASTEXITCODE -ne 0) { throw "Prijava nije uspela." }
}

while ($true) {
    $a = Read-Host "Nova lozinka za dashboard (najmanje 8 znakova)" -AsSecureString
    $b = Read-Host "Ponovi lozinku" -AsSecureString
    $pa = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($a))
    $pb = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($b))
    if ($pa -ne $pb) { Write-Host "Lozinke se ne poklapaju, probaj ponovo." -ForegroundColor Yellow; continue }
    if ($pa.Length -lt 8) { Write-Host "Lozinka je kraća od 8 znakova." -ForegroundColor Yellow; continue }
    break
}

try {
    Write-Host "Upisujem lozinku kao tajnu na GitHubu..."
    $pa | & $gh secret set PAGE_PASSWORD --repo $repo
    if ($LASTEXITCODE -ne 0) { throw "Tajna nije upisana." }

    Write-Host "Pakujem i šifrujem bazu..."
    $tmp = Join-Path $env:TEMP "patike-stanje"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    & "$root\.venv\Scripts\python.exe" -m cloud.state pack "$tmp\stanje.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw "Pakovanje baze nije uspelo." }
    $env:PAGE_PASSWORD = $pa
    & node "$root\cloud\crypt.mjs" enc "$tmp\stanje.tar.gz" "$tmp\stanje.enc"
    if ($LASTEXITCODE -ne 0) { throw "Šifrovanje nije uspelo." }

    Write-Host "Šaljem šifrovanu bazu na GitHub..."
    & $gh release view stanje --repo $repo *> $null
    if ($LASTEXITCODE -eq 0) {
        & $gh release upload stanje "$tmp\stanje.enc" --repo $repo --clobber
    } else {
        & $gh release create stanje "$tmp\stanje.enc" --repo $repo --title "Stanje baze (šifrovano)" --notes "Baza, keš, kurs, oznake i sličice, šifrovano lozinkom. Ne brisati."
    }
    if ($LASTEXITCODE -ne 0) { throw "Slanje baze nije uspelo." }
    Remove-Item -Recurse -Force $tmp
    Write-Host ""
    Write-Host "Gotovo. Lozinka je postavljena i baza je na GitHubu." -ForegroundColor Green
} finally {
    Remove-Item Env:PAGE_PASSWORD -ErrorAction SilentlyContinue
    $pa = $null; $pb = $null
}
