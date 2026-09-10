# Криптография канала обновлений (Phase 13) для Windows PowerShell 5.1+.
#
# Ed25519 (RFC 8032 §5.1.7) реализована на System.Numerics.BigInteger +
# SHA-512 средствами .NET Framework: клиент проверяет detached-подпись
# канонического manifest, не имея закрытого ключа. Совместимость с
# независимой реализацией Python cryptography подтверждена golden-тестами
# (fixture из infra/release/testdata) и эталонными векторами RFC 8032.
# Здесь нет «своей криптографии»: только открытая стандартная схема.

Set-StrictMode -Version 2.0

# --- Хеши --------------------------------------------------------------------

function Get-HrmSha256Bytes {
    param([byte[]]$Data)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return , $sha.ComputeHash($Data) } finally { $sha.Dispose() }
}

function Get-HrmSha256Hex {
    param([byte[]]$Data)
    # В переменную, а не напрямую в pipeline: результат функции — массив
    # одним объектом (защита `,` от разворачивания), pipeline не перечислит
    # его — $_ оказался бы byte[], и .ToString("x2") упал бы с MethodException.
    $bytes = Get-HrmSha256Bytes $Data
    return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Get-HrmSha512Bytes {
    param([byte[]]$Data)
    $sha = [System.Security.Cryptography.SHA512]::Create()
    try { return , $sha.ComputeHash($Data) } finally { $sha.Dispose() }
}

# --- Hex/base64 ----------------------------------------------------------------

function ConvertFrom-HrmHex {
    param([string]$Hex)
    if ($Hex.Length % 2 -ne 0) { throw "Нечётная длина hex-строки." }
    $bytes = New-Object byte[] ($Hex.Length / 2)
    for ($i = 0; $i -lt $bytes.Length; $i++) {
        $bytes[$i] = [Convert]::ToByte($Hex.Substring($i * 2, 2), 16)
    }
    return , $bytes
}

function ConvertTo-HrmBase64 {
    param([byte[]]$Bytes)
    return [Convert]::ToBase64String($Bytes)
}

function ConvertFrom-HrmBase64 {
    param([string]$Base64)
    try { return , [Convert]::FromBase64String($Base64) }
    catch { throw "Некорректный base64." }
}

# --- Ed25519 (RFC 8032 §5.1.7) -------------------------------------------------

$script:EdP = [System.Numerics.BigInteger]::Parse("7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed", "AllowHexSpecifier")
$script:EdL = [System.Numerics.BigInteger]::Parse("1000000000000000000000000000000014def9dea2f79cd65812631a5cf5d3ed", "AllowHexSpecifier")
$script:EdD = [System.Numerics.BigInteger]::Parse("52036cee2b6ffe738cc740797779e89800700a4d4141d8ab75eb4dca135978a3", "AllowHexSpecifier")
$script:EdI = [System.Numerics.BigInteger]::Parse("2b8324804fc1df0b2b4d00993dfbd7a72f431806ad2fe478c4ee1b274a0ea0b0", "AllowHexSpecifier")

function Invoke-HrmModPow {
    param($Base, $Exp, $Mod)
    return [System.Numerics.BigInteger]::ModPow($Base, $Exp, $Mod)
}

function Invoke-HrmEdAdd {
    # Сложение точек в extended-координатах (x, y, z, t).
    param($P, $Q)
    $A = [System.Numerics.BigInteger]::Remainder(($P[1] - $P[0]) * ($Q[1] - $Q[0]), $script:EdP)
    $B = [System.Numerics.BigInteger]::Remainder(($P[1] + $P[0]) * ($Q[1] + $Q[0]), $script:EdP)
    $C = [System.Numerics.BigInteger]::Remainder(2 * $P[3] * $Q[3] * $script:EdD, $script:EdP)
    $D = [System.Numerics.BigInteger]::Remainder(2 * $P[2] * $Q[2], $script:EdP)
    $E = [System.Numerics.BigInteger]::Remainder($B - $A, $script:EdP)
    $F = [System.Numerics.BigInteger]::Remainder($D - $C, $script:EdP)
    $G = [System.Numerics.BigInteger]::Remainder($D + $C, $script:EdP)
    $H = [System.Numerics.BigInteger]::Remainder($B + $A, $script:EdP)
    $x3 = [System.Numerics.BigInteger]::Remainder($E * $F, $script:EdP)
    $y3 = [System.Numerics.BigInteger]::Remainder($G * $H, $script:EdP)
    $t3 = [System.Numerics.BigInteger]::Remainder($E * $H, $script:EdP)
    $z3 = [System.Numerics.BigInteger]::Remainder($F * $G, $script:EdP)
    return , @($x3, $y3, $z3, $t3)
}

function Invoke-HrmEdDouble {
    # Удвоение через полную (complete) формулу сложения HWCD: для a=-1
    # закон сложения полон и не имеет исключительных точек. Отдельная
    # специализированная формула удвоения давала неверный результат
    # (поймано в CI через RFC-вектор и fixture-подпись; доказано
    # пошаговой эмуляцией арифметики).
    param($P)
    return Invoke-HrmEdAdd $P $P
}

function Invoke-HrmEdScalarMult {
    # [k]B через binary double-and-add от старших битов.
    # Биты извлекаются ТОЛЬКО статическими методами BigInteger
    # (op_RightShift/op_BitwiseAnd/IsOne): PS 5.1 показывала
    # значение-зависимое поведение для -shr/%/-eq на BigInteger
    # (одни скаляры считались верно, другие давали пустой результат).
    param($Scalar, $Point)
    $result = $null
    for ($bit = 255; $bit -ge 0; $bit--) {
        if ($null -ne $result) { $result = Invoke-HrmEdDouble $result }
        $shifted = [System.Numerics.BigInteger]::op_RightShift($Scalar, $bit)
        $masked = [System.Numerics.BigInteger]::op_BitwiseAnd($shifted, [System.Numerics.BigInteger]::One)
        if ($masked.IsOne) {
            $result = if ($null -eq $result) { , $Point } else { Invoke-HrmEdAdd $result $Point }
        }
    }
    if ($null -eq $result) {
        $debugBits = 0
        for ($b = 0; $b -lt 256; $b++) {
            $dbShift = [System.Numerics.BigInteger]::op_RightShift($Scalar, $b)
            if ([System.Numerics.BigInteger]::op_BitwiseAnd($dbShift, [System.Numerics.BigInteger]::One).IsOne) { $debugBits++ }
        }
        throw ("Пустой результат скалярного умножения (scalar=" + [string]$Scalar + "; bits=" + [string]$debugBits + ").")
    }
    return , $result
}

function ConvertTo-HrmEdPoint {
    # Декодирование 32-байтовой точки; отказ при неканоническом y >= p.
    param([byte[]]$Bytes)
    if ($Bytes.Length -ne 32) { throw "Точка Ed25519 должна быть 32 байта." }
    $sign = [bool]($Bytes[31] -band 0x80)
    $yBytes = New-Object byte[] 32
    [Array]::Copy($Bytes, $yBytes, 32)
    $yBytes[31] = $yBytes[31] -band 0x7F
    $yLittle = New-Object byte[] 32
    for ($i = 0; $i -lt 32; $i++) { $yLittle[$i] = $yBytes[31 - $i] }
    $yHex = (($yLittle | ForEach-Object { $_.ToString("x2") }) -join "")
    # "0"+hex: BigInteger.Parse с AllowHexSpecifier трактует hex со старшим
    # байтом >= 0x80 как отрицательное двух-дополнительное число (.NET).
    $y = [System.Numerics.BigInteger]::Parse("0" + $yHex, "AllowHexSpecifier")
    if ([System.Numerics.BigInteger]::Compare($y, $script:EdP) -ge 0) { throw "Некaноническая точка (y >= p)." }
    # x^2 = (y^2 - 1) / (d*y^2 + 1)
    $y2 = [System.Numerics.BigInteger]::Remainder($y * $y, $script:EdP)
    $num = [System.Numerics.BigInteger]::Remainder($y2 - 1, $script:EdP)
    $den = [System.Numerics.BigInteger]::Remainder($script:EdD * $y2 + 1, $script:EdP)
    $denInv = Invoke-HrmModPow $den ($script:EdP - 2) $script:EdP
    $x2 = [System.Numerics.BigInteger]::Remainder($num * $denInv, $script:EdP)
    $x = Invoke-HrmModPow $x2 (($script:EdP + 3) / 8) $script:EdP
    if (([System.Numerics.BigInteger]::Remainder($x * $x - $x2, $script:EdP)).IsZero -eq $false) {
        $x = [System.Numerics.BigInteger]::Remainder($x * $script:EdI, $script:EdP)
    }
    if (([System.Numerics.BigInteger]::Remainder($x * $x - $x2, $script:EdP)).IsZero -eq $false) {
        throw "Точка не лежит на кривой."
    }
    # Чётность x — статическим API: PS 5.1 `%`/-eq на BigInteger
    # давал значение-зависимые результаты.
    $xOdd = [System.Numerics.BigInteger]::op_BitwiseAnd($x, [System.Numerics.BigInteger]::One).IsOne
    if ($xOdd -ne $sign) {
        $x = [System.Numerics.BigInteger]::Remainder($script:EdP - $x, $script:EdP)
    }
    if ($x.IsZero -and $sign) { throw "Нулевая точка с установленным битом знака." }
    # extended: x, y, z=1, t=x*y
    return , @($x, $y, [System.Numerics.BigInteger]::One, [System.Numerics.BigInteger]::Remainder($x * $y, $script:EdP))
}

function ConvertFrom-HrmLittleEndian {
    param([byte[]]$Bytes)
    $reversed = New-Object byte[] $Bytes.Length
    for ($i = 0; $i -lt $Bytes.Length; $i++) { $reversed[$i] = $Bytes[$Bytes.Length - 1 - $i] }
    $hex = (($reversed | ForEach-Object { $_.ToString("x2") }) -join "")
    if (-not $hex) { return [System.Numerics.BigInteger]::Zero }
    # "0"+hex: Parse с AllowHexSpecifier трактует hex со старшим байтом
    # >= 0x80 как отрицательное двух-дополнительное число (.NET) — сдвиг
    # битов/сравнения на отрицательных скалярах дают неверный результат.
    return [System.Numerics.BigInteger]::Parse("0" + $hex, "AllowHexSpecifier")
}

function Get-HrmPositiveRemainder {
    # Remainder в [0, mod): .NET BigInteger.Remainder сохраняет знак
    # делимого, а сравнение представителей требует канонического вида.
    param($Value, $Mod)
    $r = [System.Numerics.BigInteger]::Remainder($Value, $Mod)
    if ([System.Numerics.BigInteger]::Compare($r, [System.Numerics.BigInteger]::Zero) -lt 0) {
        return [System.Numerics.BigInteger]::Add($r, $Mod)
    }
    return $r
}

function Get-HrmEdBasePoint {
    # Генератор B (RFC 8032): y = 4/5, x > 0 чётный.
    $y = [System.Numerics.BigInteger]::Parse("6666666666666666666666666666666666666666666666666666666666666658", "AllowHexSpecifier")
    $x = [System.Numerics.BigInteger]::Parse("216936d3cd6e53fec0a4e231fdd6dc5c692cc7609525a7b2c9562d608f25d51a", "AllowHexSpecifier")
    return , @($x, $y, [System.Numerics.BigInteger]::One, [System.Numerics.BigInteger]::Remainder($x * $y, $script:EdP))
}

function Test-HrmEd25519Signature {
    # Проверка detached Ed25519-подписи (RFC 8032 §5.1.7).
    # $Message без Mandatory: пустое сообщение — валидный вход (RFC 8032
    # §7.1 TEST 1), а PS не привязывает пустой массив к Mandatory-параметру.
    param(
        [Parameter(Mandatory = $true)][string]$PublicKeyBase64,
        [byte[]]$Message = @(),
        [Parameter(Mandatory = $true)][string]$SignatureHex
    )
    if ($SignatureHex.Length -ne 128 -or $SignatureHex -notmatch "^[0-9a-f]+$") {
        throw "Подпись должна быть 128 hex-символами."
    }
    $publicBytes = ConvertFrom-HrmBase64 $PublicKeyBase64
    if ($publicBytes.Length -ne 32) { throw "Публичный ключ должен быть 32 байта." }
    $sigBytes = ConvertFrom-HrmHex $SignatureHex
    $rBytes = New-Object byte[] 32
    $sBytes = New-Object byte[] 32
    [Array]::Copy($sigBytes, 0, $rBytes, 0, 32)
    [Array]::Copy($sigBytes, 32, $sBytes, 0, 32)
    $s = ConvertFrom-HrmLittleEndian $sBytes
    if ([System.Numerics.BigInteger]::Compare($s, $script:EdL) -ge 0) { throw "Некaнонический S (>= L)." }
    try {
        $pointA = ConvertTo-HrmEdPoint $publicBytes
    }
    catch {
        throw "Некорректный публичный ключ (точка не на кривой или неканоническая)."
    }
    # k = SHA-512(R || A || M) mod L
    $hashInput = New-Object byte[] (32 + 32 + $Message.Length)
    [Array]::Copy($rBytes, 0, $hashInput, 0, 32)
    [Array]::Copy($publicBytes, 0, $hashInput, 32, 32)
    [Array]::Copy($Message, 0, $hashInput, 64, $Message.Length)
    $hBytes = Get-HrmSha512Bytes $hashInput
    $h = [System.Numerics.BigInteger]::Remainder((ConvertFrom-HrmLittleEndian $hBytes), $script:EdL)

    # [S]B против R + [h]A (сравнение в extended-координатах по x/z, y/z).
    $basePoint = Get-HrmEdBasePoint
    $sB = Invoke-HrmEdScalarMult $s $basePoint
    $rPoint = ConvertTo-HrmEdPoint $rBytes
    $hA = Invoke-HrmEdScalarMult $h $pointA
    $rhs = Invoke-HrmEdAdd $rPoint $hA
    $lhsX = Get-HrmPositiveRemainder ($sB[0] * $rhs[2]) $script:EdP
    $rhsX = Get-HrmPositiveRemainder ($rhs[0] * $sB[2]) $script:EdP
    $lhsY = Get-HrmPositiveRemainder ($sB[1] * $rhs[2]) $script:EdP
    $rhsY = Get-HrmPositiveRemainder ($rhs[1] * $sB[2]) $script:EdP
    if (-not ([System.Numerics.BigInteger]::Equals($lhsX, $rhsX) -and [System.Numerics.BigInteger]::Equals($lhsY, $rhsY))) {
        # Диагностика в тексте исключения: аннотации CI — единственный
        # читаемый канал на GitHub-hosted Windows runner в этой инфраструктуре.
        throw ("Подпись не совпала [diag: s=" + [string]$s + "; h=" + [string]$h +
            "; sB=" + [string]$sB[0] + "," + [string]$sB[1] + "," + [string]$sB[2] + "," + [string]$sB[3] +
            "; rhs=" + [string]$rhs[0] + "," + [string]$rhs[1] + "," + [string]$rhs[2] + "," + [string]$rhs[3] + "]")
    }
    return $true
}

# --- SemVer --------------------------------------------------------------------

function Compare-HrmSemVer {
    # Строгое SemVer-сравнение: -1 (left<right), 0, 1. Build metadata не
    # учитывается. Некорректные версии — исключение. Таблица общих случаев —
    # infra/release/testdata/semver_cases.json (единая для Python и PS).
    param([string]$Left, [string]$Right)
    $pattern = "^\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$"
    if ($Left -notmatch $pattern) { throw "Некорректный SemVer: $Left" }
    if ($Right -notmatch $pattern) { throw "Некорректный SemVer: $Right" }
    $lCore = ($Left -split "\+")[0]
    $rCore = ($Right -split "\+")[0]
    $lMain = ($lCore -split "-")[0]
    $rMain = ($rCore -split "-")[0]
    $lParts = @($lMain -split "\." | ForEach-Object { [int]$_ })
    $rParts = @($rMain -split "\." | ForEach-Object { [int]$_ })
    for ($i = 0; $i -lt 3; $i++) {
        if ($lParts[$i] -lt $rParts[$i]) { return -1 }
        if ($lParts[$i] -gt $rParts[$i]) { return 1 }
    }
    $lPre = @()
    if ($lCore -match "-") { $lPre = @(($lCore -split "-", 2)[1] -split "\.") }
    $rPre = @()
    if ($rCore -match "-") { $rPre = @(($rCore -split "-", 2)[1] -split "\.") }
    if ($lPre.Count -eq 0 -and $rPre.Count -eq 0) { return 0 }
    if ($lPre.Count -eq 0) { return 1 }
    if ($rPre.Count -eq 0) { return -1 }
    $count = [Math]::Min($lPre.Count, $rPre.Count)
    for ($i = 0; $i -lt $count; $i++) {
        if ($lPre[$i] -eq $rPre[$i]) { continue }
        $lNum = $lPre[$i] -match "^\d+$" -and -not ($lPre[$i].Length -gt 1 -and $lPre[$i].StartsWith("0"))
        $rNum = $rPre[$i] -match "^\d+$" -and -not ($rPre[$i].Length -gt 1 -and $rPre[$i].StartsWith("0"))
        if ($lNum -and $rNum) {
            $li = [long]$lPre[$i]; $ri = [long]$rPre[$i]
            if ($li -lt $ri) { return -1 } else { return 1 }
        }
        if ($lNum -ne $rNum) { if ($lNum) { return -1 } else { return 1 } }
        $cmp = [string]::CompareOrdinal($lPre[$i], $rPre[$i])
        if ($cmp -lt 0) { return -1 } else { return 1 }
    }
    if ($lPre.Count -eq $rPre.Count) { return 0 }
    if ($lPre.Count -lt $rPre.Count) { return -1 } else { return 1 }
}

# --- Канонизация manifest --------------------------------------------------------

$script:ManifestFields = @(
    @("schema_version", "int"), @("channel", "str"), @("version", "str"),
    @("release_sha", "str"), @("package_url", "str"), @("package_size", "int"),
    @("package_sha256", "str"), @("minimum_supported_version", "str"),
    @("published_at", "str"), @("notes_ru", "str")
)

function Read-HrmManifestJson {
    # Строгий разбор JSON manifest: только известные поля + signature;
    # повторяющиеся ключи и неизвестные поля — отказ (fail closed).
    param([string]$Text)
    $text = $Text.Trim()
    if ($text.StartsWith("{") -and $text.EndsWith("}")) {
        $text = $text.Substring(1, $text.Length - 2)
    }
    else {
        throw "Manifest должен быть JSON-объектом."
    }
    $result = [ordered]@{}
    $position = 0
    while ($position -lt $text.Length) {
        while ($position -lt $text.Length -and $text[$position] -match "\s") { $position++ }
        if ($position -ge $text.Length) { break }
        if ($text[$position] -eq ",") { $position++; continue }
        if ($text[$position] -ne '"') { throw "Ожидался ключ JSON на позиции $position." }
        $keyEnd = $text.IndexOf('"', $position + 1)
        if ($keyEnd -lt 0) { throw "Незакрытый ключ JSON." }
        $key = $text.Substring($position + 1, $keyEnd - $position - 1)
        $position = $keyEnd + 1
        while ($position -lt $text.Length -and $text[$position] -match "\s") { $position++ }
        if ($text[$position] -ne ":") { throw "Ожидалось ':' после ключа $key." }
        $position++
        while ($position -lt $text.Length -and $text[$position] -match "\s") { $position++ }
        $valueStart = $position
        $value = ""
        if ($text[$position] -eq '"') {
            # строка (без экранирования, кроме \" и \\ — поля контракта простые)
            $position++
            $chars = New-Object System.Text.StringBuilder
            while ($position -lt $text.Length -and $text[$position] -ne '"') {
                if ($text[$position] -eq "\") {
                    $position++
                    if ($position -ge $text.Length) { throw "Незакрытая строка JSON." }
                    $next = $text[$position]
                    if ($next -eq "n") { [void]$chars.Append("`n") }
                    elseif ($next -eq "t") { [void]$chars.Append("`t") }
                    elseif ($next -eq "r") { [void]$chars.Append("`r") }
                    elseif ($next -eq "u") {
                        $hex = $text.Substring($position + 1, 4)
                        [void]$chars.Append([char][Convert]::ToInt32($hex, 16))
                        $position += 4
                    }
                    else { [void]$chars.Append($next) }
                    $position++
                    continue
                }
                [void]$chars.Append($text[$position])
                $position++
            }
            if ($position -ge $text.Length) { throw "Незакрытая строка JSON." }
            $value = $chars.ToString()
            $position++
        }
        elseif ($text[$position] -eq "{") {
            $depth = 0
            $scan = $position
            while ($scan -lt $text.Length) {
                $ch = $text[$scan]
                if ($ch -eq "{") { $depth++ }
                elseif ($ch -eq "}") { $depth--; if ($depth -eq 0) { break } }
                $scan++
            }
            if ($depth -ne 0) { throw "Незакрытый вложенный объект JSON." }
            $value = $text.Substring($position, $scan - $position + 1)
            $position = $scan + 1
        }
        elseif ($text[$position] -match "[0-9-]") {
            $scan = $position
            while ($scan -lt $text.Length -and $text[$scan] -match "[0-9]") { $scan++ }
            $value = $text.Substring($position, $scan - $position)
            $position = $scan
        }
        else {
            throw "Неподдерживаемое значение JSON на позиции $position."
        }
        if ($result.Contains($key)) { throw "Повторяющийся ключ JSON: $key" }
        $result[$key] = $value
    }
    return $result
}

function Get-HrmCanonicalBytes {
    # Канонический payload: фиксированный порядок полей, "имя:значение"
    # через LF, UTF-8, завершающий перевод строки. Зеркало Python-контракта.
    # Параметр без типа: OrderedDictionary не приводится к Hashtable.
    param($Manifest)
    $allowed = @{}
    foreach ($field in $script:ManifestFields) { $allowed[$field[0]] = $field[1] }
    $allowed["signature"] = "obj"
    foreach ($key in $Manifest.Keys) {
        if (-not $allowed.ContainsKey($key)) { throw "Неизвестное поле manifest: $key" }
    }
    $builder = New-Object System.Text.StringBuilder
    foreach ($field in $script:ManifestFields) {
        $name = $field[0]
        if (-not $Manifest.Contains($name)) { throw "Отсутствует обязательное поле: $name" }
        $value = [string]$Manifest[$name]
        # @(...): в StrictMode 2.0 у скалярного результата pipeline нет .Count.
        $controls = @($value.ToCharArray() | Where-Object { [int]$_ -lt 32 })
        if ($value -match "[\r\n]" -or $controls.Count -gt 0) {
            throw "Поле $name содержит управляющие символы."
        }
        if ($field[1] -eq "int") {
            if ($value -notmatch "^-?\d+$") { throw "Поле $name должно быть целым числом." }
        }
        [void]$builder.Append(($name + ":" + $value + "`n"))
    }
    $payload = $builder.ToString()
    return , [System.Text.Encoding]::UTF8.GetBytes($payload)
}

function Test-HrmChannelManifest {
    # Полная клиентская проверка manifest: структура, версия схемы, SemVer,
    # URL/размер, подпись доверенным (не отозванным) ключом из набора.
    # Возвращает проверенный ordered-манифест (с signature) или бросает
    # исключение с безопасным кодом в $_.Exception.Data["Code"].
    param($Manifest, [hashtable]$TrustedKeys)
    function New-HrmChannelError {
        # Имя $channelError, а не $error: $Error — автопеременная только для чтения.
        param([string]$Code, [string]$Message)
        $channelError = New-Object System.Exception ($Message)
        $channelError.Data["Code"] = $Code
        throw $channelError
    }
    foreach ($field in $script:ManifestFields) {
        if (-not $Manifest.Contains($field[0])) {
            New-HrmChannelError "manifest_invalid" ("Отсутствует поле: " + $field[0])
        }
    }
    if ([string]$Manifest["schema_version"] -ne "1") {
        New-HrmChannelError "manifest_invalid" ("Неизвестная версия схемы: " + $Manifest["schema_version"])
    }
    if ([string]$Manifest["channel"] -ne "stable") {
        New-HrmChannelError "manifest_invalid" ("Неизвестный канал: " + $Manifest["channel"])
    }
    try {
        $null = Compare-HrmSemVer ([string]$Manifest["version"]) "0.0.0"
        $null = Compare-HrmSemVer ([string]$Manifest["minimum_supported_version"]) "0.0.0"
    }
    catch {
        New-HrmChannelError "manifest_invalid" ("Некорректный SemVer: " + $_.Exception.Message)
    }
    if ([string]$Manifest["release_sha"] -notmatch "^[0-9a-f]{40}$") {
        New-HrmChannelError "manifest_invalid" "release_sha должен быть 40 hex."
    }
    if ([string]$Manifest["package_sha256"] -notmatch "^[0-9a-f]{64}$") {
        New-HrmChannelError "manifest_invalid" "package_sha256 должен быть 64 hex."
    }
    $url = [string]$Manifest["package_url"]
    if (-not $url.StartsWith("https://")) { New-HrmChannelError "manifest_invalid" "package_url обязан использовать https." }
    if ($url -match "[\?#@]") { New-HrmChannelError "manifest_invalid" "package_url не должен содержать query/fragment/userinfo." }
    $size = [string]$Manifest["package_size"]
    if ($size -notmatch "^\d+$" -or [long]$size -le 0) {
        New-HrmChannelError "manifest_invalid" "package_size должен быть положительным целым."
    }
    if (-not $Manifest.Contains("signature")) {
        New-HrmChannelError "bad_signature" "Отсутствует signature."
    }
    $signature = $Manifest["signature"]
    $sigText = [string]$signature
    # [regex]::Match вместо -match: $Matches от -notmatch не обновляется.
    $schemeMatch = [regex]::Match($sigText, '"scheme"\s*:\s*"ed25519"')
    if (-not $schemeMatch.Success) {
        New-HrmChannelError "bad_signature" "Неподдерживаемая схема подписи."
    }
    $keyMatch = [regex]::Match($sigText, '"key_id"\s*:\s*"([^"]+)"')
    if (-not $keyMatch.Success) {
        New-HrmChannelError "bad_signature" "signature.key_id обязателен."
    }
    $keyId = $keyMatch.Groups[1].Value
    $sigMatch = [regex]::Match($sigText, '"sig"\s*:\s*"([0-9a-f]{128})"')
    if (-not $sigMatch.Success) {
        New-HrmChannelError "bad_signature" "signature.sig должен быть 128 hex."
    }
    $sigHex = $sigMatch.Groups[1].Value
    if (-not $TrustedKeys.Contains($keyId)) {
        New-HrmChannelError "unknown_key" ("Ключ не входит в доверенный набор: " + $keyId)
    }
    $entry = $TrustedKeys[$keyId]
    if ($entry.revoked) {
        New-HrmChannelError "revoked_key" ("Ключ отозван: " + $keyId)
    }
    $payload = Get-HrmCanonicalBytes $Manifest
    $ok = $false
    try {
        $ok = Test-HrmEd25519Signature -PublicKeyBase64 ([string]$entry.key) -Message $payload -SignatureHex $sigHex
    }
    catch {
        New-HrmChannelError "bad_signature" ("Проверка подписи не удалась: " + $_.Exception.Message)
    }
    if (-not $ok) {
        New-HrmChannelError "bad_signature" "Подпись недействительна."
    }
    $manifestCopy = [ordered]@{}
    foreach ($field in $script:ManifestFields) { $manifestCopy[$field[0]] = [string]$Manifest[$field[0]] }
    $manifestCopy["signature"] = $signature
    $manifestCopy["key_id"] = $keyId
    $manifestCopy["signature_hex"] = $sigHex
    return $manifestCopy
}
