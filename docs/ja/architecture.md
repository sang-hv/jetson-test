# アーキテクチャ

## 概要

Mini PCは、NVIDIA Jetson Orin Nano上に構築されたAIエッジコンピューティングシステムです。CUDAアクセラレーションによるAIモデルを使用し、映像ストリームをローカルで処理することで、リアルタイムのセキュリティ監視、入退室管理、分析機能を提供します。

映像キャプチャ、AI推論、イベント処理、クラウド同期のすべてがエッジデバイス上で完結するため、遅延と帯域幅の使用を最小限に抑えます。

## ハードウェア

| コンポーネント | 説明 |
|-----------|-------------|
| NVIDIA Jetson Orin Nano | メインコンピュートボード（8GB RAM、CUDAコア搭載） |
| CSIカメラ（IMX219） | CSIリボンケーブル経由の映像入力 |
| USBマイク/スピーカー（Jabra） | エコーキャンセル対応の双方向音声 |
| SIM7600 LTEモジュール | USB 4Gモデム（セルラーフェイルオーバー用） |
| NVMe SSD（256GB） | `/data`にマウントされるプライマリストレージ |

## システムアーキテクチャ

```
                        ┌──────────────────────────────────────────────────┐
                        │              Jetson Nano / Orin Nano             │
                        │                                                  │
  CSI Camera ──────────►│ start-stream.py (GStreamer)                      │
  (IMX219)              │   ├─ Video: nvarguscamerasrc → H.264 → ─┐        │
                        │   ├─ Audio: echocancel_source → AAC →   ├───────►│ go2rtc :1984
                        │   │                              mpegtsmux       │   ├─ MSE
                        │   └─ AI: 5fps BGR → /dev/shm/ ─────────────────► │   ├─ WebRTC
                        │         (shared memory for ai_core)              │   └─ RTMP
                        │                                                  │
                        │ ai-core (systemd)                                │
                        │   ├─ YOLO detection + InsightFace recognition    │
                        │   ├─ Pipeline: home / shop / enterprise          │
                        │   ├─ ZMQ PUB tcp://127.0.0.1:5555 ─────────────► │ logic-service :8095
                        │   └─ .env: PIPELINE_TYPE set by sync-config      │   ├─ ZMQ SUB → events
                        │                                                  │   └─ AWS SQS sender
                        │                                                  │
  USB Mic/Speaker ─────►│ PulseAudio + echo cancel                         │
  (Jabra)               │   ├─ echocancel_source (mic in)                  │
                        │   └─ echocancel_sink (speaker out) ◄─────────────│
                        │                                                  │
  Browser/App ─────────►│ nginx :80 (reverse proxy)                        │
                        │   ├─ /api/*       → go2rtc :1984         [auth]  │
                        │   ├─ /backchannel → backchannel :8080    [auth]  │
                        │   ├─ /detections  → person-count :8090   [auth]  │
                        │   ├─ /detection/* → saved images         [auth]  │
                        │   └─ auth_request → stream-auth :8091            │
                        │       (query param token + cookie fallback)      │
                        │                                                  │
  Mobile (BLE) ────────►│ oobe-setup (systemd)                             │
                        │   ├─ BLE GATT server (BlueZ/D-Bus)               │
                        │   ├─ WiFi provisioning, network config           │
                        │   └─ PIN from DB (bluetooth_password)            │
                        │                                                  │
  Backend API ◄────────►│ sync-config.py (cron 5min)                       │
                        │   ├─ camera_settings, ai_rules                   │
                        │   ├─ detection_zones, face_embeddings            │
                        │   ├─ cloudflare tunnel token                     │
                        │   ├─ SQS credentials → logic-service .env        │
                        │   ├─ PIPELINE_TYPE → ai-core .env                │
                        │   └─ restart ai-core on facility/face change     │
                        │                                                  │
  Backend API ─────────►│ device-update-server :8092 (OTA)                 │
  (via tunnel)          │   └─ POST /update → run-update.sh                │
                        │       ├─ git fetch + checkout <tag>              │
                        │       ├─ setup-services.sh --restart-all         │
                        │       └─ callback ACK → backend API              │
                        │                                                  │
  SIM7600 4G ──────────►│ network-watchdog.sh                              │
  LAN / WiFi            │   └─ auto failover: LAN > WiFi > 4G              │
                        └──────────────────────────────────────────────────┘
```

## コアコンポーネント

### 1. 映像パイプライン

映像パイプラインはCSIカメラからフレームをキャプチャし、2つのコンシューマに配信します：

- **ストリーミング**: GStreamerがH.264映像 + AAC音声をMPEG-TSにエンコードし、go2rtcに転送。マルチプロトコル配信（MSE、WebRTC、RTMP）を実現します。
- **AI処理**: 生のBGRフレームを5fpsで共有メモリ（`/dev/shm/mini_pc_ai_frames.bin`）に書き込み、AIパイプラインが利用します。

共有メモリはダブルバッファリングプロトコルを使用し、64バイトのヘッダー（マジックナンバー、寸法、シーケンスカウンタ、アクティブスロット）によりロックフリーのプロデューサー・コンシューマー通信を実現します。

### 2. AI検出パイプライン（ai-core）

施設タイプに応じた3つのモードをサポートするCUDAアクセラレーション検出パイプライン：

| PIPELINE_TYPE | 施設タイプ | 機能 |
|---------------|----------|----------|
| `home` | 家庭 | ゾーンカウント、不審者/動物アラート、通行人検出 |
| `shop` | 店舗 | 基本検出 + 顔認識 |
| `enterprise` | 企業 | 完全検出 + 顔認識 + PPE/マスク違反アラート |

主な機能：
- **物体検出**: TensorRT最適化によるYOLO
- **顔認識**: InsightFaceによるID照合
- **PPE検出**: ヘルメット、手袋、マスクのコンプライアンスチェック（enterpriseモード）
- **ゾーンカウント**: ポリゴンベースの人数カウント（方向追跡付き）

検出イベントはZMQ（PUB/SUB）で`tcp://127.0.0.1:5555`を通じてパブリッシュされます。

### 3. ロジックサービス

AI検出をクラウドに橋渡しするイベント処理サービス：

- **ZMQサブスクライバー**: ai-coreからの検出イベントを受信
- **イベント処理**: SQLiteからルール（時間/曜日制約、メンバーフィルタ）を適用
- **SQS送信**: 条件を満たしたイベントをAWS SQSに転送し、バックエンドで処理
- **FastAPIサーバー**: ポート8095で内部クエリ用REST APIを公開

### 4. ストリーミングとアクセス制御

```
クライアントリクエスト → nginx :80
                          │
                          ├─ auth_request → stream-auth :8091
                          │   (クエリパラメータまたはCookieでHMAC-SHA256トークンを検証)
                          │
                          ├─ /api/* → go2rtc :1984 (映像ストリーミング)
                          ├─ /backchannel → backchannel :8080 (クライアント → スピーカー音声)
                          ├─ /detections → person-count-ws :8090 (ライブ検出フィード)
                          └─ /detection/* → 保存済み検出画像
```

トークン形式：ペイロード（`camera_id`、`time_exp`）とHMAC-SHA256署名を含むBase64urlエンコードされたJSON。iOS HLSはセグメントリクエストにCookieフォールバックを使用します。

### 5. バックエンド同期（sync-config.py）

5分間隔のcrontabで実行。同期内容：

| データ | 保存先 |
|------|---------|
| カメラ設定、AIルール、検出ゾーン | SQLite（`/data/mini-pc/db/logic_service.db`） |
| 顔埋め込みベクトル | SQLite（`updated_at`追跡によるページネーション同期） |
| SQS認証情報 | `/opt/logic_service/.env` |
| パイプラインタイプ | `/opt/ai_core/.env` |
| Cloudflareトンネルトークン | Cloudflaredサービス設定 |

設定変更が検出されると、影響を受けるサービスを自動的に再起動します。

### 6. ネットワーク

複数のネットワークインターフェースに対応し、自動フェイルオーバーをサポート：

| モード | 優先順位 |
|------|----------|
| `auto`（デフォルト） | LAN > WiFi > 4G |
| `lan` | LAN > 4G > WiFi |
| `wifi` | WiFi > 4G > LAN |
| `4g` | 4G > LAN > WiFi |

ネットワークウォッチドッグは設定可能なホスト（デフォルト：8.8.8.8）に30秒間隔でpingを送信し、3回連続失敗後に次の利用可能なインターフェースへフェイルオーバーを実行します。

### 7. OTAアップデート

バックエンドからCloudflareトンネル経由でリモートソフトウェアアップデートを実行：

1. バックエンドがターゲットバージョンとアップデートモードを含む`POST /update`を送信
2. デバイスが即座に`200 accepted`を返答
3. バックグラウンドで処理：`git fetch` → `git checkout <tag>` → サービス再デプロイ
4. デバイスが結果をバックエンドにACK（成功/失敗とバージョン情報）
5. 1時間以内にACKがない場合、バックエンドがアップデートを失敗としてマーク

### 8. BLE OOBE（初期セットアップ）

モバイルアプリからの初期デバイスプロビジョニング用BLE GATTサーバー：

- NetworkManagerによるWiFiネットワーク設定
- ネットワークモード選択
- PINで保護されたペアリング（SQLiteからPIN取得、フォールバック：`123456`）
- 10分間無操作で自動シャットダウン

## サービス一覧

| サービス | ポート | 説明 |
|---------|------|-------------|
| camera-stream | - | CSIカメラ → MPEG-TSストリーム + AI共有メモリ |
| go2rtc | 1984 | マルチプロトコルストリーミングサーバー（MSE、WebRTC、RTMP） |
| ai-core | - | AI検出パイプライン（YOLO + InsightFace、CUDA） |
| logic-service | 8095 | ZMQイベント受信 + SQS送信 + FastAPI |
| oobe-setup | - | BLE GATTサーバー（WiFi/ネットワークプロビジョニング） |
| backchannel | 8080 | ブラウザ音声 → デバイススピーカー（WebSocket） |
| person-count-ws | 8090 | AI検出 → WebSocketブロードキャスト |
| stream-auth | 8091 | nginx用HMACトークン検証 |
| device-update-server | 8092 | OTAアップデートエンドポイント |
| nginx | 80 | リバースプロキシ + 認証ルーティング |
| sim7600-4g | - | 4Gモデム初期化 |
| network-watchdog | - | 接続監視 + フェイルオーバー |
| audio-autostart | - | PulseAudio + エコーキャンセル設定 |
| cloudflared | - | バックエンドへのCloudflareトンネル |
| cleanup-detections | - | 古い検出画像/ログの定期クリーンアップ |

### サービス依存関係

```
camera-stream
  ├─► go2rtc (After, Wants)
  ├─► ExecStartPre: setup-audio-autostart.sh
  └─► start-stream.py

ai-core (After: camera-stream)
  ├─► /dev/shmからBGRフレームを読み取り
  └─► ZMQ PUB tcp://127.0.0.1:5555

logic-service (After: network.target)
  ├─► ai-core :5555からZMQ SUB
  └─► AWS SQSにイベント送信

device-update-server
  └─► stream-auth (After, Wants)

sim7600-4g ──► network-watchdog (Wants, 並列起動)
```

## データフロー

```
CSIカメラ
    │
    ▼
start-stream.py (GStreamer)
    │
    ├──── H.264 + AAC (MPEG-TS) ──────► go2rtc ──────► ブラウザ/アプリ
    │                                                    (MSE/WebRTC)
    └──── BGRフレーム (5fps) ──────► /dev/shm
                                       │
                                       ▼
                                   ai-core (CUDA)
                                       │
                                       ├── YOLO検出
                                       ├── InsightFace顔認識
                                       └── ZMQ PUB :5555
                                              │
                                              ▼
                                       logic-service
                                              │
                                              ├── ルール適用（時間、ゾーン、メンバー）
                                              ├── 検出画像を保存
                                              └── AWS SQSに送信
                                                      │
                                                      ▼
                                                  バックエンドAPI
```

## ファイルシステムレイアウト

```
/etc/device/
├── device.env           # DEVICE_ID, BACKEND_URL, SECRET_KEY
├── network.conf         # NETWORK_MODE, APN, PING_HOST, CHECK_INTERVAL
├── repo-path            # gitリポジトリパス（OTA用）
├── config.json          # 最新の同期済みバックエンド設定
└── config.prev.json     # 前回の設定（差分比較用）

/opt/
├── stream/              # カメラパイプライン
├── ai_core/             # AI検出パイプライン
├── logic_service/       # ロジックサービス
├── oobe-setup/          # BLE OOBE
├── backchannel/         # 音声バックチャネル
├── person_count_ws/     # ZMQ → WebSocketブリッジ
├── stream_auth/         # トークンバリデータ
├── device_update/       # OTAアップデートサーバー
├── device/              # sync-config.py, device-update.py
├── 4g/                  # ネットワークスクリプト
└── audio/               # 音声設定スクリプト

/data/mini-pc/
├── db/                  # SQLiteデータベース
├── media/               # 検出画像
├── faces/               # 顔画像クロップ
├── logs/                # アプリケーションログ
└── models/              # MLモデル
```

## 認証

nginxでプロキシされるすべてのルートにはトークンベースの認証が必要です：

- **トークン**: `camera_id`、`time_exp`、HMAC-SHA256署名を含むBase64urlエンコードされたJSON
- **送信方法**: クエリパラメータ（`?token=<base64url>`）または`stream_token` Cookie
- **検証**: ポート8091のstream-authサービス（nginx `auth_request`）
- **iOSサポート**: クエリパラメータを落とすHLSセグメントリクエスト用のCookieフォールバック

## 診断（check-status.sh）

すべてのサービスとハードウェアのヘルスチェックを一括実行するコマンド：

```bash
sudo ./setup-firstboot/scripts/check-status.sh
```

レポート内容：

| セクション | チェック内容 |
|---------|---------------|
| サービス | 13のsystemdサービス + audio-autostart（ユーザー）のactive/enabled状態 |
| Cronジョブ | sync-config.py、device-update.pyのcrontab登録状況 |
| CSIカメラ | nvargus-daemonの状態、/dev/video*デバイス、AI共有メモリファイル |
| USBオーディオ | PulseAudioの状態、USBスピーカー（sink）、USBマイク（source）、エコーキャンセルモジュール（ロード状況、正しいデバイスに接続されているか） |
| LTEモジュール | /dev/ttyUSB*ポート、lsusb検出、ModemManagerモデム状態 + 信号、4Gインターフェース + IP |
| ネットワーク | ネットワークモード、アクティブなインターフェースとIP、デフォルトルート |
| デバイス情報 | デバイスID、バックエンドURL、ソフトウェアバージョン（gitタグ） |

