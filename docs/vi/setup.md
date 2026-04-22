# Hướng dẫn cài đặt

Quy trình cài đặt thiết bị AIVIS Camera gồm 3 phase:

| Phase | Nội dung | Thực hiện trên |
|-------|----------|----------------|
| [Phase 1](#phase-1-tạo-camera-trên-aivis-admin) | Tạo bản ghi camera trên AIVIS Admin | AIVIS Admin Web |
| [Phase 2](#phase-2-tạo-cloudflare-tunnel) | Tạo Cloudflare Tunnel và cập nhật domain/token | Cloudflare Dashboard + AIVIS Admin |
| [Phase 3](#phase-3-cài-đặt-phần-mềm-trên-jetson) | Cài đặt phần mềm trên Jetson | Jetson Orin Nano (SSH) |

---

## Phase 1: Tạo camera trên AIVIS Admin

### Chuẩn bị

- Link download image, domain backend, thông tin tài khoản mặc định,... được lưu trong sheet dự án. Liên hệ quản lý dự án nếu chưa có quyền truy cập.
- Serial Number của Jetson Orin Nano (in trên nhãn mặt dưới thiết bị)

![Jetson Serial Number](../images/phase1/jetson_nano_serial_number.webp)

### Các bước thực hiện

**Bước 1.** Đăng nhập AIVIS Admin

![Login AIVIS Admin](../images/phase1/step1_admin_login_page.png)

**Bước 2.** Vào menu **Cameras** > nhấn **+ Add New Camera**

![Click tạo camera mới](../images/phase1/step2_admin_click_create_new_camera.png)

**Bước 3.** Điền thông tin camera

1. Nhập **Camera Name** và **Serial Number** (lấy từ nhãn Jetson)
2. Nhấn **Create**

> Các trường khác (Installation Location, Domain Name, Cloudflare Tunnel Token, Facility Type, ...) có thể điền sau.

![Tạo camera mới](../images/phase1/step3_admin_create_camera.png)

**Bước 4.** Lấy thông tin Device Info

Sau khi tạo xong, vào trang chi tiết camera > nhấn **Device Info**. Popup hiển thị 3 thông tin cần lưu lại cho [Phase 3](#phase-3-cài-đặt-phần-mềm-trên-jetson):

- **Device ID** — UUID của camera
- **Backend URL** — URL API backend
- **Secret Key** — khóa HMAC để xác thực

![Copy Device ID](../images/phase1/step4_admin_copy_device_id_to_clipboard.png)

> Nhấn icon copy bên cạnh mỗi trường để sao chép giá trị.

---

## Phase 2: Tạo Cloudflare Tunnel

Cloudflare Tunnel cho phép backend truy cập thiết bị Jetson từ xa (OTA update, live stream, SSH) mà không cần public IP hoặc port forwarding.

> Link download, domain backend, thông tin tài khoản mặc định,... được lưu trong sheet dự án. Liên hệ quản lý dự án nếu chưa có quyền truy cập.

Mỗi camera cần **1 tunnel** với **2 public hostname**:
- `{device-id}.aivis-camera.ai` — truy cập HTTP (stream, API, OTA)
- `{device-id}-ssh.aivis-camera.ai` — truy cập SSH

### Các bước thực hiện

**Bước 1.** Đăng nhập [Cloudflare Dashboard](https://dash.cloudflare.com)

![Cloudflare Login](../images/phase2/step1_cloudflare_login_page.png)

**Bước 2.** Chọn **Zero Trust** ở sidebar trái

![Click Zero Trust](../images/phase2/step2_cloudflare_click_zero_trust.png)

**Bước 3.** Vào **Connectors** > **Cloudflare Tunnels** > nhấn **Create a tunnel**

![Tạo tunnel](../images/phase2/step3_cloudflare_create_a_tunnel.png)

**Bước 4.** Chọn **Cloudflared** > nhấn **Select Cloudflared**

![Chọn Cloudflared](../images/phase2/step4_cloudflare_select_cloudflared.png)

**Bước 5.** Đặt tên tunnel

1. Nhập tunnel name theo format: `Camera {device-id}` (ví dụ: `Camera 671fb184-bcbf-4fe9-8459-a7a85b64f994`)
2. Nhấn **Save tunnel**

![Đặt tên tunnel](../images/phase2/step5_cloudflare_enter_tunnel_name.png)

**Bước 6.** Bỏ qua trang "Install and run a connector" > nhấn **Next**

> Không cần chạy lệnh cài đặt ở bước này. Cloudflared sẽ được cài tự động trên Jetson bởi `install-software.sh`.

![Click Next](../images/phase2/step6_cloudflare_click_next.png)

**Bước 7.** Thêm Public Hostname cho **HTTP** (stream/API)

1. **Subdomain**: `{device-id}` (ví dụ: `671fb184-bcbf-4fe9-8459-a7a85b64f994`)
2. **Domain**: `aivis-camera.ai`
3. **Type**: `HTTP`
4. **URL**: `localhost`
5. Nhấn **Complete setup**

![Thêm domain stream](../images/phase2/step7_cloudflare_enter_domain_stream_for_camera.png)

**Bước 8.** Quay lại danh sách Tunnels > nhấn **Configure** trên tunnel vừa tạo

![Click Configure](../images/phase2/step8_cloudflare_click_config_camera_after_created.png)

**Bước 9.** Chọn tab **Published application routes** > nhấn **+ Add a published application route**

![Thêm route mới](../images/phase2/step9_cloudflare_click_add_a_published_application_route_button.png)

**Bước 10.** Thêm Public Hostname cho **SSH**

1. **Subdomain**: `{device-id}-ssh` (ví dụ: `671fb184-bcbf-4fe9-8459-a7a85b64f994-ssh`)
2. **Domain**: `aivis-camera.ai`
3. **Type**: `SSH`
4. **URL**: `localhost:22`
5. Nhấn **Save**

![Thêm domain SSH](../images/phase2/step10_cloudflare_enter_domain_ssh_for_camera.png)

**Bước 11.** Chọn tab **Overview** > trong mục **Connectors** nhấn **Add a connector**

![Add connector](../images/phase2/step11_cloudflare_click_add_a_connector_button.png)

**Bước 12.** Copy **Tunnel Token**

Trong popup "Install and run a connector", copy token từ dòng lệnh `cloudflared tunnel run --token eyJhI...`

> Chỉ copy phần token (bắt đầu bằng `eyJ...`), không copy toàn bộ lệnh.

![Copy tunnel token](../images/phase2/step12_cloudflare_copy_tunnel_token.png)

**Bước 13.** Quay lại AIVIS Admin > vào trang chi tiết camera > nhấn **Edit**

![Edit camera](../images/phase2/step13_admin_edit_current_camera.png)

**Bước 14.** Nhấn icon khóa bên cạnh trường **Domain Name** và **Cloudflare Tunnel Token** để mở khóa chỉnh sửa

![Mở khóa input](../images/phase2/step14_admin_click_icon_for_enable_domain_and_token_input.png)

**Bước 15.** Điền Domain Name và Tunnel Token

1. **Domain Name**: copy từ tab Published application routes trên Cloudflare (hostname HTTP, ví dụ: `671fb184-bcbf-4fe9-8459-a7a85b64f994.aivis-camera.ai`)

   ![Lấy domain từ Cloudflare](../images/phase2/step15_get_domain_from_cloudflare_tab.png)

2. **Cloudflare Tunnel Token**: paste token đã copy ở bước 12
3. Nhấn **Save**

![Nhập domain và token](../images/phase2/step15_admin_enter_domain_and_token.png)

---

## Phase 3: Cài đặt và cấu hình thiết bị Jetson

### Yêu cầu

- SSD NVMe 256GB (mới hoặc đã xóa)
- File image hệ thống (download link xem trong [sheet thông tin dự án](<!-- TODO: link sheet -->))
- Adapter ghi SSD (USB-to-NVMe hoặc máy tính có slot M.2)
- Thông tin Device Info từ [Phase 1 - Bước 4](#các-bước-thực-hiện): Device ID, Backend URL, Secret Key

### Các bước thực hiện

**Bước 1.** Download file image

Download file image hệ thống từ link trong sheet thông tin dự án.

> Link download, domain backend, thông tin tài khoản mặc định,... được lưu trong sheet dự án. Liên hệ quản lý dự án nếu chưa có quyền truy cập.

**Bước 2.** Ghi image vào SSD

Kết nối SSD với máy tính qua adapter, sau đó ghi image:

```bash
# Từ file nén (.img.gz)
pigz -dc jetson-base.img.gz | sudo dd of=/dev/<ssd-device> bs=4M status=progress
```

> Thay `<ssd-device>` bằng tên thiết bị SSD thực tế (ví dụ: `sda`, `nvme0n1`). Kiểm tra bằng `lsblk` trước khi ghi.

**Bước 3.** Lắp đặt phần cứng

Lắp SSD vào Jetson Orin Nano và kết nối các thiết bị ngoại vi:

| Thiết bị | Cổng kết nối |
|----------|-------------|
| SSD NVMe 256GB | Slot M.2 trên board |
| Camera CSI (IMX219) | Cổng CSI qua ribbon cable |
| USB Mic/Speaker | Cổng USB |
| SIM7600 LTE Module | Cổng USB (nếu cần 4G) |
| Nguồn điện (9-19V 5A) | DC barrel jack |
| Cáp LAN (khuyến nghị cho setup ban đầu) | Cổng Ethernet |
| Màn hình (HDMI) | Cổng HDMI (cần cho setup ban đầu) |
| Bàn phím USB | Cổng USB (cần cho setup ban đầu) |

![Lắp đặt thiết bị - mặt trên](../images/phase3_physical_settings/illustrative01.webp)

![Lắp đặt thiết bị - mặt trước](../images/phase3_physical_settings/illustrative02.webp)

> Màn hình và bàn phím chỉ cần trong quá trình setup ban đầu. Sau khi hoàn tất có thể tháo ra và quản lý từ xa qua SSH.

**Bước 4.** Cấp nguồn và đăng nhập trực tiếp

Cấp nguồn cho Jetson, chờ khoảng 1-2 phút để thiết bị khởi động hoàn tất. Đăng nhập trên màn hình trực tiếp:

```
avis-cam login: avis
Password: 1
```

> Thiết bị mới chưa có kết nối mạng nên chưa thể SSH. Phải thao tác trực tiếp qua màn hình + bàn phím.

**Bước 5.** Chạy master setup

```bash
sudo bash mini-pc/setup-firstboot/master-setup.sh --prompt-device-env --restart-all
```

Nhập password root (`1`), sau đó script sẽ hỏi 3 thông tin (lấy từ [Phase 1 - Bước 4](#các-bước-thực-hiện)):

```
DEVICE_ID: 671fb184-bcbf-4fe9-8459-a7a85b64f994
BACKEND_URL: https://avis-api-dev.aivis-camera.ai
SECRET_KEY (hidden): ••••••••••
```

Script tự động chạy:
1. **install-software.sh** — cài đặt packages, swap, go2rtc, cloudflared
2. **setup-services.sh --restart-all** — deploy files, enable và restart tất cả services

**Bước 6.** Kiểm tra trạng thái

```bash
sudo bash mini-pc/setup-firstboot/scripts/check-status.sh
```

Kết quả mong đợi — tất cả services **active** và phần cứng được phát hiện:

```
━━━ Services ━━━
  ✓  camera-stream         active      (enabled)
  ✓  go2rtc                active      (enabled)
  ✓  ai-core               active      (enabled)
  ✓  logic-service         active      (enabled)
  ✓  oobe-setup            active      (enabled)
  ✓  backchannel           active      (enabled)
  ✓  person-count-ws       active      (enabled)
  ✓  stream-auth           active      (enabled)
  ✓  device-update-server  active      (enabled)
  ✓  nginx                 active      (enabled)
  ✓  sim7600-4g            active      (enabled)
  ✓  network-watchdog      active      (enabled)
  ✓  cloudflared           active      (enabled)
  ✓  audio-autostart [user]  active      (enabled)

━━━ Cron Jobs ━━━
  ✓  sync-config.py             (*/5 * * * *, root)
  ✓  device-update.py           (*/5 * * * *, root)

━━━ CSI Camera ━━━
  ✓  nvargus-daemon          active
  ✓  video devices           /dev/video0
  ✓  AI shared memory        12441664 bytes

━━━ USB Audio ━━━
  ✓  PulseAudio              running
  ✓  Speaker                 alsa_output.usb-...-00.iec958-stereo (SUSPENDED)
  ✓  Microphone              alsa_input.usb-...-00.analog-stereo (RUNNING)
  ✓  Echo Cancel             loaded (module ...)
      echocancel_sink: SUSPENDED, echocancel_source: RUNNING

━━━ LTE Module (SIM7600) ━━━
  ✓  USB serial              /dev/ttyUSB0,...
  ✓  USB device              Bus 001 Device 006: ID 1e0e:9011 ...
  ✓  4G interface            usb2 — 169.254.x.x/16

━━━ Network ━━━
  Mode: auto
  ✓  wlP1p1s0               192.168.x.x/24
  Default route: default via 192.168.x.1 dev wlP1p1s0

━━━ Device Info ━━━
  Device ID:    671fb184-bcbf-4fe9-8459-a7a85b64f994
  Backend URL:  https://avis-api-dev.aivis-camera.ai
```

**Bước 7.** Đổi password

Sau khi xác nhận mọi thứ hoạt động, **bắt buộc** đổi password mặc định:

```bash
passwd
```

> Lưu lại password mới vào file quản lý thiết bị (sheet dự án hoặc password manager) kèm theo Device ID để có thể SSH xử lý từ xa khi cần.

### Xác nhận hoàn tất

Sau khi cài đặt thành công:

- `sync-config.py` tự động đồng bộ config từ backend mỗi 5 phút
- `device-update.py` gửi heartbeat (software version) mỗi 5 phút
- Trạng thái camera trên AIVIS Admin chuyển sang **Online**
- Live stream truy cập qua: `https://{device-id}.aivis-camera.ai`

---

## Quản lý services

### Deploy mà không restart

```bash
sudo ./setup-services.sh
```

### Deploy và restart tất cả

```bash
sudo ./setup-services.sh --restart-all
```

### Deploy và restart services cụ thể

```bash
sudo ./setup-services.sh network-watchdog go2rtc nginx
```

### Full setup (install + deploy) với restart

```bash
sudo ./master-setup.sh --restart-all
```

### Xem AI Detection (GUI)

Nếu cần xem trực tiếp kết quả detection trên màn hình (cần kết nối HDMI):

```bash
# Dừng service trước
sudo systemctl stop ai-core

# Chạy AI detection với GUI
python3 mini-pc/src/ai_core/main.py --device cuda
```

> Nhấn `Ctrl+C` để dừng. Sau đó khởi động lại service: `sudo systemctl start ai-core`