# Start backend in a new window, frontend in this one.
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  Write-Error "backend\.venv not found. Run: cd backend; python -m venv .venv; pip install -e ."
  exit 1
}
Start-Process -FilePath $py `
  -ArgumentList "-m","uvicorn","clipsmith.api:app","--port","8000","--reload" `
  -WorkingDirectory (Join-Path $root "backend")
Set-Location (Join-Path $root "frontend")
npm run dev
