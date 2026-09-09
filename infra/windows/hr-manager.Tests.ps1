# Pester-сценарии для движка локального Windows-пилота (phase 12).
#
# Запуск (PowerShell 5.1 или PowerShell 7, НЕ трогает реальную установку):
#   Invoke-Pester -Path .\infra\windows\hr-manager.Tests.ps1 -Output Detailed
#
# Движок dot-source'ится с -TestMode и временным StateDir; Main не выполняется
# (см. guard в hr-manager.ps1). Docker-вызовы детерминированно подменяются
# через сменный $script:CmdRunner, поэтому тесты не требуют Docker Desktop.

BeforeAll {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $enginePath = Join-Path $PSScriptRoot 'hr-manager.ps1'
    $testState = Join-Path ([System.IO.Path]::GetTempPath()) 'hr-manager-pester-state'
    if (Test-Path -LiteralPath $testState) { Remove-Item -LiteralPath $testState -Recurse -Force }
    . $enginePath -TestMode -StateDir $testState -PayloadDir $repoRoot
    Resolve-Paths
    # Чистый runner по умолчанию: вызовы внешних команд возвращают 0.
    $script:CmdRunner = { param($Exe, $Args) return 0 }
}

Describe 'Валидация формы установки' {
    It 'принимает корректные фамилии (кириллица, латиница, дефис, апостроф)' {
        Test-Surname 'Иванов' | Should -BeTrue
        Test-Surname 'Смирнов-Петров' | Should -BeTrue
        Test-Surname "O'Connor" | Should -BeTrue
    }
    It 'отклоняет пустую, слишком длинную и небезопасную фамилию' {
        Test-Surname '' | Should -BeFalse
        Test-Surname ('А' * 61) | Should -BeFalse
        Test-Surname 'Иванов; rm -rf /' | Should -BeFalse
        Test-Surname 'Иванов<script>' | Should -BeFalse
    }
    It 'принимает ровно три рабочие роли' {
        Test-WorkingMode 'hr' | Should -BeTrue
        Test-WorkingMode 'manager' | Should -BeTrue
        Test-WorkingMode 'admin' | Should -BeTrue
        Test-WorkingMode 'root' | Should -BeFalse
        Test-WorkingMode '' | Should -BeFalse
    }
}

Describe 'Генерация секретов' {
    It 'генерирует уникальные hex-значения нужной длины' {
        $a = Get-NewHex 16
        $b = Get-NewHex 16
        $a.Length | Should -Be 32
        $b.Length | Should -Be 32
        $a | Should -Not -Be $b
    }
    It 'генерирует URL-safe токен без символов, ломающих URL/env' {
        $t = Get-NewToken 32
        $t.Length | Should -Be 43   # 32 байта base64url без паддинга
        $t | Should -Match '^[A-Za-z0-9_-]+$'
    }
}

Describe 'Redact-Text (санитаризация)' {
    It 'скрывает пароли, ключи, токены, email, телефон и ФИО' {
        $raw = @(
            'POSTGRES_PASSWORD=supersecret123',
            'SECRET_KEY=deadbeefdeadbeef',
            'BACKUP_ENC_KEY=YWJjZGVmZ2hpamtsbW5vcA==',
            'FIRST_RUN_TOKEN=abcdef1234567890',
            'контакт: user@example.com',
            'тел: +7 912 345-67-89',
            'Ответственный: Иван Петров'
        ) -join [Environment]::NewLine
        $clean = Redact-Text $raw
        $clean | Should -Not -Match 'supersecret123'
        $clean | Should -Not -Match 'deadbeefdeadbeef'
        $clean | Should -Not -Match 'YWJjZGVmZ2hpamtsbW5vcA=='
        $clean | Should -Not -Match 'user@example.com'
        $clean | Should -Not -Match '\+7 912 345-67-89'
        $clean | Should -Not -Match 'Иван Петров'
        $clean | Should -Match '<REDACTED>'
    }
}

Describe 'Invoke-Cmd — сменный runner' {
    It 'возвращает код из mock-раннера без вызова реальной команды' {
        $script:CmdRunner = { param($Exe, $Args) return 42 }
        (Invoke-Cmd -Exe 'docker' -Args @('compose', 'version')) | Should -Be 42
    }
}

Describe 'Get-ComposeArgs — пилотный контур' {
    It 'собирает overlay, env file и стабильное имя проекта' {
        $args = Get-ComposeArgs @('up', '-d')
        ($args -join ' ') | Should -Match 'compose.pilot.yml'
        ($args -join ' ') | Should -Match 'docker-compose.yml'
        ($args -join ' ') | Should -Match 'hr-manager-pilot'
        ($args -join ' ') | Should -Match 'pilot.env'
    }
    It 'никогда не содержит секретов или фамилии в аргументах' {
        $args = Get-ComposeArgs @('up')
        ($args -join ' ') | Should -Not -Match 'POSTGRES_PASSWORD'
        ($args -join ' ') | Should -Not -Match 'SECRET_KEY'
    }
}

Describe 'Идемпотентность секретов' {
    It 'не ротирует секреты при повторном вызове Initialize-Secrets' {
        Initialize-Secrets
        $first = Get-Content -LiteralPath $script:EnvFile -Raw
        Initialize-Secrets
        $second = Get-Content -LiteralPath $script:EnvFile -Raw
        $first | Should -Be $second
    }
    It 'env file не содержит dev-секретов' {
        Initialize-Secrets
        $content = Get-Content -LiteralPath $script:EnvFile -Raw
        $content | Should -Not -Match 'dev'
        $content | Should -Not -Match 'changeme'
        $content | Should -Match 'POSTGRES_PASSWORD='
        $content | Should -Match 'SECRET_KEY='
        $content | Should -Match 'BACKUP_ENC_KEY='
    }
}

AfterAll {
    $script:CmdRunner = $null
    if (Test-Path -LiteralPath $testState) { Remove-Item -LiteralPath $testState -Recurse -Force }
}
