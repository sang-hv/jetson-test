# セットアップ

## 前提条件

- JetPack OSがフラッシュ済みのNVIDIA Jetson Orin Nano
- リボンケーブルで接続されたCSIカメラ（IMX219）
- ネットワークアクセス（LAN、WiFi、または4G用SIMカード）
- NVMe SSD（推奨）またはSDカード（`/data`ストレージ用）

## クイックスタート

```bash
# 1. リポジトリをJetsonにコピー
scp -r . user@jetson-ip:/home/user/mini-pc/

# 2. デバイスにSSH接続
ssh user@jetson-ip

# 3. マスターセットアップを実行
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh

# 4. 再起動
sudo reboot
```

新規またはクローンデバイスの場合、`--prompt-device-env`でデバイスIDを設定します：

```bash
sudo ./master-setup.sh --prompt-device-env
```

## インストールフェーズ

`master-setup.sh`は2つのフェーズを順番に実行します：

### フェーズ1: install-software.sh

システムレベルの依存関係をインストール：

- APTパッケージ（GStreamerプラグイン、Nginx、Pythonなど）
- スワップ設定（8GB）
- go2rtcストリーミングサーバー
- Cloudflaredトンネルクライアント

### フェーズ2: setup-services.sh

アプリケーションファイルのデプロイとすべてのサービスの有効化：

| ステップ | 説明 |
|---------|-------------|
| 1/11 | go2rtcストリームサービス |
| 2/11 | デバイスIDと同期スクリプト |
| 3/11 | Cloudflaredサービスチェック |
| 4/11 | バックチャネル、人数カウントWebSocket、ストリーム認証 |
| 5/11 | デバイスOTAアップデートサーバー |
| 6/11 | Nginxリバースプロキシ |
| 7/11 | 音声自動起動 |
| 8/11 | SIM7600 4Gスクリプト/サービス |
| 9/11 | OOBE BLEセットアップ |
| 10/11 | ロジックサービス（ZMQ + FastAPI） |
| 11/11 | AI Core検出パイプライン |

## デバイスID

デバイスIDは`/etc/device/device.env`に保存され、`sync-config.py`と`device-update.py`がバックエンドとの通信に使用します。

| 変数 | 説明 |
|----------|-------------|
| `DEVICE_ID` | バックエンドから取得した一意のカメラUUID |
| `BACKEND_URL` | APIベースURL |
| `SECRET_KEY` | API認証用のHMAC署名キー |

`--prompt-device-env`で対話的に設定するか、`/etc/device/device.env`を直接編集します。

## サービス管理

`master-setup.sh`と`setup-services.sh`の両方がリスタートフラグをサポートしています。すべてのコマンドは最初にデプロイと有効化を行い、引数に基づいてリスタートを実行します。

### デプロイのみ（リスタートなし）

```bash
sudo ./setup-services.sh
```

### デプロイ + 全サービスリスタート

```bash
sudo ./setup-services.sh --restart-all
```

### デプロイ + 特定サービスのリスタート

```bash
sudo ./setup-services.sh network-watchdog go2rtc nginx
```

### フルセットアップ（インストール + デプロイ）+ リスタート

```bash
sudo ./master-setup.sh --restart-all
sudo ./master-setup.sh network-watchdog go2rtc
```

無効なサービス名を指定すると、エラーメッセージと有効なサービスの一覧が表示されます。

## ネットワーク設定

### ネットワークモード

設定ファイル: `/etc/device/network.conf`

| モード | 優先順位 | 説明 |
|------|----------|-------------|
| `auto` | LAN > WiFi > 4G | デフォルト、利用可能な最良の接続を使用 |
| `lan` | LAN > 4G > WiFi | 有線接続を優先 |
| `wifi` | WiFi > 4G > LAN | 無線接続を優先 |
| `4g` | 4G > LAN > WiFi | セルラー接続を強制 |

### ネットワークモードの切り替え

```bash
sudo /opt/4g/switch-network.sh auto   # または: 4g, lan, wifi
```

### ウォッチドッグ設定

`/etc/device/network.conf`で設定：

| 設定 | デフォルト | 説明 |
|---------|---------|-------------|
| `PING_HOST` | 8.8.8.8 | 接続チェック用のpingホスト |
| `CHECK_INTERVAL` | 30 | チェック間隔（秒） |
| `MAX_RETRIES` | 3 | フェイルオーバーまでの失敗回数 |
| `APN` | （キャリア） | SIM7600 LTEモジュールのAPN |

## OTAソフトウェアアップデート

バックエンドAPI経由でリモートソフトウェアアップデートが可能です。

### アップデートフロー

```
モバイルアプリ                 バックエンドAPI                    Jetson
    │                              │                              │
    ├─ POST /cameras/{id}/update ─►│                              │
    │   { version, run_install }   │                              │
    │                              ├─ POST /update (トンネル) ───►│
    │                              │   (HMAC認証)                 ├─ 200 accepted
    │                              │                              ├─ git fetch + checkout
    │                              │                              ├─ サービス再デプロイ
    │                              │◄─ PATCH /update-logs/ack ───┤
    │◄── update_logsクエリ ────────┤                              │
```

### アップデートパラメータ

| パラメータ | 説明 |
|-----------|-------------|
| `version` | チェックアウトするgitタグまたはブランチ |
| `run_install` | `true` = フルインストール（`master-setup.sh`）、`false` = デプロイのみ（`setup-services.sh`） |
| `update_log_id` | ACK追跡用のバックエンドUUID |

### アップデートプロセス（デバイス上）

1. ロック取得（`/tmp/device-update.lock`）
2. `git fetch origin`
3. `git checkout <version>`
4. `setup-services.sh --restart-all`を実行（`run_install=true`の場合は`master-setup.sh --restart-all`）
5. ヘルスチェック：コアサービスの動作確認
6. バックエンドに結果をACK（成功/失敗）
7. ロック解放

### バージョン追跡

デバイスは`software_version`（`git describe --tags`から取得）を5分ごとのハートビート（`device-update.py`）で報告します。バックエンドは`cameras.software_version`に保存します。

## システムクローニング

### 1. ディスクイメージの作成（稼働中のデバイス上）

```bash
# NVMe全体をUSBドライブにクローン
sudo dd if=/dev/nvme0n1 of=/media/usb/jetson-image.img bs=4M status=progress

# 圧縮版（容量60-70%削減）
sudo dd if=/dev/nvme0n1 bs=4M status=progress | gzip > /media/usb/jetson-image.img.gz
```

### 2. 新しいデバイスへの復元

```bash
# 生イメージから
sudo dd if=jetson-image.img of=/dev/nvme0n1 bs=4M status=progress

# 圧縮イメージから
gunzip -c jetson-image.img.gz | sudo dd of=/dev/nvme0n1 bs=4M status=progress
```

### 3. デバイスIDの再プロビジョニング

```bash
cd /home/user/mini-pc/setup-firstboot
sudo ./master-setup.sh --prompt-device-env
# 新しいDEVICE_ID、BACKEND_URL、SECRET_KEYを入力
sudo reboot
```

再起動後、`sync-config.py`が自動的に新しいデバイスの設定をバックエンドから取得します。

## バックエンド同期

`sync-config.py`は5分間隔のcrontabで実行されます。

### APIコール

| API | 目的 |
|-----|---------|
| `GET /api/v1/cameras/{id}/config` | 設定、ルール、ゾーン、Cloudflareトークン、SQS設定の同期 |
| `GET /api/v1/cameras/{id}/face-embeddings` | ページネーション対応の顔埋め込みベクトル同期 |

認証ヘッダー: `X-Device-ID`、`X-Timestamp`、`X-Signature`（HMAC-SHA256）。

### 同期データ（SQLite）

データベース: `/data/mini-pc/db/logic_service.db`

| テーブル | 内容 |
|-------|---------|
| `camera_settings` | stream_secret_key, stream_view_duration, bluetooth_password, facility, ai_threshold, image_retention_days |
| `ai_rules` | 検出ルール：名前、コード、メンバーID、時間/曜日制約 |
| `detection_zones` | 座標（JSON）付きポリゴン、方向ポイント |
| `face_embeddings` | ユーザーの顔ベクトル（`updated_at`追跡によるページネーション同期） |

### 環境ファイルの同期

| 対象 | キー | ソース |
|--------|------|--------|
| `/opt/logic_service/.env` | AWS_SQS_REGION, AWS_SQS_QUEUE_URL, AWS_SQS_ACCESS_KEY_ID, AWS_SQS_SECRET_ACCESS_KEY | APIレスポンス |
| `/opt/ai_core/.env` | PIPELINE_TYPE | facilityからマッピング：Family→home, Store→shop, Enterprise→enterprise |

### 自動リスタートトリガー

| サービス | リスタート条件 |
|---------|-------------|
| `logic-service` | `.env`のSQS認証情報が変更された場合 |
| `ai-core` | `PIPELINE_TYPE`が変更された場合、または`face_embeddings.updated_at`が変更された場合 |
| `cloudflared` | トンネルトークンが変更された場合 |

## 診断

### 全サービスのステータス確認

```bash
sudo systemctl status camera-stream go2rtc ai-core logic-service oobe-setup \
  backchannel person-count-ws stream-auth device-update-server nginx \
  sim7600-4g network-watchdog cloudflared
systemctl --user status audio-autostart
```

### ログの確認

```bash
sudo journalctl -u camera-stream -f
sudo journalctl -u ai-core -f
sudo journalctl -u logic-service -f
sudo journalctl -u network-watchdog -f
sudo journalctl -u device-update-server -f
```

### 音声チェック

```bash
pactl list short sinks | grep -i "jabra\|echocancel"
pactl list short sources | grep -i "jabra\|echocancel"
# USBオーディオを再接続した場合：
systemctl --user restart audio-autostart
```

### ネットワークチェック

```bash
ip route show
cat /etc/device/network.conf
cat /run/4g-interface
mmcli -L
```

### OTAチェック

```bash
curl http://127.0.0.1:8092/health
cd $(cat /etc/device/repo-path) && git describe --tags --always
```

### トークン検証

```bash
python3 /opt/stream_auth/check_token.py <token>
```

## トラブルシューティング

| 症状 | 確認方法 | 対処法 |
|---------|-------|-----|
| 映像ストリームなし | `journalctl -u camera-stream` | CSIリボンケーブルを確認、`nvargus-daemon`を再起動 |
| 音声なし | `pactl list short sinks` | USBオーディオを再接続、`audio-autostart`を再起動 |
| AIが検出しない | `journalctl -u ai-core` | `.env`のPIPELINE_TYPEを確認、TensorRTエンジンの存在を確認 |
| ロジックサービスエラー | `journalctl -u logic-service` | `.env`のSQS認証情報を確認、ZMQポート5555を確認 |
| BLE OOBEが動作しない | `journalctl -u oobe-setup` | `bluetoothctl show`を確認、oobe-setupを再起動 |
| 4G接続不可 | `mmcli -L`、`journalctl -u sim7600-4g` | SIMを確認、`network.conf`のAPNを確認 |
| ストリーム認証401 | `check_token.py <token>` | トークン有効期限を確認、DBの`stream_secret_key`を確認 |
| iOSストリーム失敗 | `stream_token` Cookieを確認 | nginxがCookieを設定し、stream-authが読み取ることを確認 |
| 設定が同期されない | `cat /etc/device/device.env` | BACKEND_URLの到達性を確認、SECRET_KEYを確認 |
| OTAアップデート失敗 | `journalctl -u device-update-server` | gitアクセス、ディスク容量、サービスヘルスを確認 |
| ネットワークが頻繁に切り替わる | `journalctl -u network-watchdog` | `network.conf`の`CHECK_INTERVAL`/`MAX_RETRIES`を調整 |
