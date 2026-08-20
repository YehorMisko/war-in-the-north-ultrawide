# Ultrawide (21:9 / 32:9) patch for
# The Lord of the Rings: War in the North - Legacy Edition
#
# Run ultrawide_patch.bat, or:
#     powershell -ExecutionPolicy Bypass -File ultrawide_patch.ps1
#
# Every range is checked before anything is written. If a range holds neither
# the stock bytes nor the patched bytes the script bails out and leaves the
# file alone. Backup goes to witn.exe.orig.

param([ValidateSet('menu','status','apply','revert','report','setres')][string]$Action = 'menu',
      [int]$Width = 0, [int]$Height = 0)

$ErrorActionPreference = 'Stop'

$dir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$exe = Join-Path $dir 'witn.exe'
$bak = Join-Path $dir 'witn.exe.orig'

$KNOWN_STOCK   = 'ADF1753F4F182E3C4287EEEA19CEDDB5B91F3386C73D1D17B8F0B8171A9C219F'
$KNOWN_PATCHED = 'A10ADADAEEB5BF6E368B723B9839908B5AD33A469851F381102FD55894E4D7CA'

$patches = @(
    @{ Name='aspect_table'; Offset=0x00A46C50; Stock='398ee33fcdcccc3fabaaaa3f0000a03f'; Patched='398ee33fcdcccc3f03781740398e6340'; Desc='aspect-ratio whitelist: 4:3 and 5:4 -> 21:9 and 32:9' },
    @{ Name='tolerance'; Offset=0x007A2A7C; Stock='a0f82400'; Patched='e4ac2500'; Desc='mode-match tolerance: 0.0001 -> 0.04' },
    @{ Name='gui_canvas'; Offset=0x00208A28; Stock='f30f5edd0f28d4f30f5915b9c97e000f28cbf30f591d6a4f7f00f30f59caf30f59dcf30f1111f30f1149040f28c34881c408010000c3cccccccccccccccc'; Patched='0f28c5f30f5d2d794f7f00f30f5eddf30f5925b1c97e00f30f59e30f28ccf30f59c8f30f1109f30f1161040f28c4f30f5e055ea27f004881c408010000c3'; Desc='UI canvas: width-anchored -> height-anchored above 16:9' },
    @{ Name='gui_pdata'; Offset=0x00BAFD30; Stock='5e962000'; Patched='66962000'; Desc='unwind EndAddress for the rewritten canvas function' },
    @{ Name='hud_proj'; Offset=0x00208BC3; Stock='410f28f8f3440f11442430f30f5efb0f28c40f57c9f30f590510c87e000f28f7f30f59f5f30f59f8f3440f59c8f3410f104104f3410f5c01410f28d1f30f114c2428f3410f5cd0f30f114c2420f30f59f4f30f59f80f28dff3410f5cd8'; Patched='e9e8be7d00cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'; Desc='HUD ortho projection: jmp to the rewritten canvas math' },
    @{ Name='hud_cave'; Offset=0x009E4AB0; Stock='00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'; Patched='0f28d3f30f5ddd410f28f8f3440f11442430f30f5efb0f28c40f57c9f30f59051c090100f30f59f80f28c7f30f59c20f28f7f30f5e3502090100f3440f59c8f3410f104104f3410f5c01410f28d1f30f114c2428f3410f5cd0f30f114c2420f30f59f80f28dff3410f5cd8e9004182ff'; Desc='the rewritten math, in the .text tail padding' },
    @{ Name='text_vsize'; Offset=0x00000268; Stock='af469e00'; Patched='00489e00'; Desc='.text VirtualSize raised so the padding is mapped' },
    @{ Name='cam_aspect'; Offset=0x00280664; Stock='c7058ee88e00398ee33f'; Patched='e8b74476000f1f440000'; Desc='character-select camera: hardcoded 16/9 -> real aspect' },
    @{ Name='cam_cave'; Offset=0x009E4B20; Stock='0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'; Patched='4883ec18f30f110424f30f114c24044889442408488d05955a1800837848007e1683784c007e10f30f2a4048f30f2a484cf30f5ec1eb08f30f10054d8e0100f30f110595a31800488b442408f30f104c2404f30f1004244883c418c3'; Desc='the aspect helper, in the .text tail padding' },
    @{ Name='fov_projA'; Offset=0x007B0D4D; Stock='f30f5ec848c744242c00000000'; Patched='e92e3e23009090909090909090'; Desc='gameplay FOV: jmp out of the plain perspective builder' },
    @{ Name='fov_caveA'; Offset=0x009E4B80; Stock='000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'; Patched='f30f5ec8f30f108424e00000000f2f05188e0100760cf30f590d0e8e0100f30f5ec848c744242c00000000e9aac1dcff'; Desc='Vert- -> Hor+ correction, in the .text tail padding' },
    @{ Name='fov_projB'; Offset=0x007B129C; Stock='f30f5ee048c744242c00000000'; Patched='e90f3923009090909090909090'; Desc='gameplay FOV: jmp out of the concatenating builder' },
    @{ Name='fov_caveB'; Offset=0x009E4BB0; Stock='000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'; Patched='f30f5ee0f30f108424500100000f2f05e88d0100760cf30f5925de8d0100f30f5ee048c744242c00000000e9c9c6dcff'; Desc='the same correction for that builder' }
)

function HexToBytes([string]$h) {
    $n = $h.Length / 2
    $b = New-Object byte[] $n
    for ($i = 0; $i -lt $n; $i++) { $b[$i] = [Convert]::ToByte($h.Substring($i * 2, 2), 16) }
    return ,$b
}

function RegionState($buf, $p) {
    $want = HexToBytes $p.Stock
    $new  = HexToBytes $p.Patched
    if (($p.Offset + $want.Length) -gt $buf.Length) { return 'off-end' }
    $isStock = $true; $isPatched = $true
    for ($i = 0; $i -lt $want.Length; $i++) {
        $c = $buf[$p.Offset + $i]
        if ($c -ne $want[$i]) { $isStock = $false }
        if ($c -ne $new[$i])  { $isPatched = $false }
    }
    if ($isPatched) { return 'patched' }
    if ($isStock)   { return 'stock' }
    return 'unknown'
}

function ReadExe {
    if (-not (Test-Path $exe)) {
        throw "witn.exe not found in $dir - put this script in the game folder, next to witn.exe."
    }
    return [System.IO.File]::ReadAllBytes($exe)
}

function Show-Status {
    $buf = ReadExe
    Write-Host ""
    Write-Host "witn.exe : $exe"
    if (Test-Path $bak) { Write-Host "backup   : $bak" } else { Write-Host "backup   : (none yet)" }
    Write-Host ""
    foreach ($p in $patches) {
        $st = RegionState $buf $p
        Write-Host ("  [{0,-7}] {1,-14}  {2}" -f $st, $p.Name, $p.Desc)
    }
    Write-Host ""
}

function Write-Report {
    $out = Join-Path $dir 'ultrawide_report.txt'
    $L = New-Object System.Collections.ArrayList

    [void]$L.Add("War in the North ultrawide patch - diagnostic report")
    [void]$L.Add("generated  : " + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
    [void]$L.Add("folder     : " + $dir)
    [void]$L.Add("OS         : " + [System.Environment]::OSVersion.VersionString)
    [void]$L.Add("PowerShell : " + $PSVersionTable.PSVersion.ToString())
    [void]$L.Add("")

    if (-not (Test-Path $exe)) {
        [void]$L.Add("witn.exe   : *** NOT FOUND IN THIS FOLDER ***")
        [void]$L.Add("")
        [void]$L.Add("The script is not sitting next to the game executable.")
        [void]$L.Add("Move both files into the folder that contains witn.exe")
        [void]$L.Add("and run it again.")
        Set-Content -Path $out -Value $L -Encoding UTF8
        Write-Host ""
        Write-Host "  witn.exe was not found in this folder." -ForegroundColor Red
        Write-Host "  wrote $out"
        return
    }

    $fi  = Get-Item $exe
    $sha = (Get-FileHash $exe -Algorithm SHA256).Hash
    [void]$L.Add(("witn.exe   : {0} bytes, modified {1}" -f $fi.Length, $fi.LastWriteTime))
    [void]$L.Add("  sha256   : " + $sha)
    if ($sha -eq $KNOWN_PATCHED) {
        [void]$L.Add("  verdict  : MATCHES the known fully-patched build")
    } elseif ($sha -eq $KNOWN_STOCK) {
        [void]$L.Add("  verdict  : MATCHES the known untouched build - patch NOT applied")
    } else {
        [void]$L.Add("  verdict  : matches neither known build (different game version?)")
    }
    [void]$L.Add("")

    if (Test-Path $bak) {
        $bi = Get-Item $bak
        [void]$L.Add(("witn.exe.orig : {0} bytes, modified {1}" -f $bi.Length, $bi.LastWriteTime))
        [void]$L.Add("  sha256      : " + (Get-FileHash $bak -Algorithm SHA256).Hash)
    } else {
        [void]$L.Add("witn.exe.orig : absent - the patcher has never written to this install")
    }
    [void]$L.Add("")

    $buf = [System.IO.File]::ReadAllBytes($exe)
    [void]$L.Add("patch states:")
    foreach ($p in $patches) {
        $st = RegionState $buf $p
        [void]$L.Add(("  [{0,-7}] {1}" -f $st, $p.Name))
        if ($st -eq 'unknown') {
            $n = [Math]::Min(24, ($p.Stock.Length / 2))
            $got = ''
            for ($i = 0; $i -lt $n; $i++) { $got += '{0:x2}' -f $buf[$p.Offset + $i] }
            [void]$L.Add(("             at 0x{0:X8}" -f $p.Offset))
            [void]$L.Add("             expected " + $p.Stock.Substring(0, $n * 2))
            [void]$L.Add("             found    " + $got)
        }
    }
    [void]$L.Add("")
    $gs = Join-Path $env:LOCALAPPDATA 'Aspyr\War in the North\GameSettings.dat'
    if (Test-Path $gs) {
        $g = [System.IO.File]::ReadAllBytes($gs)
        $gw = [BitConverter]::ToInt32($g, 0x2C)
        $gh = [BitConverter]::ToInt32($g, 0x30)
        $gi = Get-Item $gs
        [void]$L.Add(("game settings : resolution {0} x {1}   (saved {2})" -f $gw, $gh, $gi.LastWriteTime))
        if ($gh -gt 0 -and $gw -gt 0) {
            $ar = [double]$gw / [double]$gh
            $clamped = [Math]::Min($ar, 16.0 / 9.0)
            $canvasH = 1280.0 / $clamped
            $canvasW = $canvasH * $ar
            $iv = [System.Globalization.CultureInfo]::InvariantCulture
            [void]$L.Add("  aspect      : " + $ar.ToString("F6", $iv))
            [void]$L.Add("  canvas      : " + $canvasW.ToString("F2", $iv) + " x " + $canvasH.ToString("F2", $iv) + "  (design box is 1280 x 720)")
            if ($canvasH -lt 719.5 -or $canvasW -lt 1279.5) {
                [void]$L.Add("  expectation : HUD WOULD BE CLIPPED at this resolution")
            } else {
                [void]$L.Add("  expectation : HUD should be fully visible at this resolution")
            }
        }
    } else {
        [void]$L.Add("game settings : GameSettings.dat not found - has the game been run yet?")
    }

    $disp = @()
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        foreach ($sc in [System.Windows.Forms.Screen]::AllScreens) {
            $b = $sc.Bounds
            $disp += ("{0} x {1}{2}" -f $b.Width, $b.Height, $(if ($sc.Primary) { " (primary)" } else { "" }))
        }
    } catch { }
    if ($disp.Count -gt 0) { [void]$L.Add("desktop       : " + ($disp -join ', ')) }

    [void]$L.Add("")
    [void]$L.Add("Attach this file when reporting a problem.")

    Set-Content -Path $out -Value $L -Encoding UTF8
    Write-Host ""
    foreach ($line in $L) { Write-Host ("  " + $line) }
    Write-Host ""
    Write-Host "  wrote $out - attach that file when reporting a problem." -ForegroundColor Green
}

function Invoke-Apply {
    $buf = ReadExe
    foreach ($p in $patches) {
        $st = RegionState $buf $p
        if ($st -eq 'unknown' -or $st -eq 'off-end') {
            throw ("Patch '{0}': the bytes at 0x{1:X8} are neither stock nor patched. " -f $p.Name, $p.Offset) +
                  "This is not the game build the patch was written for. " +
                  "Run this script again and press D to write a diagnostic report."
        }
    }
    if (-not (Test-Path $bak)) {
        Copy-Item $exe $bak
        Write-Host "  backed up untouched exe -> witn.exe.orig"
    } else {
        Write-Host "  backup already present  -> witn.exe.orig"
    }
    $changed = 0
    foreach ($p in $patches) {
        if ((RegionState $buf $p) -eq 'patched') { continue }
        $new = HexToBytes $p.Patched
        for ($i = 0; $i -lt $new.Length; $i++) { $buf[$p.Offset + $i] = $new[$i] }
        Write-Host ("  patched {0}" -f $p.Name)
        $changed++
    }
    if ($changed -gt 0) {
        [System.IO.File]::WriteAllBytes($exe, $buf)
        Write-Host ""
        Write-Host "  done - $changed patch(es) written." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "  already fully patched, nothing to do." -ForegroundColor Green
    }
    $sha = (Get-FileHash $exe -Algorithm SHA256).Hash
    if ($sha -ne $KNOWN_PATCHED) {
        Write-Host ""
        Write-Host "  WARNING: the result does not match the expected patched build." -ForegroundColor Yellow
        Write-Host "  Run this again and press D to write a diagnostic report." -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  Now launch the game and pick your ultrawide resolution under"
    Write-Host "  Options -> Settings -> Video -> Change Resolution."
}

function Get-Crc32([byte[]]$bytes) {
    $MASK = 4294967295L
    $table = New-Object 'System.Int64[]' 256
    for ($i = 0; $i -lt 256; $i++) {
        $c = [int64]$i
        for ($j = 0; $j -lt 8; $j++) {
            if ($c -band 1L) { $c = (3988292384L -bxor ($c -shr 1)) -band $MASK }
            else             { $c = ($c -shr 1) -band $MASK }
        }
        $table[$i] = $c
    }
    $crc = $MASK
    foreach ($b in $bytes) {
        $idx = [int](($crc -bxor [int64]$b) -band 255L)
        $crc = ($table[$idx] -bxor ($crc -shr 8)) -band $MASK
    }
    return ($crc -bxor $MASK) -band $MASK
}

function Invoke-SetRes([int]$w, [int]$h) {
    $gs = Join-Path $env:LOCALAPPDATA 'Aspyr\War in the North\GameSettings.dat'
    if (-not (Test-Path $gs)) {
        Write-Host ""
        Write-Host "  GameSettings.dat not found - launch the game once first." -ForegroundColor Red
        Write-Host "  looked in $gs"
        return
    }

    if ($w -le 0 -or $h -le 0) {
        Write-Host ""
        Write-Host "  Which resolution should the game use?"
        try {
            Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
            foreach ($sc in [System.Windows.Forms.Screen]::AllScreens) {
                $b = $sc.Bounds
                Write-Host ("    your display reports {0} x {1}{2}" -f $b.Width, $b.Height, $(if ($sc.Primary) { " (primary)" } else { "" }))
            }
        } catch { }
        $txt = Read-Host "  width x height (for example 2560x1080)"
        $m = [regex]::Match($txt.Trim(), '^\s*(\d{3,5})\s*[xX*, ]\s*(\d{3,5})\s*$')
        if (-not $m.Success) { Write-Host "  did not understand that - nothing done." -ForegroundColor Red; return }
        $w = [int]$m.Groups[1].Value
        $h = [int]$m.Groups[2].Value
    }

    $g = [System.IO.File]::ReadAllBytes($gs)
    if ($g.Length -lt 0x38) { Write-Host "  GameSettings.dat is too small - not touching it." -ForegroundColor Red; return }

    $ver = [BitConverter]::ToUInt32($g, 4)
    if ($ver -ne 9) {
        Write-Host ("  unexpected GameSettings.dat version {0} - not touching it." -f $ver) -ForegroundColor Red
        return
    }
    $body = New-Object byte[] ($g.Length - 4)
    [Array]::Copy($g, 4, $body, 0, $body.Length)
    if ([BitConverter]::ToUInt32($g, 0) -ne (Get-Crc32 $body)) {
        Write-Host "  GameSettings.dat checksum does not validate - not touching it." -ForegroundColor Red
        return
    }

    $ow = [BitConverter]::ToInt32($g, 0x2C)
    $oh = [BitConverter]::ToInt32($g, 0x30)

    $sbak = $gs + '.uwbak'
    if (-not (Test-Path $sbak)) { Copy-Item $gs $sbak; Write-Host ("  backed up -> " + (Split-Path -Leaf $sbak)) }

    [Array]::Copy([BitConverter]::GetBytes([int32]$w), 0, $g, 0x2C, 4)
    [Array]::Copy([BitConverter]::GetBytes([int32]$h), 0, $g, 0x30, 4)
    [Array]::Copy($g, 4, $body, 0, $body.Length)
    [Array]::Copy([BitConverter]::GetBytes([uint32](Get-Crc32 $body)), 0, $g, 0, 4)
    [System.IO.File]::WriteAllBytes($gs, $g)

    Write-Host ""
    Write-Host ("  resolution {0} x {1}  ->  {2} x {3}   (checksum fixed up)" -f $ow, $oh, $w, $h) -ForegroundColor Green
    $iv = [System.Globalization.CultureInfo]::InvariantCulture
    $ar = [double]$w / [double]$h
    Write-Host ("  aspect is " + $ar.ToString("F6", $iv))
    Write-Host "  Start the game. Do not touch Change Resolution first - just look."
}

function Invoke-Revert {
    if (Test-Path $bak) {
        Copy-Item $bak $exe -Force
        Write-Host ""
        Write-Host "  restored the untouched witn.exe." -ForegroundColor Green
        return
    }
    $buf = ReadExe
    foreach ($p in $patches) {
        if ((RegionState $buf $p) -eq 'unknown') {
            throw "No backup file, and the bytes do not look like this patch. Use Steam - Verify integrity of game files instead."
        }
    }
    foreach ($p in $patches) {
        $old = HexToBytes $p.Stock
        for ($i = 0; $i -lt $old.Length; $i++) { $buf[$p.Offset + $i] = $old[$i] }
    }
    [System.IO.File]::WriteAllBytes($exe, $buf)
    Write-Host ""
    Write-Host "  reverted in place (there was no backup file)." -ForegroundColor Green
}

try {
    Write-Host ""
    Write-Host "  War in the North - ultrawide patch" -ForegroundColor Cyan
    Write-Host "  ----------------------------------"

    if ($Action -eq 'status') { Show-Status; exit 0 }
    if ($Action -eq 'apply')  { Invoke-Apply; exit 0 }
    if ($Action -eq 'revert') { Invoke-Revert; exit 0 }
    if ($Action -eq 'report') { Write-Report; exit 0 }
    if ($Action -eq 'setres') { Invoke-SetRes $Width $Height; exit 0 }

    Show-Status
    Write-Host "  [A] apply the patch"
    Write-Host "  [R] revert to the untouched exe"
    Write-Host "  [S] set the resolution the game starts at"
    Write-Host "  [D] write a diagnostic report to send back"
    Write-Host "  [Q] quit"
    Write-Host ""
    $k = Read-Host "  choice"
    switch ($k.Trim().ToUpper()) {
        'A' { Invoke-Apply }
        'R' { Invoke-Revert }
        'S' { Invoke-SetRes 0 0 }
        'D' { Write-Report }
        default { Write-Host "  nothing done." }
    }
}
catch {
    Write-Host ""
    Write-Host "  FAILED:" -ForegroundColor Red
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    Write-Host "  Nothing was written. If the game is running, close it and try again."
    Write-Host "  Run this again and press D to write a diagnostic report."
    exit 1
}
