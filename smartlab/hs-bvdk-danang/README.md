# Triển khai SmartLab – Khoa Hóa Sinh, BVĐK Đà Nẵng

Gói triển khai cho Hợp đồng dịch vụ SmartLab (48.900.000đ, 21 điểm đo, 15–25 ngày làm việc).
Nền tảng: **Home Assistant + Zigbee (Sonoff)** cho 18 điểm nhiệt độ/độ ẩm, **ESPHome + RS485** cho 3 điểm EC nước RO,
**Zalo Bot** để báo động, **watchdog trên VPS** để báo mất mạng/mất điện.

> Hạng mục 6 (Phần mềm Quản lý Kho Hóa chất) **không nằm trong gói này**, cần triển khai riêng.

## 1. Đối chiếu hợp đồng ↔ giải pháp

| HĐ | Hạng mục | SL | Thiết bị / thành phần | File |
|---|---|---|---|---|
| 1 | Nhiệt độ tủ lạnh/tủ đông | 12 | Sonoff **SNZB-02LD** (đầu dò chống nước IP65, màn LCD) | `points.csv` tl01–tl12 |
| 2 | Nhiệt độ & độ ẩm phòng | 6 | Sonoff **SNZB-02D** (LCD) | `points.csv` mt01–mt06 |
| 3 | EC nước RO | 3 | ESP32 + RS485 + bộ phát EC công nghiệp, đầu dò inline K=0.1 | `esphome/` |
| 4 | Dashboard 24/7 + sổ ISO 15189 | 1 | Home Assistant, dashboard TV, ghi sổ 3 lần/ngày, lưu lịch sử 2 năm | `homeassistant/` |
| 5 | Cảnh báo Zalo 24/7 | 1 | Zalo Bot + watchdog ngoài bệnh viện | `smartlab_core.yaml`, `watchdog/` |
| 6 | Phần mềm kho hóa chất | 1 | *Ngoài gói này* | – |
| 7 | Hiệu chuẩn, thi công, đào tạo | 1 | Hệ số hiệu chuẩn trong `points.csv`, checklist nghiệm thu bên dưới | – |

## 2. Dự toán vật tư

Giá Shopee chụp ngày 01/11/2026 (giá sau voucher 11.11):

| Thiết bị | SL | Đơn giá | Thành tiền |
|---|---|---|---|
| SNZB-02LD (tủ lạnh/tủ đông) | 12 | 350.760 | 4.209.120 |
| SNZB-02D (phòng) | 6 | 237.800 | 1.426.800 |
| ZBDongle-P (coordinator) | 1 | 384.651 | 384.651 |
| **Cộng phần đã có giá** | | | **6.020.571** |

Phần còn lại – **ước tính, cần báo giá thực**:

| Thiết bị | SL | Ước tính |
|---|---|---|
| Thiết bị Zigbee cắm điện làm router (ổ cắm/relay Zigbee Sonoff) | 3–4 | 0,8–1,4 tr |
| Mini PC máy chủ (N100, 8GB RAM, SSD 256GB) | 1 | 4–5 tr |
| UPS 1kVA có cổng USB | 1 | 2–2,5 tr |
| Bộ phát EC RS485 + đầu dò K=0.1 + T-fitting | 3 | 4,5–9 tr |
| ESP32 + module RS485 + nguồn 12/24V + hộp | 3 | 0,6–1 tr |
| Cáp USB nối dài, dây, ốc vít, băng keo, nhãn | – | ~1 tr |
| VPS chạy watchdog (12 tháng) | 1 | 1,2–1,8 tr |
| **Tổng vật tư ước tính** | | **~20–27 tr / HĐ 48,9 tr** |

Mẹo mua: gom đơn ≥ 550k để dùng voucher giảm 22k; các sản phẩm cùng shop thì đặt chung 1 đơn.

## 3. Tiến độ (theo Điều 3 HĐ)

| Giai đoạn | Ngày | Việc |
|---|---|---|
| 0. Khảo sát | trước khi đặt hàng | Đếm & đo nhiệt độ thực tế từng tủ (tủ nào là tủ đông, có tủ −80°C không), vị trí ống RO, ổ điện, sóng Wi-Fi, chỗ đặt máy chủ + TV. Cập nhật `points.csv`. |
| 1. Xưởng | 7 ngày | Nạp firmware Z-Stack mới nhất cho ZBDongle-P; cài HA; ghép nối 18 cảm biến; nạp ESPHome 3 module RO; **hiệu chuẩn** đối chiếu nhiệt kế chuẩn; **chạy liên tục 72h**. |
| 2. Thi công | 2–3 ngày | Gắn cảm biến 12 tủ, 6 phòng; lắp đầu dò EC 3 máy RO (khóa van, xả áp trước); đặt router Zigbee; máy chủ + router mạng + dongle trên UPS. |
| 3. Cấu hình & đào tạo | 5–7 ngày | Zalo bot + danh sách người nhận, chỉnh ngưỡng, test cảnh báo từng điểm, đào tạo, nghiệm thu. |

## 4. Cài đặt

### 4.1 Máy chủ Home Assistant
1. Cài **Home Assistant OS** lên mini PC. Cắm ZBDongle-P qua **cáp USB nối dài** (tránh nhiễu cổng USB 3.0), đặt dongle ở vị trí cao, giữa khoa.
2. Chép thư mục `homeassistant/` vào `/config` (giữ nguyên `packages/`, `dashboards/`).
3. `cp secrets.example.yaml secrets.yaml` rồi điền URL Zalo, chat_id, URL watchdog.
4. Settings → Devices → Add Integration:
   - **Zigbee Home Automation (ZHA)** → chọn ZBDongle-P.
   - **File** → tạo 2 notifier: `smartlab_so_theo_doi` (ghi `/config/so_theo_doi.txt`) và `smartlab_nhat_ky_canh_bao` (ghi `/config/nhat_ky_canh_bao.txt`).
   - **Network UPS Tools** (nếu UPS hỗ trợ) → kiểm tra entity `sensor.ups_status_data`, đổi trong `smartlab_core.yaml` nếu khác tên.
   - **ESPHome** → thêm 3 module RO.
5. Ghép nối từng cảm biến Zigbee, **đổi entity ID** đúng quy ước (đây là chỗ dễ sai nhất):
   - tủ: `sensor.tl01_raw_temperature` … `sensor.tl12_raw_temperature`
   - phòng: `sensor.mt01_raw_temperature`, `sensor.mt01_raw_humidity` … mt06
   - RO (ESPHome tự đặt): `sensor.ro01_ec` … ro03
   - Dán nhãn mã điểm đo (TL01, MT01, RO01…) lên thiết bị và lên tủ.
   - Ghép **router Zigbee trước**, cảm biến pin sau, ghép tại chỗ lắp đặt để thiết bị chọn đường tốt nhất.
6. Developer Tools → Check configuration → Restart.

### 4.2 Sửa ngưỡng / tên / hiệu chuẩn
Chỉ sửa `points.csv`, rồi:
```bash
python3 tools/generate.py
```
và chép lại `packages/smartlab_points.yaml`, `dashboards/smartlab.yaml` lên máy chủ.

| Cột | Ý nghĩa |
|---|---|
| `t_min,t_max` | ngưỡng nhiệt độ (với RO: ngưỡng EC µS/cm) |
| `h_min,h_max` | ngưỡng độ ẩm (chỉ phòng) |
| `t_offset,h_offset` | hệ số hiệu chỉnh = giá trị nhiệt kế chuẩn − giá trị cảm biến |
| `delay_min` | phải vượt ngưỡng liên tục bao lâu mới báo (tránh báo nhầm khi mở cửa tủ) |

### 4.3 Zalo Bot
1. Tạo bot trên Zalo Bot Platform, lấy token. Kiểm tra lại URL API hiện hành trong tài liệu Zalo (mẫu trong `secrets.example.yaml`).
2. Trưởng khoa + từng người trực nhắn 1 tin cho bot (hoặc thêm bot vào nhóm trực) để lấy `chat_id`.
3. Điền vào `secrets.yaml`, gọi thử `script.smartlab_zalo` trong Developer Tools.

### 4.4 Watchdog (VPS của Bên B)
```bash
sudo mkdir -p /opt/smartlab && sudo cp watchdog/watchdog.py /opt/smartlab/
sudo tee /etc/smartlab-watchdog.env <<EOF
WATCHDOG_KEY=<chuỗi ngẫu nhiên dài>
ZALO_SEND_URL=https://bot-api.zaloplatforms.com/bot<TOKEN>/sendMessage
ZALO_CHAT_IDS=<id1>,<id2>
TIMEOUT_MIN=5
EOF
sudo cp watchdog/smartlab-watchdog.service /etc/systemd/system/
sudo systemctl enable --now smartlab-watchdog
```
Đặt sau Caddy/nginx có HTTPS. Một watchdog dùng được cho nhiều khoa/bệnh viện (mỗi nơi một đường dẫn `/hb/<ten>`).

## 5. Cảnh báo đã cài

| Sự kiện | Khi nào gửi Zalo |
|---|---|
| Vượt ngưỡng | sau `delay_min` phút liên tục; báo lại khi bình thường |
| Nhắc lại | mỗi 30 phút khi còn điểm vượt ngưỡng |
| Cảm biến mất tín hiệu | mất dữ liệu > 60 phút (pin yếu, mất sóng) |
| Mất điện lưới | UPS chuyển sang pin > 1 phút; báo lại khi có điện |
| Máy chủ khởi động lại | 2 phút sau khi khởi động |
| Mất mạng / máy chủ chết | watchdog ngoài bệnh viện báo sau 5 phút không có nhịp tim |

Mọi tin cảnh báo đồng thời ghi vào `nhat_ky_canh_bao.txt`.

## 6. Sổ theo dõi ISO 15189
- Tự ghi 07:30, 13:30, 19:30 vào `/config/so_theo_doi.txt` (tất cả 21 điểm, đã hiệu chỉnh).
- Lịch sử chi tiết lưu 730 ngày; xuất CSV từ trang History (nút tải về) theo tháng để in/ký.
- Lưu kèm hồ sơ: biên bản hiệu chuẩn (số chứng nhận nhiệt kế chuẩn, ngày, offset từng điểm) – chính là các giá trị trong `points.csv`.
- Bật sao lưu tự động (Settings → System → Backups) ra ổ mạng hoặc Google Drive.

## 7. Checklist nghiệm thu (kèm Biên bản nghiệm thu)
- [ ] 21/21 điểm hiển thị trên TV phòng trực, số liệu khớp màn hình LCD tại chỗ.
- [ ] Hiệu chuẩn: chênh lệch sau hiệu chỉnh so với nhiệt kế chuẩn ≤ ±0,5°C, có biên bản.
- [ ] Test vượt ngưỡng từng điểm (lấy đầu dò ra khỏi tủ / nhúng nước ấm) → Zalo đến **tất cả** người nhận.
- [ ] Test mất tín hiệu (tháo pin 1 cảm biến, có thể hạ tạm delay để test nhanh).
- [ ] Test mất điện: rút điện UPS → Zalo "mất điện"; cắm lại → "có điện".
- [ ] Test mất mạng: rút WAN router → watchdog báo sau 5 phút; cắm lại → "kết nối lại".
- [ ] File sổ theo dõi có dòng ghi tự động; xuất được CSV.
- [ ] 72h chạy liên tục không mất dữ liệu.
- [ ] Đào tạo + bàn giao tài liệu (bản in + file), danh sách người tham gia ký nhận.

## 8. Lưu ý kỹ thuật cần kiểm tra trước khi đặt hàng
1. **Dải đo đầu dò SNZB-02LD**: kiểm tra datasheet có phủ nhiệt độ tủ đông của khoa. Nếu khoa có **tủ −80°C**, cảm biến này không phù hợp → dùng đầu dò PT100 + ESPHome cho tủ đó.
2. **Sóng Zigbee**: tủ lạnh vỏ kim loại + tường bệnh viện chặn sóng. Bắt buộc có 3–4 router Zigbee cắm điện; kiểm tra bản đồ mạng (ZHA → Visualization) sau khi lắp.
3. **Dây đầu dò qua cửa tủ**: đi dây dẹt sát gioăng phía bản lề, kiểm tra cửa còn kín.
4. **Ngưỡng EC nước RO** (mặc định 10 µS/cm) chỉ là giá trị tạm: đặt theo yêu cầu nước của từng máy xét nghiệm trong hướng dẫn của hãng.
5. **Thanh ghi Modbus** trong `esphome/ro-common.yaml` là placeholder (TODO) – điền theo datasheet bộ phát EC đã mua.
6. **Lắp đầu dò RO**: khóa van, xả áp, báo khoa dừng máy XN liên quan; kiểm tra rò rỉ sau 24h.
7. Không commit `secrets.yaml` (đã có `.gitignore`).

## Cấu trúc thư mục
```
points.csv                      21 điểm đo: tên, ngưỡng, hệ số hiệu chuẩn
tools/generate.py               sinh cấu hình từ points.csv
homeassistant/
  configuration.yaml
  secrets.example.yaml
  packages/smartlab_core.yaml   Zalo, nhịp tim, UPS, khởi động lại
  packages/smartlab_points.yaml (tự sinh) cảm biến hiệu chỉnh, cảnh báo, ghi sổ
  dashboards/smartlab.yaml      (tự sinh) dashboard TV
esphome/ro01..03.yaml           firmware 3 module EC nước RO
watchdog/                       giám sát mất kết nối, chạy trên VPS
```
