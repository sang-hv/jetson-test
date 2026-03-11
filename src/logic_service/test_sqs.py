#!/usr/bin/env python3
"""
Test script for SQS sender — sends a single test message.

Usage:
    cd /path/to/logic_service
    python3 test_sqs.py

Requires .env file with valid AWS credentials:
    AWS_SQS_REGION=ap-northeast-1
    AWS_SQS_QUEUE_URL=https://sqs.ap-northeast-1.amazonaws.com/...
    AWS_SQS_ACCESS_KEY_ID=<your-key>
    AWS_SQS_SECRET_ACCESS_KEY=<your-secret>
"""

import os
import sys
import time
from pathlib import Path

# Load .env
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ Loaded .env from {env_path}")
else:
    print(f"⚠️  .env not found at {env_path}")

# Print config (mask secrets)
region = os.getenv("AWS_SQS_REGION", "")
queue_url = os.getenv("AWS_SQS_QUEUE_URL", "")
access_key = os.getenv("AWS_SQS_ACCESS_KEY_ID", "")
secret_key = os.getenv("AWS_SQS_SECRET_ACCESS_KEY", "")

print(f"\n--- SQS Config ---")
print(f"  Region:     {region}")
print(f"  Queue URL:  {queue_url}")
print(f"  Access Key: {access_key[:8]}{'*' * max(0, len(access_key) - 8) if access_key else '(empty)'}")
print(f"  Secret Key: {'*' * len(secret_key) if secret_key else '(empty)'}")

if not access_key or not secret_key:
    print("\n❌ AWS credentials are empty. Please fill in .env first.")
    sys.exit(1)

if not queue_url:
    print("\n❌ AWS_SQS_QUEUE_URL is empty.")
    sys.exit(1)

# Test SQS connection
print(f"\n--- Testing SQS Send ---")

from services.sqs_sender import send_detection_to_sqs

test_timestamp = time.time()

result = send_detection_to_sqs(
    rule_code="home_return_count",
    member_id="test_member_001",
    camera_id="test_camera_001",
    detected_at=test_timestamp,
    detection_image_url="https://example.com/test_image.jpg",
    confidence=0.95,
    object_attributes={"test": True},
)

if result:
    print(f"\n✅ SQS message sent successfully!")
    print(f"   rule_code:    home_return_count")
    print(f"   member_id:    test_member_001")
    print(f"   camera_id:    test_camera_001")
    print(f"   timestamp:    {test_timestamp}")
else:
    print(f"\n❌ Failed to send SQS message. Check logs above for details.")
    sys.exit(1)
