# SmartLab – nền tảng giám sát (giai đoạn 1)

Python (FastAPI) + MySQL + MQTT (Mosquitto) + Zigbee2MQTT, chạy bằng Docker trên máy chủ đặt tại khoa.

## Đã có
| Phần | Mô tả |
|---|---|
| Nhận dữ liệu | Zigbee2MQTT (`zigbee2mqtt/hs/tl01`), camera AI-on-the-edge (`smartlab/hs/ro01/main/value`), API/LIS (`smartlab/hs/xn01`) |
| Danh mục điểm đo | `/catalog` (có mật khẩu): sửa tên hiển thị, topic, ngưỡng, độ trễ báo; mọi thay đổi ghi nhật ký |
| Dashboard TV | `/tv`: 12 tủ, 3 máy RO, 2 máy XN, 6 khu vực; tự làm mới 10 giây; giờ Việt Nam |
| Cảnh báo | Vượt ngưỡng liên tục quá thời gian trễ → Zalo; nhắc lại 30 phút; báo trở lại bình thường; mất tín hiệu |
| Chống báo nhầm | Mở cửa tủ ngắn không báo; điểm chưa ghép nối không báo; khởi động lại máy chủ không báo mất tín hiệu hàng loạt |
| API | `/api/status`, `/api/events` |
| Giả lập | `tools/simulator.py`: 23 điểm đo, kịch bản bình thường / có sự cố |

**Chưa làm (bước tiếp):** sổ theo dõi ISO xuất PDF/Excel kèm ảnh RO; lấy ảnh từ camera; đọc RO03 qua API; IQC, Monitoring TAT, trạng thái cobas (cần phương án kết nối LIS).

## Chạy thử trên máy tính (không cần thiết bị)
```bash
pip install -r requirements.txt
# MQTT broker bất kỳ, ví dụ: mosquitto -p 1883
python -m smartlab.seed                       # tạo 23 điểm đo (SQLite smartlab.db)
SMARTLAB_ADMIN_PASSWORD=123 uvicorn smartlab.web:app --port 8000 &
python -m smartlab.worker &
python tools/simulator.py --scenario alarm    # TL07 nóng dần, TL10 im lặng
# Mở http://localhost:8000/tv  và  http://localhost:8000/catalog (admin / 123)
```
Kiểm thử: `pytest -q tests`

## Triển khai tại khoa
1. Máy chủ Ubuntu Server LTS, cài Docker. Cắm USB ZBDongle-P, lấy đường dẫn: `ls /dev/serial/by-id/`, sửa dòng `devices:` trong `docker-compose.yml`.
2. `cp .env.example .env` rồi đặt mật khẩu MySQL, MQTT, quản trị; điền Zalo khi có token.
3. `cp zigbee2mqtt/configuration.example.yaml zigbee2mqtt/configuration.yaml`, điền mật khẩu MQTT.
4. Tạo tài khoản MQTT (smartlab, zigbee2mqtt, mỗi camera một tài khoản):
   `docker run --rm -v $PWD/mosquitto:/m eclipse-mosquitto:2 mosquitto_passwd -c -b /m/passwd smartlab <mật khẩu>`
   (các tài khoản sau bỏ `-c`).
5. `docker compose up -d` rồi `docker compose exec web python -m smartlab.seed`.
6. Ghép nối cảm biến tại `http://<IP>:8080`, đặt friendly_name theo quy tắc `hs/tl01`, `hs/mt01`…
7. Mở `http://<IP>:8000/catalog` sửa tên tủ, khu vực, ngưỡng theo khảo sát. TV mở `http://<IP>:8000/tv` ở chế độ toàn màn hình.

## Dùng chung MySQL với Reagent Manager
SmartLab dùng schema riêng `smartlab` trên cùng máy chủ MySQL, không đụng bảng của Reagent Manager.
Khi làm bản web quản lý hóa chất, hai bên đọc chung qua tài khoản MySQL phân quyền, sau đó gộp đăng nhập.
