Describe "run-sleep.ps1 runner" {
    BeforeAll {
        $script = Join-Path $PSScriptRoot "..\plugins\run-sleep.ps1"
        $tempDir = [System.IO.Path]::GetTempFileName()
        Remove-Item $tempDir
        New-Item -ItemType Directory -Path $tempDir | Out-Null

        # Detect a working python to bypass Windows Store alias issues
        if (-not $env:SKILLOPT_SLEEP_PYTHON) {
            if (Test-Path "C:\Python314\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python314\python.exe"
            } elseif (Test-Path "C:\Python313\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python313\python.exe"
            } elseif (Test-Path "C:\Python312\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python312\python.exe"
            } elseif (Test-Path "C:\Python311\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python311\python.exe"
            } elseif (Test-Path "C:\Python310\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python310\python.exe"
            }
        }
    }

    AfterAll {
        if (Test-Path $tempDir) {
            Remove-Item -Recurse -Force $tempDir
        }
    }

    It "runs successfully in source checkout mode" {
        $env:SKILLOPT_SLEEP_REPO = Resolve-Path (Join-Path $PSScriptRoot "..")
        # Run help
        $result = powershell -File $script "--help" 2>&1
        $LASTEXITCODE | Should Be 0
        $result | Out-String | Should Match "skillopt_sleep"
    }

    It "falls back to CLI on PATH" {
        # Create a temp dir for isolated testing
        $sandbox = Join-Path $tempDir "cli_fallback"
        New-Item -ItemType Directory -Path $sandbox | Out-Null
        $scriptCopy = Join-Path $sandbox "run-sleep.ps1"
        Copy-Item $script $scriptCopy

        # Create fake CLI
        $binDir = Join-Path $sandbox "bin"
        New-Item -ItemType Directory -Path $binDir | Out-Null
        $fakeCli = Join-Path $binDir "skillopt-sleep.cmd"
        "@echo off`r`necho fake-cli invoked %*`r`nexit /b 0" | Out-File -FilePath $fakeCli -Encoding ascii

        # Save existing env vars
        $oldPath = $env:PATH
        $oldRepo = $env:SKILLOPT_SLEEP_REPO
        $oldPlugin = $env:CLAUDE_PLUGIN_ROOT

        $env:PATH = "$binDir;$oldPath"
        $env:SKILLOPT_SLEEP_REPO = $null
        $env:CLAUDE_PLUGIN_ROOT = $null

        $oldLocation = Get-Location
        Set-Location $sandbox
        try {
            $result = powershell -File $scriptCopy "status" 2>&1
            $LASTEXITCODE | Should Be 0
            $result | Out-String | Should Match "fake-cli invoked status"
        }
        finally {
            Set-Location $oldLocation
            $env:PATH = $oldPath
            $env:SKILLOPT_SLEEP_REPO = $oldRepo
            $env:CLAUDE_PLUGIN_ROOT = $oldPlugin
        }
    }

    It "propagates exit code on failure" {
        $env:SKILLOPT_SLEEP_REPO = Resolve-Path (Join-Path $PSScriptRoot "..")
        # Run a non-existent command to trigger python module failure
        $result = powershell -File $script "non-existent-subcommand" 2>&1
        $LASTEXITCODE | Should Not Be 0
    }

    It "handles paths containing spaces correctly" {
        # Create path containing spaces
        $spaceDir = Join-Path $tempDir "path with spaces"
        New-Item -ItemType Directory -Path $spaceDir | Out-Null
        $scriptCopy = Join-Path $spaceDir "run-sleep.ps1"
        Copy-Item $script $scriptCopy

        # Create fake CLI
        $binDir = Join-Path $spaceDir "bin"
        New-Item -ItemType Directory -Path $binDir | Out-Null
        $fakeCli = Join-Path $binDir "skillopt-sleep.cmd"
        "@echo off`r`necho fake-cli invoked %*`r`nexit /b 0" | Out-File -FilePath $fakeCli -Encoding ascii

        # Save env
        $oldPath = $env:PATH
        $oldRepo = $env:SKILLOPT_SLEEP_REPO
        $oldPlugin = $env:CLAUDE_PLUGIN_ROOT

        $env:PATH = "$binDir;$oldPath"
        $env:SKILLOPT_SLEEP_REPO = $null
        $env:CLAUDE_PLUGIN_ROOT = $null

        $oldLocation = Get-Location
        Set-Location $spaceDir
        try {
            $result = powershell -File $scriptCopy "status" 2>&1
            $LASTEXITCODE | Should Be 0
            $result | Out-String | Should Match "fake-cli invoked status"
        }
        finally {
            Set-Location $oldLocation
            $env:PATH = $oldPath
            $env:SKILLOPT_SLEEP_REPO = $oldRepo
            $env:CLAUDE_PLUGIN_ROOT = $oldPlugin
        }
    }
}

Describe "run-sleep.cmd runner" {
    BeforeAll {
        $script = Join-Path $PSScriptRoot "..\plugins\run-sleep.cmd"
        $tempDir = [System.IO.Path]::GetTempFileName()
        Remove-Item $tempDir
        New-Item -ItemType Directory -Path $tempDir | Out-Null

        # Detect a working python to bypass Windows Store alias issues
        if (-not $env:SKILLOPT_SLEEP_PYTHON) {
            if (Test-Path "C:\Python314\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python314\python.exe"
            } elseif (Test-Path "C:\Python313\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python313\python.exe"
            } elseif (Test-Path "C:\Python312\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python312\python.exe"
            } elseif (Test-Path "C:\Python311\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python311\python.exe"
            } elseif (Test-Path "C:\Python310\python.exe") {
                $env:SKILLOPT_SLEEP_PYTHON = "C:\Python310\python.exe"
            }
        }
    }

    AfterAll {
        if (Test-Path $tempDir) {
            Remove-Item -Recurse -Force $tempDir
        }
    }

    It "runs successfully in source checkout mode" {
        $env:SKILLOPT_SLEEP_REPO = Resolve-Path (Join-Path $PSScriptRoot "..")
        # Run help
        $result = cmd.exe /c $script "--help" 2>&1
        $LASTEXITCODE | Should Be 0
        $result | Out-String | Should Match "skillopt_sleep"
    }

    It "falls back to CLI on PATH" {
        # Create a temp dir for isolated testing
        $sandbox = Join-Path $tempDir "cmd_cli_fallback"
        New-Item -ItemType Directory -Path $sandbox | Out-Null
        $scriptCopy = Join-Path $sandbox "run-sleep.cmd"
        Copy-Item $script $scriptCopy

        # Create fake CLI
        $binDir = Join-Path $sandbox "bin"
        New-Item -ItemType Directory -Path $binDir | Out-Null
        $fakeCli = Join-Path $binDir "skillopt-sleep.cmd"
        "@echo off`r`necho fake-cli invoked %*`r`nexit /b 0" | Out-File -FilePath $fakeCli -Encoding ascii

        # Save existing env vars
        $oldPath = $env:PATH
        $oldRepo = $env:SKILLOPT_SLEEP_REPO
        $oldPlugin = $env:CLAUDE_PLUGIN_ROOT

        $env:PATH = "$binDir;$oldPath"
        $env:SKILLOPT_SLEEP_REPO = $null
        $env:CLAUDE_PLUGIN_ROOT = $null

        $oldLocation = Get-Location
        Set-Location $sandbox
        try {
            $result = cmd.exe /c $scriptCopy "status" 2>&1
            $LASTEXITCODE | Should Be 0
            $result | Out-String | Should Match "fake-cli invoked status"
        }
        finally {
            Set-Location $oldLocation
            $env:PATH = $oldPath
            $env:SKILLOPT_SLEEP_REPO = $oldRepo
            $env:CLAUDE_PLUGIN_ROOT = $oldPlugin
        }
    }

    It "falls back to importable module" {
        $sandbox = Join-Path $tempDir "cmd_module_fallback"
        New-Item -ItemType Directory -Path $sandbox | Out-Null
        $scriptCopy = Join-Path $sandbox "run-sleep.cmd"
        Copy-Item $script $scriptCopy

        $oldRepo = $env:SKILLOPT_SLEEP_REPO
        $oldPlugin = $env:CLAUDE_PLUGIN_ROOT

        $env:SKILLOPT_SLEEP_REPO = $null
        $env:CLAUDE_PLUGIN_ROOT = $null

        $oldPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = Resolve-Path (Join-Path $PSScriptRoot "..")

        $oldLocation = Get-Location
        Set-Location $sandbox
        try {
            $result = cmd.exe /c $scriptCopy "--help" 2>&1
            $LASTEXITCODE | Should Be 0
            $result | Out-String | Should Match "skillopt_sleep"
        }
        finally {
            Set-Location $oldLocation
            $env:SKILLOPT_SLEEP_REPO = $oldRepo
            $env:CLAUDE_PLUGIN_ROOT = $oldPlugin
            $env:PYTHONPATH = $oldPythonPath
        }
    }

    It "fails when nothing is found" {
        $sandbox = Join-Path $tempDir "cmd_failure"
        New-Item -ItemType Directory -Path $sandbox | Out-Null
        $scriptCopy = Join-Path $sandbox "run-sleep.cmd"
        Copy-Item $script $scriptCopy

        $oldRepo = $env:SKILLOPT_SLEEP_REPO
        $oldPlugin = $env:CLAUDE_PLUGIN_ROOT
        $oldPath = $env:PATH

        $env:SKILLOPT_SLEEP_REPO = $null
        $env:CLAUDE_PLUGIN_ROOT = $null

        $emptyBin = Join-Path $sandbox "empty_bin"
        New-Item -ItemType Directory -Path $emptyBin | Out-Null
        $env:PATH = $emptyBin

        $oldLocation = Get-Location
        Set-Location $sandbox
        try {
            $result = cmd.exe /c $scriptCopy "status" 2>&1
            $LASTEXITCODE | Should Not Be 0
            $result | Out-String | Should Match "could not locate the skillopt_sleep package"
        }
        finally {
            Set-Location $oldLocation
            $env:SKILLOPT_SLEEP_REPO = $oldRepo
            $env:CLAUDE_PLUGIN_ROOT = $oldPlugin
            $env:PATH = $oldPath
        }
    }

    It "propagates exit code on failure" {
        $env:SKILLOPT_SLEEP_REPO = Resolve-Path (Join-Path $PSScriptRoot "..")
        $result = cmd.exe /c $script "non-existent-subcommand" 2>&1
        $LASTEXITCODE | Should Not Be 0
    }

    It "handles paths containing spaces correctly" {
        $spaceDir = Join-Path $tempDir "cmd path with spaces"
        New-Item -ItemType Directory -Path $spaceDir | Out-Null
        $scriptCopy = Join-Path $spaceDir "run-sleep.cmd"
        Copy-Item $script $scriptCopy

        $binDir = Join-Path $spaceDir "bin"
        New-Item -ItemType Directory -Path $binDir | Out-Null
        $fakeCli = Join-Path $binDir "skillopt-sleep.cmd"
        "@echo off`r`necho fake-cli invoked %*`r`nexit /b 0" | Out-File -FilePath $fakeCli -Encoding ascii

        $oldPath = $env:PATH
        $oldRepo = $env:SKILLOPT_SLEEP_REPO
        $oldPlugin = $env:CLAUDE_PLUGIN_ROOT

        $env:PATH = "$binDir;$oldPath"
        $env:SKILLOPT_SLEEP_REPO = $null
        $env:CLAUDE_PLUGIN_ROOT = $null

        $oldLocation = Get-Location
        Set-Location $spaceDir
        try {
            $result = cmd.exe /c $scriptCopy "status" 2>&1
            $LASTEXITCODE | Should Be 0
            $result | Out-String | Should Match "fake-cli invoked status"
        }
        finally {
            Set-Location $oldLocation
            $env:PATH = $oldPath
            $env:SKILLOPT_SLEEP_REPO = $oldRepo
            $env:CLAUDE_PLUGIN_ROOT = $oldPlugin
        }
    }
}
