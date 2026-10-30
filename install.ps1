$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$root;$env:PYTHONPATH"
python -u "$root\agents.py" install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -u "$root\agents.py" doctor
exit $LASTEXITCODE
