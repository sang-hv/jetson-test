# Setup Guide

The AIVIS Camera device setup consists of 3 main phases (+ 1 optional):

| Phase | Description | Performed on |
|-------|-------------|--------------|
| [Phase 1](#phase-1-create-camera-on-aivis-admin) | Create camera record on AIVIS Admin | AIVIS Admin Web |
| [Phase 2](#phase-2-create-cloudflare-tunnel) | Create Cloudflare Tunnel and update domain/token | Cloudflare Dashboard + AIVIS Admin |
| [Phase 3](#phase-3-install-and-configure-jetson-device) | Install software on Jetson | Jetson Orin Nano (direct access) |
| [Phase 4](#phase-4-clone-image-if-needed) *(optional)* | Clone NVMe to a reusable golden image | Jetson Orin Nano |

---

## Phase 1: Create Camera on AIVIS Admin

### Prerequisites

- Download link for image, backend domain, default account credentials, etc. are stored in the project sheet. Contact the project manager if you don't have access.
- Serial Number of the Jetson Orin Nano (printed on the label on the bottom of the device)

![Jetson Serial Number](../images/phase1/jetson_nano_serial_number.webp)

### Steps

**Step 1.** Log in to AIVIS Admin

![Login AIVIS Admin](../images/phase1/step1_admin_login_page.png)

**Step 2.** Go to **Cameras** menu > click **+ Add New Camera**

![Click create new camera](../images/phase1/step2_admin_click_create_new_camera.png)

**Step 3.** Fill in camera information

1. Enter **Camera Name** and **Serial Number** (from the Jetson label)
2. Click **Create**

> Other fields (Installation Location, Domain Name, Cloudflare Tunnel Token, Facility Type, ...) can be filled in later.

![Create new camera](../images/phase1/step3_admin_create_camera.png)

**Step 4.** Get Device Info

After creation, go to the camera detail page > click **Device Info**. The popup shows 3 values needed for [Phase 3](#phase-3-install-and-configure-jetson-device):

- **Device ID** — Camera UUID
- **Backend URL** — API backend URL
- **Secret Key** — HMAC signing key for authentication

![Copy Device ID](../images/phase1/step4_admin_copy_device_id_to_clipboard.png)

> Click the copy icon next to each field to copy the value.

---

## Phase 2: Create Cloudflare Tunnel

Cloudflare Tunnel allows the backend to access the Jetson device remotely (OTA update, live stream, SSH) without requiring a public IP or port forwarding.

> Download link for image, backend domain, default account credentials, etc. are stored in the project sheet. Contact the project manager if you don't have access.

Each camera requires **1 tunnel** with **2 public hostnames**:
- `{device-id}.aivis-camera.ai` — HTTP access (stream, API, OTA)
- `{device-id}-ssh.aivis-camera.ai` — SSH access

### Steps

**Step 1.** Log in to [Cloudflare Dashboard](https://dash.cloudflare.com)

![Cloudflare Login](../images/phase2/step1_cloudflare_login_page.png)

**Step 2.** Select **Zero Trust** in the left sidebar

![Click Zero Trust](../images/phase2/step2_cloudflare_click_zero_trust.png)

**Step 3.** Go to **Connectors** > **Cloudflare Tunnels** > click **Create a tunnel**

![Create tunnel](../images/phase2/step3_cloudflare_create_a_tunnel.png)

**Step 4.** Select **Cloudflared** > click **Select Cloudflared**

![Select Cloudflared](../images/phase2/step4_cloudflare_select_cloudflared.png)

**Step 5.** Name the tunnel

1. Enter tunnel name in format: `Camera {device-id}` (e.g., `Camera 671fb184-bcbf-4fe9-8459-a7a85b64f994`)
2. Click **Save tunnel**

![Name the tunnel](../images/phase2/step5_cloudflare_enter_tunnel_name.png)

**Step 6.** Skip the "Install and run a connector" page > click **Next**

> No need to run the install command at this step. Cloudflared will be installed automatically on Jetson by `install-software.sh`.

![Click Next](../images/phase2/step6_cloudflare_click_next.png)

**Step 7.** Add Public Hostname for **HTTP** (stream/API)

1. **Subdomain**: `{device-id}` (e.g., `671fb184-bcbf-4fe9-8459-a7a85b64f994`)
2. **Domain**: `aivis-camera.ai`
3. **Type**: `HTTP`
4. **URL**: `localhost`
5. Click **Complete setup**

![Add stream domain](../images/phase2/step7_cloudflare_enter_domain_stream_for_camera.png)

**Step 8.** Go back to the Tunnels list > click **Configure** on the newly created tunnel

![Click Configure](../images/phase2/step8_cloudflare_click_config_camera_after_created.png)

**Step 9.** Select the **Published application routes** tab > click **+ Add a published application route**

![Add new route](../images/phase2/step9_cloudflare_click_add_a_published_application_route_button.png)

**Step 10.** Add Public Hostname for **SSH**

1. **Subdomain**: `{device-id}-ssh` (e.g., `671fb184-bcbf-4fe9-8459-a7a85b64f994-ssh`)
2. **Domain**: `aivis-camera.ai`
3. **Type**: `SSH`
4. **URL**: `localhost:22`
5. Click **Save**

![Add SSH domain](../images/phase2/step10_cloudflare_enter_domain_ssh_for_camera.png)

**Step 11.** Select the **Overview** tab > in the **Connectors** section, click **Add a connector**

![Add connector](../images/phase2/step11_cloudflare_click_add_a_connector_button.png)

**Step 12.** Copy the **Tunnel Token**

In the "Install and run a connector" popup, copy the token from the command `cloudflared tunnel run --token eyJhI...`

> Only copy the token part (starting with `eyJ...`), not the entire command.

![Copy tunnel token](../images/phase2/step12_cloudflare_copy_tunnel_token.png)

**Step 13.** Go back to AIVIS Admin > go to camera detail page > click **Edit**

![Edit camera](../images/phase2/step13_admin_edit_current_camera.png)

**Step 14.** Click the lock icon next to the **Domain Name** and **Cloudflare Tunnel Token** fields to unlock editing

![Unlock input](../images/phase2/step14_admin_click_icon_for_enable_domain_and_token_input.png)

**Step 15.** Fill in Domain Name and Tunnel Token

1. **Domain Name**: copy from the Published application routes tab on Cloudflare (HTTP hostname, e.g., `671fb184-bcbf-4fe9-8459-a7a85b64f994.aivis-camera.ai`)

   ![Get domain from Cloudflare](../images/phase2/step15_get_domain_from_cloudflare_tab.png)

2. **Cloudflare Tunnel Token**: paste the token copied in step 12
3. Click **Save**

![Enter domain and token](../images/phase2/step15_admin_enter_domain_and_token.png)

---

## Phase 3: Install and Configure Jetson Device

### Requirements

- NVMe SSD 256GB (new or wiped)
- System image file (download link available in the project sheet)
- SSD write adapter (USB-to-NVMe or a computer with M.2 slot)
- Device Info from [Phase 1 - Step 4](#steps): Device ID, Backend URL, Secret Key

### Steps

**Step 1.** Download the system image

Download the system image from the following link:

[jetson-base.img.gz](https://dehasoftvn-my.sharepoint.com/:u:/r/personal/administrator_deha-soft_com/Documents/DEHA%20group/02.%20BOM/02.%20Head%20Office/01.%20JMU/05.%20JMU%20-%20Projects%20(B%E1%BA%A3o%20m%E1%BA%ADt)/TIMIMA01/05%20-%20Release/%E7%B4%8D%E5%93%81%E7%89%A9/Jetson%20Orin%20Nano%E7%94%A8%20%E3%82%BB%E3%83%83%E3%83%88%E3%82%A2%E3%83%83%E3%83%97%E3%83%95%E3%82%A1%E3%82%A4%E3%83%AB/jetson-base.img.gz?csf=1&web=1&e=kbVSfc)

> Contact the project manager if you don't have access to the link above.

**Step 2.** Write the image to the SSD

Connect the SSD to your computer via adapter, then write the image:

```bash
# From compressed file (.img.gz)
pigz -dc jetson-base.img.gz | sudo dd of=/dev/<ssd-device> bs=4M status=progress
```

> Replace `<ssd-device>` with the actual SSD device name (e.g., `sda`, `nvme0n1`). Check with `lsblk` before writing.

**Step 3.** Assemble the hardware

Insert the SSD into the Jetson Orin Nano and connect all peripherals:

| Device | Connection |
|--------|-----------|
| NVMe SSD 256GB | M.2 slot on board |
| CSI Camera (IMX219) | CSI port via ribbon cable |
| USB Mic/Speaker | USB port |
| SIM7600 LTE Module | USB port (if 4G needed) |
| Power supply (9-19V 5A) | DC barrel jack |
| LAN cable (recommended for initial setup) | Ethernet port |
| Monitor (HDMI) | HDMI port (required for initial setup) |
| USB keyboard | USB port (required for initial setup) |

![Hardware assembly - top view](../images/phase3_physical_settings/illustrative01.webp)

![Hardware assembly - front view](../images/phase3_physical_settings/illustrative02.webp)

> Monitor and keyboard are only needed during initial setup. They can be removed after setup is complete and the device can be managed remotely via SSH.

**Step 4.** Power on and log in directly

Power on the Jetson, wait about 1-2 minutes for it to boot. Log in on the connected monitor:

```
avis-cam login: avis
Password: 1
```

> The new device has no network connection yet, so SSH is not available. You must operate directly via monitor + keyboard.

**Step 5.** Connect to the network (REQUIRED before master setup)

A cloned image does not carry WiFi credentials, so a freshly flashed device has no Internet access. Cloudflared needs Internet to bootstrap the tunnel — skipping this step will leave `cloudflared.service` in a failed state.

**Option 1 — Plug in a LAN cable** (simplest): connect the Ethernet cable, the network is configured automatically. Skip the rest of this step and continue to step 6.

**Option 2 — Connect via WiFi from the GUI** (the monitor and keyboard from step 3 are already attached):

1. Click the network icon in the top-right corner of the screen (Network Manager)
2. Select the WiFi SSID, enter the password
3. Wait until the icon shows a successful connection
4. Open a Terminal (`Ctrl + Alt + T`) and run the following command to make WiFi the preferred interface:

   ```bash
   sudo bash /opt/4g/switch-network.sh wifi
   ```

**Option 3 — Use 4G** (only if a SIM7600 module is attached and a SIM is inserted):

```bash
sudo bash /opt/4g/switch-network.sh 4g
```

Verify Internet connectivity:

```bash
ping -c 3 8.8.8.8
```

If the ping succeeds, continue to step 6.

**Step 6.** Run master setup

```bash
sudo bash mini-pc/setup-firstboot/master-setup.sh --prompt-device-env --restart-all
```

Enter the root password (`1`), then the script will prompt for 3 values (from [Phase 1 - Step 4](#steps)):

```
DEVICE_ID: 671fb184-bcbf-4fe9-8459-a7a85b64f994
BACKEND_URL: https://avis-api-dev.aivis-camera.ai
SECRET_KEY (hidden): ••••••••••
```

The script automatically runs:
1. **install-software.sh** — installs packages, swap, go2rtc, cloudflared
2. **setup-services.sh --restart-all** — deploys files, enables and restarts all services

**Step 7.** Check status

```bash
sudo bash mini-pc/setup-firstboot/scripts/check-status.sh
```

Expected result — all services **active** and hardware detected:

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

**Step 8.** Change the password

After confirming everything works, **you must** change the default password:

```bash
passwd
```

> Save the new password in the device management file (project sheet or password manager) along with the Device ID for remote SSH access when needed.

### Verification

After successful setup:

- `sync-config.py` automatically syncs config from backend every 5 minutes
- `device-update.py` sends heartbeat (software version) every 5 minutes
- Camera status on AIVIS Admin changes to **Online**
- Live stream accessible at: `https://{device-id}.aivis-camera.ai`

---

## Phase 4: Clone Image (if needed)

Use this phase when you want to capture a "golden" image from a fully configured Jetson and reuse it to flash other devices. The flow wipes device-specific identity/state, zeroes free space (so `pigz` can compress better), then clones the NVMe to a compressed file on an external USB SSD.

> Run all commands directly on the Jetson (via monitor + keyboard or SSH). All steps require `sudo`.

### Requirements

- USB SSD large enough to hold the compressed image (recommended ≥ 256GB)
- Jetson booted from the NVMe you want to clone
- All setup work already finished (services healthy via `check-status.sh`)

### Steps

**Step 1.** Stop all services

```bash
sudo systemctl stop ai-core logic-service person-count-ws backchannel \
                    stream-auth device-update-server cloudflared \
                    go2rtc camera-stream 2>/dev/null
```

**Step 2.** Wipe device identity & cloudflared state

```bash
sudo rm -f /etc/device/device.env /etc/device/config.json /etc/device/config.prev.json
sudo systemctl stop cloudflared 2>/dev/null
sudo cloudflared service uninstall 2>/dev/null
sudo rm -f /etc/systemd/system/cloudflared.service
sudo rm -rf /etc/cloudflared /var/lib/cloudflared /root/.cloudflared
```

> Removes `DEVICE_ID`, `BACKEND_URL`, `SECRET_KEY`, and the cloudflared tunnel credentials so the cloned image becomes a generic base. Each new device must be re-provisioned in [Phase 1](#phase-1-create-camera-on-aivis-admin) and [Phase 2](#phase-2-create-cloudflare-tunnel).

**Step 3.** Wipe runtime data

```bash
sudo rm -rf /data/mini-pc/db/* /data/mini-pc/media/* /data/mini-pc/faces/* /data/mini-pc/logs/*
sudo rm -rf /detection/* /dev/shm/mini_pc_ai_frames.bin
```

**Step 4.** Clear logs & journal

```bash
sudo journalctl --rotate && sudo journalctl --vacuum-time=1s
sudo rm -rf /var/log/*.log /var/log/*.gz /var/log/*.[0-9] /var/log/journal/*
```

**Step 5.** Zero out free space (improves `pigz` compression by 30–60%)

```bash
sudo dd if=/dev/zero of=/zero.fill bs=4M status=progress 2>/dev/null; sudo rm -f /zero.fill
sync; sync
```

**Step 6.** Mount the destination USB SSD

Plug in the USB SSD, then format and mount it. **Pick the correct device name** with `lsblk` first (example below uses `sda1`).

```bash
sudo mkdir -p /mnt/backup
sudo mkfs.ext4 -F /dev/sda1
sudo mount /dev/sda1 /mnt/backup
```

> `mkfs.ext4 -F` will erase everything on `/dev/sda1`. Double-check the device name to avoid wiping the wrong disk.

**Step 7.** Clone NVMe to a compressed file on the USB SSD

**Pick the correct OS disk name** with `lsblk` (typically `nvme0n1`):

```bash
sudo bash -c "dd if=/dev/nvme0n1 bs=4M status=progress | pigz > /mnt/backup/jetson-base.img.gz"
```

**Step 8.** Verify the image size

```bash
ls -lh /mnt/backup/jetson-base.img.gz
```

**Step 9.** Unmount the USB SSD

```bash
sudo umount /mnt/backup
```

The resulting `jetson-base.img.gz` can be flashed onto a new SSD via the same command shown in [Phase 3 - Step 2](#steps-2).

---

## Service Management

### Deploy without restart

```bash
sudo ./setup-services.sh
```

### Deploy and restart all

```bash
sudo ./setup-services.sh --restart-all
```

### Deploy and restart specific services

```bash
sudo ./setup-services.sh network-watchdog go2rtc nginx
```

### Full setup (install + deploy) with restart

```bash
sudo ./master-setup.sh --restart-all
```

### View AI Detection (GUI)

To view detection results directly on a monitor (HDMI connection required):

```bash
# Stop the service first
sudo systemctl stop ai-core

# Run AI detection with GUI
python3 mini-pc/src/ai_core/main.py --device cuda
```

> Press `Ctrl+C` to stop. Then restart the service: `sudo systemctl start ai-core`
