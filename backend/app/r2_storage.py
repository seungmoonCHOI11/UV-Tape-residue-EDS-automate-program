
import os
from pathlib import Path
from typing import Optional
import boto3
from botocore.config import Config

class R2Storage:
    def __init__(self):
        self.endpoint = os.getenv("R2_ENDPOINT_URL")
        self.access_key = os.getenv("R2_ACCESS_KEY_ID")
        self.secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
        self.bucket = os.getenv("R2_BUCKET_NAME")
        self.client = None
        if self.endpoint and self.access_key and self.secret_key and self.bucket:
            self.client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name="auto",
                config=Config(signature_version="s3v4"),
            )

    @property
    def configured(self) -> bool:
        return self.client is not None

    def upload_file(self, local_path: str | Path, key: str, content_type: Optional[str] = None) -> str:
        if not self.client:
            raise RuntimeError("R2 is not configured.")
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(str(local_path), self.bucket, key, ExtraArgs=extra)
        return key

    def upload_bytes(self, data: bytes, key: str, content_type: Optional[str] = None) -> str:
        if not self.client:
            raise RuntimeError("R2 is not configured.")
        from io import BytesIO
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_fileobj(BytesIO(data), self.bucket, key, ExtraArgs=extra)
        return key

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        if not self.client:
            raise RuntimeError("R2 is not configured.")
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )

    def download_file(self, key: str, local_path: str | Path):
        if not self.client:
            raise RuntimeError("R2 is not configured.")
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(local_path))

r2 = R2Storage()
