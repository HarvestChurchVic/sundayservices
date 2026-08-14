#!/usr/bin/env python3
"""Renames the audio file with the bad year-1015 date in its filename to
the correct 2025 date, by copying to the new key and deleting the old one."""
import os

import boto3
from botocore.client import Config as BotoConfig

OLD_KEY = "audio/1015-06-22-the-revelation-of-jesus.mp3"
NEW_KEY = "audio/2025-06-22-the-revelation-of-jesus.mp3"

session = boto3.client(
    "s3",
    endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    config=BotoConfig(signature_version="s3v4"),
    region_name="auto",
)

bucket = os.environ["R2_BUCKET_NAME"]

print(f"Copying {OLD_KEY} -> {NEW_KEY}...")
session.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": OLD_KEY}, Key=NEW_KEY)

print("Verifying new object exists...")
session.head_object(Bucket=bucket, Key=NEW_KEY)
print("Confirmed.")

print(f"Deleting old key {OLD_KEY}...")
session.delete_object(Bucket=bucket, Key=OLD_KEY)
print("Done.")
