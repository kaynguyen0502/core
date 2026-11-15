# SmartLab – cài đặt trên Windows (không dùng Docker)
#
# Cách chạy: mở PowerShell bằng quyền Administrator, vào thư mục chứa file này rồi chạy
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   .\install_windows.ps1                      # tự tìm cổng COM của USB Zigbee
#   .\install_windows.ps1 -ZigbeePort COM4     # hoặc chỉ định cổng
#   .\install_windows.ps1 -ZigbeeAdapter ember # ZBDongle-E ("V2"); ZBDongle-P là zstack (mặc định: tự nhận)
#   .\install_windows.ps1 -MqttPort 1884       # máy đã có MQTT của dự án khác ở cổng 1883: chạy song song
#
# Chạy lại nhiều lần được: mật khẩu đã tạo được giữ nguyên, dịch vụ được cài lại.
# Cài: Python, Node.js, Git, Mosquitto, NSSM (qua winget nếu thiếu), Zigbee2MQTT, phần mềm SmartLab.
# Dùng MySQL đã có trên máy (MySQL của Reagent Manager), tạo schema riêng "smartlab".
# Máy thử nghiệm không có MySQL: dùng SQLite (tự hỏi, hoặc chạy với -UseSQLite).

param(
    [string]$InstallDir = "C:\SmartLab",
    [string]$ZigbeePort = "",
    [string]$MySqlBin = "",
    [string]$MySqlRootUser = "root",
    [switch]$UseSQLite,
    [ValidateSet("auto", "zstack", "ember")]
    [string]$ZigbeeAdapter = "auto",
    [int]$MqttPort = 1883,
    [int]$WebPort = 8000,
    [int]$Z2mPort = 8080
)

# "Continue": lệnh ngoài (nssm, py...) ghi ra stderr không làm dừng script; lỗi kiểm tra bằng mã thoát
$ErrorActionPreference = "Continue"
$env:COREPACK_ENABLE_DOWNLOAD_PROMPT = "0"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" }
function Fail($msg) { Write-Host "`nLỖI: $msg" -ForegroundColor Red; exit 1 }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}
function Write-Utf8NoBom($path, $text) {
    [IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))
}
function New-Password {
    -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
}
function Run($exe, [string[]]$argv) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { Fail "Không tìm thấy lệnh '$exe'." }
    $global:LASTEXITCODE = 0
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { Fail "Lệnh thất bại: $exe $($argv -join ' ')" }
}

# ---------------------------------------------------------------- 0. Kiểm tra
Step "Kiểm tra quyền Administrator"
$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail "Hãy mở PowerShell bằng 'Run as administrator' rồi chạy lại."
}
$Src = Split-Path $PSScriptRoot -Parent          # thư mục smartlab/platform trong repo
if (-not (Test-Path "$Src\smartlab\web.py")) { Fail "Không thấy mã nguồn SmartLab cạnh file cài đặt ($Src)." }

# Không cài đè vào thư mục đang có chương trình / dữ liệu khác (vd dự án SmartLab cũ ở C:\SmartLab)
$Marker = "$InstallDir\.smartlab-platform"
if ((Test-Path $InstallDir) -and -not (Test-Path $Marker)) {
    $foreign = Get-ChildItem $InstallDir -Force -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -notin @("app", "venv", "logs", "mosquitto", "zigbee2mqtt", "data") }
    if ($foreign -or (Test-Path "$InstallDir\data\smartlab.db")) {
        Info "Thư mục $InstallDir đã có file của chương trình khác:"
        $foreign | Select-Object -First 8 | ForEach-Object { Info "  - $($_.Name)" }
        Fail "Không cài đè. Chạy lại với thư mục riêng, vd: .\install_windows.ps1 -InstallDir C:\SmartLabHS"
    }
}
$App   = "$InstallDir\app"
$Logs  = "$InstallDir\logs"
$Mq    = "$InstallDir\mosquitto"
$Z2m   = "$InstallDir\zigbee2mqtt"
New-Item -ItemType Directory -Force -Path $InstallDir, $App, $Logs, $Mq, "$Mq\data" -ErrorAction Stop | Out-Null
Set-Content -Path $Marker -Value "Thư mục cài SmartLab platform (install_windows.ps1)" -Encoding UTF8

# ---------------------------------------------------------------- 1. Công cụ
Step "Kiểm tra / cài công cụ (Python, Node.js, Git, NSSM, Mosquitto)"
function Ensure-Tool($cmd, $wingetId, $label) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { Info "${label}: đã có"; return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "Thiếu $label và máy không có winget. Cài $label thủ công rồi chạy lại." }
    Info "Đang cài $label ..."
    winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
    Refresh-Path
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Fail "Cài $label xong nhưng chưa nhận lệnh '$cmd'. Đóng PowerShell, mở lại (Administrator) rồi chạy lại file này." }
}
Ensure-Tool "git"  "Git.Git"           "Git"
Ensure-Tool "node" "OpenJS.NodeJS.LTS" "Node.js"
Ensure-Tool "nssm" "NSSM.NSSM"         "NSSM"

# Python: dùng trình khởi chạy py nếu có (tránh bản giả của Microsoft Store)
$PyExe = $null
$probeCode = "import sys;print(sys.executable)"
if (Get-Command py -ErrorAction SilentlyContinue) {
    $out = & py -3 -c $probeCode 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { $PyExe = "$out".Trim() }
}
if (-not $PyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $out = & python -c $probeCode 2>$null   # bản giả Microsoft Store không in ra gì
    if ($LASTEXITCODE -eq 0 -and $out) { $PyExe = "$out".Trim() }
}
if (-not $PyExe) {
    Ensure-Tool "py" "Python.Python.3.12" "Python 3.12"
    $PyExe = "$(& py -3 -c $probeCode)".Trim()
}
Info "Python: $PyExe"

$nodeMajor = [int]((& node -v).TrimStart("v").Split(".")[0])
if ($nodeMajor -gt 24) { Info "Lưu ý: Node.js $(& node -v) mới hơn bản Zigbee2MQTT hỗ trợ chính thức (22/24). Nếu Zigbee2MQTT lỗi, cài Node.js 24 LTS." }
if ($nodeMajor -lt 22) { Fail "Zigbee2MQTT cần Node.js 22 trở lên (máy đang có $(& node -v)). Cập nhật: winget upgrade OpenJS.NodeJS.LTS" }

$MosqDir = "$env:ProgramFiles\mosquitto"
if (-not (Test-Path "$MosqDir\mosquitto.exe")) {
    Info "Đang cài Mosquitto ..."
    winget install --id EclipseFoundation.Mosquitto -e --silent --accept-source-agreements --accept-package-agreements | Out-Null
}
if (-not (Test-Path "$MosqDir\mosquitto.exe")) { Fail "Chưa có Mosquitto tại $MosqDir. Tải bản cài Windows tại mosquitto.org rồi chạy lại." }

# MySQL
if (-not $MySqlBin) {
    $found = Get-ChildItem "$env:ProgramFiles\MySQL\MySQL Server *\bin\mysql.exe" -ErrorAction SilentlyContinue |
             Sort-Object FullName | Select-Object -Last 1
    if ($found) { $MySqlBin = $found.FullName }
}
$MySqlService = $null
if (-not $UseSQLite -and (-not $MySqlBin -or -not (Test-Path $MySqlBin))) {
    Info "Không tìm thấy MySQL trên máy này."
    $ans = Read-Host "    Dùng SQLite (một file dữ liệu, hợp cho máy thử nghiệm)? Y/N"
    if ($ans -notmatch '^[Yy]') { Fail "Cài MySQL rồi chạy lại, hoặc chạy với -MySqlBin 'đường dẫn\mysql.exe'." }
    $UseSQLite = $true
}
if ($UseSQLite) {
    New-Item -ItemType Directory -Force -Path "$InstallDir\data" | Out-Null
    Info "Cơ sở dữ liệu: SQLite tại $InstallDir\data\smartlab-platform.db"
} else {
    $MySqlService = (Get-Service | Where-Object { $_.Name -like "MySQL*" } | Select-Object -First 1).Name
    Info "MySQL: $MySqlBin (dịch vụ: $MySqlService)"
}

# ---------------------------------------------------------------- 2. Mật khẩu (.env)
Step "Tạo / đọc cấu hình .env"
$EnvFile = "$App\.env"
$cfg = [ordered]@{}
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        if ($line -match '^\s*([^#=]+?)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2] }
    }
    Info "Dùng lại mật khẩu trong $EnvFile"
}
function Keep($key, $default) { if ($cfg.Contains($key) -and $cfg[$key]) { return $cfg[$key] } return $default }

$dbPw    = Keep "SMARTLAB_DB_PASSWORD"   (New-Password)
$mqttPw  = Keep "SMARTLAB_MQTT_PASSWORD" (New-Password)
$z2mPw   = Keep "Z2M_MQTT_PASSWORD"      (New-Password)
$ro1Pw   = Keep "RO01_MQTT_PASSWORD"     (New-Password)
$ro2Pw   = Keep "RO02_MQTT_PASSWORD"     (New-Password)
$adminPw = Keep "SMARTLAB_ADMIN_PASSWORD" (New-Password)

$dbUrl = if ($UseSQLite) { "sqlite:///$($InstallDir -replace '\\','/')/data/smartlab-platform.db" }
         else { "mysql+pymysql://smartlab:$dbPw@localhost:3306/smartlab?charset=utf8mb4" }
$envText = @"
# SmartLab – cấu hình máy chủ (tạo bởi install_windows.ps1). Giữ bí mật file này.
SMARTLAB_DB_PASSWORD=$dbPw
SMARTLAB_DB_URL=$dbUrl
SMARTLAB_MQTT_HOST=localhost
SMARTLAB_MQTT_PORT=$MqttPort
SMARTLAB_MQTT_USER=smartlab
SMARTLAB_MQTT_PASSWORD=$mqttPw
Z2M_MQTT_PASSWORD=$z2mPw
RO01_MQTT_PASSWORD=$ro1Pw
RO02_MQTT_PASSWORD=$ro2Pw
SMARTLAB_SITE_NAME=$(Keep "SMARTLAB_SITE_NAME" "Khoa Hóa Sinh · BVĐK Đà Nẵng")
SMARTLAB_ADMIN_USER=$(Keep "SMARTLAB_ADMIN_USER" "admin")
SMARTLAB_ADMIN_PASSWORD=$adminPw
SMARTLAB_ZALO_SEND_URL=$(Keep "SMARTLAB_ZALO_SEND_URL" "")
SMARTLAB_ZALO_CHAT_IDS=$(Keep "SMARTLAB_ZALO_CHAT_IDS" "")
SMARTLAB_WATCHDOG_URL=$(Keep "SMARTLAB_WATCHDOG_URL" "")
"@

# ---------------------------------------------------------------- 3. Mã nguồn + Python
Step "Chép phần mềm SmartLab vào $App"
foreach ($item in @("smartlab", "tools", "requirements.txt")) {
    Copy-Item -Recurse -Force "$Src\$item" $App -ErrorAction Stop
}
Write-Utf8NoBom $EnvFile $envText
# *S-1-5-32-544 = nhóm Administrators, *S-1-5-18 = SYSTEM (đúng cả trên Windows tiếng Việt)
icacls $EnvFile /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null

Step "Tạo môi trường Python và cài thư viện"
if (-not (Test-Path "$InstallDir\venv\Scripts\python.exe")) { Run $PyExe @("-m", "venv", "$InstallDir\venv") }
$VPy = "$InstallDir\venv\Scripts\python.exe"
Run $VPy @("-m", "pip", "install", "--upgrade", "pip", "-q")
Run $VPy @("-m", "pip", "install", "-r", "$App\requirements.txt", "-q")

# ---------------------------------------------------------------- 4. MySQL
if (-not $UseSQLite) {
Step "Tạo schema MySQL 'smartlab' và tài khoản riêng"
$rootSecure = Read-Host "    Nhập mật khẩu tài khoản '$MySqlRootUser' của MySQL" -AsSecureString
$env:MYSQL_PWD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($rootSecure))
$sql = "CREATE DATABASE IF NOT EXISTS smartlab CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; " +
       "CREATE USER IF NOT EXISTS 'smartlab'@'localhost' IDENTIFIED BY '$dbPw'; " +
       "ALTER USER 'smartlab'@'localhost' IDENTIFIED BY '$dbPw'; " +
       "GRANT ALL PRIVILEGES ON smartlab.* TO 'smartlab'@'localhost'; FLUSH PRIVILEGES;"
& $MySqlBin -u $MySqlRootUser -e $sql
$code = $LASTEXITCODE
Remove-Item Env:\MYSQL_PWD
if ($code -ne 0) { Fail "Không tạo được schema MySQL (sai mật khẩu root?)." }
Info "Đã tạo schema smartlab (không đụng dữ liệu Reagent Manager)."
}

Push-Location $App
Run $VPy @("-m", "smartlab.seed")
Pop-Location

# ---------------------------------------------------------------- 5. Mosquitto
Step "Cấu hình Mosquitto (MQTT, cổng $MqttPort, có mật khẩu)"
# Chỉ dừng dịch vụ của chính SmartLab. MQTT của dự án khác (vd huyết học) KHÔNG tự tắt.
# Dừng các dịch vụ phụ thuộc trước, MQTT sau cùng (Windows không cho dừng MQTT khi còn dịch vụ dựa vào nó).
foreach ($svcName in @("SmartLab-Web", "SmartLab-Worker", "SmartLab-Zigbee2MQTT", "SmartLab-MQTT")) {
    if (Get-Service $svcName -ErrorAction SilentlyContinue) { Stop-Service $svcName -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 2
$busy = Get-NetTCPConnection -LocalPort $MqttPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($busy) {
    $proc = Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue
    $svc = Get-CimInstance Win32_Service | Where-Object { $_.ProcessId -eq $busy.OwningProcess } | Select-Object -First 1
    Info "Cổng $MqttPort đang được dùng bởi '$($proc.ProcessName)' (PID $($busy.OwningProcess)$(if ($svc) { ", dịch vụ $($svc.Name)" }))."
    Info "  Có thể là MQTT của dự án khác trên máy này."
    $ans = Read-Host "    Tắt hẳn chương trình đó để SmartLab dùng cổng $MqttPort? (Y = tắt, N = dừng cài, chạy lại với -MqttPort 1884)"
    if ($ans -notmatch '^[Yy]') { Fail "Chưa cài tiếp. Muốn chạy song song: .\install_windows.ps1 -UseSQLite -MqttPort 1884" }
    if ($svc) {
        Set-Service $svc.Name -StartupType Disabled
        & sc.exe stop $svc.Name *> $null
        Info "Đã dừng và tắt tự khởi động dịch vụ $($svc.Name)."
    } else {
        Stop-Process -Id $busy.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}
# Không bật persistence: Mosquitto 2.0.20 trên Windows bị sập (0xC0000409) khi bật.
# SmartLab không cần: số đo được lưu vào CSDL ngay khi nhận, cảm biến tự gửi lại theo chu kỳ.
$mqConf = @"
listener $MqttPort
allow_anonymous false
password_file $($Mq -replace '\\','/')/passwd
log_dest stdout
"@
Set-Content -Path "$Mq\mosquitto.conf" -Value $mqConf -Encoding ASCII
$passwd = "$Mq\passwd"
if (Test-Path $passwd) { Remove-Item $passwd }
Run "$MosqDir\mosquitto_passwd.exe" @("-c", "-b", $passwd, "smartlab", $mqttPw)
Run "$MosqDir\mosquitto_passwd.exe" @("-b", $passwd, "zigbee2mqtt", $z2mPw)
Run "$MosqDir\mosquitto_passwd.exe" @("-b", $passwd, "ro01", $ro1Pw)
Run "$MosqDir\mosquitto_passwd.exe" @("-b", $passwd, "ro02", $ro2Pw)
# mosquitto_passwd khóa file chỉ cho người tạo đọc -> dịch vụ (SYSTEM) bị chặn, thoát mã 13.
# Cấp quyền đọc cho SYSTEM (*S-1-5-18) và Administrators (*S-1-5-32-544).
icacls $passwd /grant "*S-1-5-18:R" "*S-1-5-32-544:R" | Out-Null
icacls "$Mq\mosquitto.conf" /grant "*S-1-5-18:R" | Out-Null

# ---------------------------------------------------------------- 5b. Cổng web
# Cổng dashboard / Zigbee2MQTT đã bị chương trình khác dùng -> tự chọn cổng trống kế tiếp
& nssm stop SmartLab-Web *> $null
& nssm stop SmartLab-Zigbee2MQTT *> $null
Start-Sleep -Seconds 2
function Get-FreePort([int]$port, [string]$label) {
    for ($p = $port; $p -lt $port + 20; $p++) {
        $l = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $l) {
            if ($p -ne $port) { Info "${label}: cổng $port đang bận, dùng cổng $p" }
            return $p
        }
        $who = (Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Info "${label}: cổng $p đang được '$who' dùng"
    }
    Fail "Không tìm được cổng trống cho $label từ $port."
}
$WebPort = Get-FreePort $WebPort "Dashboard"
$Z2mPort = Get-FreePort $Z2mPort "Zigbee2MQTT"

# ---------------------------------------------------------------- 6. Zigbee2MQTT
Step "Cài Zigbee2MQTT"
if (-not (Test-Path "$Z2m\package.json")) {
    Run "git" @("clone", "--depth", "1", "https://github.com/Koenkk/zigbee2mqtt.git", $Z2m)
}
Push-Location $Z2m
# Node.js 25+ không còn kèm corepack -> cài pnpm bằng npm
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    if (Get-Command corepack -ErrorAction SilentlyContinue) { Run "corepack" @("enable") }
    else { Run "npm" @("install", "-g", "pnpm") }
    Refresh-Path
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) { $env:Path += ";$env:APPDATA\npm" }
}
Run "pnpm" @("install", "--frozen-lockfile")
Run "pnpm" @("run", "build")
Pop-Location

# Cổng COM của USB Zigbee. Bỏ qua cổng Bluetooth ("Standard Serial over Bluetooth link").
# Chip USB có thể là CP2102N (Silicon Labs) hoặc CH9102F (WCH) ở CẢ bản P lẫn bản E,
# nên loại adapter xác định theo tên gốc USB báo về:
#   "Sonoff Zigbee 3.0 USB Dongle Plus V2" -> ZBDongle-E -> adapter ember
#   "Sonoff Zigbee 3.0 USB Dongle Plus"    -> ZBDongle-P -> adapter zstack
$dev = $null
$ports = Get-CimInstance Win32_PnPEntity | Where-Object {
    $_.Name -match '\((COM\d+)\)' -and $_.Name -notmatch 'Bluetooth'
}
if ($ZigbeePort) {
    $dev = $ports | Where-Object { $_.Name -match "\($ZigbeePort\)" } | Select-Object -First 1
} else {
    $dev = $ports | Where-Object { $_.Name -match 'CP210|Silicon Labs|CH910|CH343|CH340|USB-Enhanced|Sonoff' } |
           Select-Object -First 1
    if ($dev -and $dev.Name -match '\((COM\d+)\)') { $ZigbeePort = $Matches[1] }
}
if (-not $ZigbeePort) {
    $usbZb = Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'Sonoff|Zigbee' -and $_.Name -notmatch '\(COM' }
    if ($usbZb) {
        Info "Thấy '$($usbZb[0].Name)' nhưng CHƯA có driver (nằm trong Other devices)."
        Info "  Xem Properties > Details > Hardware Ids:"
        Info "  - VID_10C4&PID_EA60: cài driver Silicon Labs CP210x VCP (silabs.com)"
        Info "  - VID_1A86&PID_55D4: cài driver WCH CH343SER (wch-ic.com)"
        Fail "Cài driver, rút cắm lại USB, thấy cổng COM trong Device Manager rồi chạy lại."
    }
}
if (-not $ZigbeePort) {
    Info "Không tự tìm thấy USB Zigbee. Mở Device Manager > Ports (COM & LPT) để xem cổng."
    $ZigbeePort = Read-Host "    Nhập cổng COM của ZBDongle-P (vd COM4)"
}
if ($ZigbeeAdapter -eq "auto") {
    $busDesc = ""
    if ($dev) {
        $busDesc = (Get-PnpDeviceProperty -InstanceId $dev.PNPDeviceID -KeyName "DEVPKEY_Device_BusReportedDeviceDesc" `
                    -ErrorAction SilentlyContinue).Data
    }
    if ($busDesc -match 'V2') { $ZigbeeAdapter = "ember" }
    elseif ($busDesc -match 'Dongle Plus') { $ZigbeeAdapter = "zstack" }
    else {
        Info "Không đọc được loại USB ($busDesc). Xem tem trên USB: ZBDongle-E hay ZBDongle-P."
        $ans = Read-Host "    Gõ E hoặc P"
        $ZigbeeAdapter = if ($ans -match '^[Ee]') { "ember" } else { "zstack" }
    }
}
Info "USB Zigbee: $ZigbeePort (adapter: $ZigbeeAdapter$(if ($dev) { ", $($dev.Name)" }))"

New-Item -ItemType Directory -Force -Path "$Z2m\data" | Out-Null
$z2mConf = "$Z2m\data\configuration.yaml"
if (-not (Test-Path $z2mConf)) {
    $yaml = @"
# Zigbee2MQTT - SmartLab. Device names: hs/sh19_mat, hs/sh19_dong, hs/mt01 ...
homeassistant:
  enabled: false
frontend:
  enabled: true
  port: $Z2mPort
mqtt:
  base_topic: zigbee2mqtt
  server: mqtt://localhost:$MqttPort
  user: zigbee2mqtt
  password: $z2mPw
serial:
  port: $ZigbeePort
  adapter: $ZigbeeAdapter
advanced:
  channel: 25
  network_key: GENERATE
  pan_id: GENERATE
  ext_pan_id: GENERATE
  last_seen: ISO_8601
"@
    Write-Utf8NoBom $z2mConf $yaml
} else {
    # Giữ nguyên cấu hình (có thể đã ghép cảm biến), chỉ cập nhật cổng giao diện và cổng MQTT
    $yaml = [IO.File]::ReadAllText($z2mConf)
    $yaml = [regex]::Replace($yaml, '(?ms)(^frontend:.*?^\s+port:\s*)\d+', "`${1}$Z2mPort")
    $yaml = [regex]::Replace($yaml, 'mqtt://localhost:\d+', "mqtt://localhost:$MqttPort")
    # Mật khẩu MQTT phải khớp file passwd vừa tạo (cấu hình có thể chép từ lần cài khác)
    $yaml = [regex]::Replace($yaml, '(?ms)(^mqtt:.*?^\s+user:\s*)\S+', "`${1}zigbee2mqtt")
    $yaml = [regex]::Replace($yaml, '(?ms)(^mqtt:.*?^\s+password:\s*)\S+', "`${1}$z2mPw")
    Write-Utf8NoBom $z2mConf $yaml
    Info "Giữ nguyên $z2mConf, cập nhật cổng giao diện $Z2mPort và tài khoản MQTT."
}

# ---------------------------------------------------------------- 7. Dịch vụ Windows
Step "Đăng ký dịch vụ Windows (tự chạy khi bật máy, tự khởi động lại khi lỗi)"
function Install-SmartLabService($name, $exe, $params, $dir, [string[]]$deps) {
    & nssm status $name *> $null
    if ($LASTEXITCODE -eq 0) {
        & nssm stop $name *> $null
        & nssm remove $name confirm *> $null
    }
    Run "nssm" @("install", $name, $exe)
    Run "nssm" @("set", $name, "AppParameters", $params)
    Run "nssm" @("set", $name, "AppDirectory", $dir)
    Run "nssm" @("set", $name, "Start", "SERVICE_AUTO_START")
    Run "nssm" @("set", $name, "AppStdout", "$Logs\$name.log")
    Run "nssm" @("set", $name, "AppStderr", "$Logs\$name.log")
    Run "nssm" @("set", $name, "AppRotateFiles", "1")
    Run "nssm" @("set", $name, "AppRotateBytes", "5000000")
    Run "nssm" @("set", $name, "AppExit", "Default", "Restart")
    Run "nssm" @("set", $name, "AppRestartDelay", "10000")
    $deps = @($deps | Where-Object { $_ })
    if ($deps.Count -gt 0) { Run "nssm" (@("set", $name, "DependOnService") + $deps) }
    & nssm start $name *> $null
    Start-Sleep -Seconds 3
    $st = (Get-Service $name -ErrorAction SilentlyContinue).Status
    if ($st -ne "Running") {
        Write-Host "    $name không chạy được (trạng thái: $st). Nhật ký gần nhất:" -ForegroundColor Yellow
        $log = "$Logs\$name.log"
        if (Test-Path $log) {
            Write-Host "    --- $log"
            try {
                $fs = [IO.File]::Open($log, "Open", "Read", "ReadWrite")
                $lines = (New-Object IO.StreamReader($fs)).ReadToEnd() -split "`r?`n"
                $fs.Close()
                $lines | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" }
            } catch { Write-Host "    (không đọc được: $($_.Exception.Message))" }
        }
        Fail "Dịch vụ $name không khởi động được. Chụp màn hình này gửi lại để xử lý."
    }
    Info "${name}: đã chạy"
}
$NodeExe = (Get-Command node).Source
Install-SmartLabService "SmartLab-MQTT" "$MosqDir\mosquitto.exe" "-c `"$Mq\mosquitto.conf`"" $Mq @()
Install-SmartLabService "SmartLab-Zigbee2MQTT" $NodeExe "index.js" $Z2m @("SmartLab-MQTT")
Install-SmartLabService "SmartLab-Worker" $VPy "-m smartlab.worker" $App @("SmartLab-MQTT", $MySqlService)
Install-SmartLabService "SmartLab-Web" $VPy "-m uvicorn smartlab.web:app --host 0.0.0.0 --port $WebPort" $App @($MySqlService)

# ---------------------------------------------------------------- 8. Tường lửa, nguồn điện
Step "Mở tường lửa cho mạng nội bộ ($WebPort dashboard, $Z2mPort Zigbee2MQTT, $MqttPort MQTT)"
foreach ($port in $WebPort, $Z2mPort, $MqttPort) {
    $rule = "SmartLab TCP $port"
    Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $port `
        -Action Allow -RemoteAddress LocalSubnet -Profile Any | Out-Null
}

Step "Tắt Sleep / Hibernate để máy chạy 24/7"
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off

# ---------------------------------------------------------------- Xong
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
       Select-Object -First 1).IPAddress
Write-Host "`n================ CÀI ĐẶT XONG ================" -ForegroundColor Green
Write-Host "Dashboard TV       : http://${ip}:$WebPort/tv"
Write-Host "Danh mục điểm đo   : http://${ip}:$WebPort/catalog   (admin / $adminPw)"
Write-Host "Ghép cảm biến      : http://${ip}:$Z2mPort"
Write-Host "MQTT cho camera RO : máy chủ $ip, cổng $MqttPort; ro01 / $ro1Pw ; ro02 / $ro2Pw"
Write-Host "Mật khẩu lưu tại   : $EnvFile (chỉ Administrators đọc được)"
Write-Host "Nhật ký            : $Logs"
Write-Host "`nViệc còn lại làm tay:" -ForegroundColor Yellow
Write-Host " 1. BIOS: 'Restore on AC Power Loss' = Power On"
Write-Host " 2. Windows Update: đặt Active hours / tạm dừng, cập nhật thủ công ngoài giờ"
Write-Host " 3. Đặt IP tĩnh cho card mạng theo IP Phòng CNTT cấp"
Write-Host " 4. Điền Zalo vào $EnvFile rồi chạy: nssm restart SmartLab-Worker"
