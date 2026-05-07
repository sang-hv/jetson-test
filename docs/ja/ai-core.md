# AI Core セットアップ

AI Core パイプラインの動作確認と、Jetson Orin Nano 上での推論を高速化するために YOLO モデルを `.pt` から `.engine`（TensorRT）形式へエクスポートする手順を説明します。

| 項目 | 説明 |
|------|------|
| [AI Core のテスト実行](#ai-core-のテスト実行) | カメラからの顔認識パイプラインを実行する |
| [モデルのエクスポート `.pt` → `.engine`](#モデルのエクスポート-pt--engine) | YOLO `.pt` を TensorRT `.engine` に変換（FP16/INT8） |

---

## AI Core のテスト実行

人物検出（YOLO）+ トラッキング（ByteTrack）+ 顔認識（InsightFace）のパイプラインです。

### 準備

```bash
cd src/ai_core

# 依存パッケージのインストール
pip install -r requirements.txt
```

### 既知顔ソースの設定（SQLite データベース）

AI Core は `logic_service` の SQLite データベースから事前計算済みの顔埋め込み（embedding）を取得します。

`src/ai_core/` の `.env` ファイルで設定します：

```env
# フォルダの代わりに SQLite データベースから既知顔を取得する
FACE_DB_SOURCE=sqlite

# 顔埋め込みテーブルを含む SQLite ファイルへのパス
FACE_DB_PATH=logic_service/logic_service.db
```

> DB 内の埋め込みは、ユーザー登録時（admin/app 経由）に `logic_service` によって生成されます。AI Core は DB を読み取るのみで、書き込みは行いません。

### テスト実行コマンド

```bash
# CUDA で実行（Jetson）
python main.py --device cuda
```

キー操作：
- `q` — 終了

その他のオプションについては [src/ai_core/README.md](../../src/ai_core/README.md) を参照してください。

---

## モデルのエクスポート `.pt` → `.engine`

TensorRT エンジンは **ビルドした GPU アーキテクチャと完全に一致する環境でのみ動作する**ため、エクスポートコマンドは **必ずターゲット機である Jetson Orin Nano 上で実行する必要があります**。他のマシン（PC、サーバー、別世代の Jetson）でビルドしたエンジンは **動作しません**。

スクリプト：[src/ai_core/export_tensorrt.py](../../src/ai_core/export_tensorrt.py)

### エクスポートコマンド

```bash
cd src/ai_core

# パイプラインの既定モデルをすべてエクスポート（FP16）
python export_tensorrt.py

# 特定のモデルをエクスポート
python export_tensorrt.py --models yolo11l.pt

# 任意の画像サイズでエクスポート
python export_tensorrt.py --models yolo11l.pt --imgsz 640

# INT8 量子化付きでエクスポート（キャリブレーション用データセットが必要）
python export_tensorrt.py --models yolo11l.pt --int8 --data coco128.yaml
```

### パラメータ

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `--models` | `yolo11l.pt`, `yolo11l-pose.pt`, `yolov8n-face-mask.pt`, `yolov8m-protective-equipment-detection.pt` | エクスポート対象の `.pt` ファイル一覧 |
| `--imgsz` | `640` | 推論時の画像サイズ（正方形） |
| `--int8` | `False` | INT8 量子化を有効化（`--data` の指定が必要） |
| `--data` | `None` | INT8 キャリブレーションに用いる YAML データセットファイル（例：`coco128.yaml`） |
| `--workspace` | `4` | TensorRT のワークスペース（GB） |

### エクスポート完了後

`.engine` ファイルは対応する `.pt` ファイルと同じディレクトリに生成されます。パイプラインが `.pt` の代わりに engine を使用するよう、config / `.env` を更新してください：

```bash
YOLO_MODEL=yolo11l.engine
MASK_MODEL_PATH=yolov8n-face-mask.engine
PPE_MODEL_PATH=yolov8m-protective-equipment-detection.engine
```

> 注意：`.engine` ファイルが既に存在する場合、再ビルドを避けるためスクリプトはスキップします。最初から再ビルドしたい場合は、古いファイルを削除してください。
