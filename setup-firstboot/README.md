# Jetson Nano Setup - Production Package

Bộ cài đặt tự động cho Jetson Nano: livestream video/audio + backchannel + echo cancel.

## Cấu trúc

```
jetson-nano-setup-final/
├── master-setup.sh              ← Chạy 1 lần, cài toàn bộ
├── config/
│   ├── go2rtc.yaml              ← go2rtc config
│   └── nginx.conf               ← Nginx reverse proxy
├── scripts/
│   ├── start-stream.sh          ← GStreamer pipeline (auto-detect camera/mic)
│   └── setup-audio-autostart.sh ← PulseAudio auto-config on boot
├── services/
│   ├── go2rtc.service           ← go2rtc systemd service
│   ├── backchannel.service      ← Backchannel systemd service
│   ├── cloudflared.service      ← Cloudflare tunnel service
│   └── audio-autostart.service  ← Audio autostart user service
├── backchannel/
│   ├── server.py                ← WebSocket audio server (FFmpeg→pacat)
│   ├── start.sh                 ← PulseAudio wrapper
│   └── demo.html                ← Browser test page
└── README.md
```

## Cài đặt (1 lệnh)

```bash
# 1. Copy thư mục lên Jetson
scp -r jetson-nano-setup-final/ user@jetson-ip:/home/user/setup/

# 2. SSH vào Jetson và chạy
ssh user@jetson-ip
cd /home/user/setup
chmod +x master-setup.sh
sudo ./master-setup.sh

# 3. Sửa TURN credentials
sudo nano /etc/go2rtc/go2rtc.yaml

# 4. Reboot
sudo reboot
```

## Sau khi cài

| Service | Port | Chức năng |
|---------|------|-----------|
| go2rtc | 1984 | Video/Audio streaming |
| backchannel | 8080 | Audio từ client → speaker |
| nginx | 80 | Reverse proxy |
| cloudflared | - | Cloudflare tunnel |
| audio-autostart | - | Auto PulseAudio + echo cancel |

## Kiểm tra

```bash
# Services
sudo systemctl status go2rtc backchannel nginx cloudflared
systemctl --user status audio-autostart

# Audio
pactl list short sinks | grep -i "jabra\|echocancel"
pactl list short sources | grep -i "jabra\|echocancel"

# Logs
sudo journalctl -u go2rtc -f
sudo journalctl -u backchannel -f
```

## Pipeline

```
USB Camera (MJPEG) → jpegdec → videoconvert → x264enc → ┐
USB Mic (PulseAudio echocancel) → voaacenc →              ├→ mpegtsmux → go2rtc
                                                          ┘

Client audio → WebSocket → FFmpeg (decode) → pacat → PulseAudio (echocancel_sink) → Speaker
```
