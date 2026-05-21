from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings


class StoredFile:
    def __init__(self, key: str, url: str) -> None:
        self.key = key
        self.url = url


class StorageService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def save_upload(self, file: UploadFile) -> StoredFile:
        suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
        key = f"garments/{uuid4()}{suffix.lower()}"
        data = await file.read()
        if self.settings.storage_driver == "s3":
            return self._save_s3(key, data, file.content_type or "application/octet-stream")
        return self._save_local(key, data)

    def save_bytes(self, data: bytes, suffix: str = ".jpg", prefix: str = "garments") -> StoredFile:
        key = f"{prefix}/{uuid4()}{suffix.lower()}"
        if self.settings.storage_driver == "s3":
            return self._save_s3(key, data, "image/jpeg")
        return self._save_local(key, data)

    def _save_local(self, key: str, data: bytes) -> StoredFile:
        upload_root = Path(self.settings.local_upload_dir)
        destination = upload_root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return StoredFile(key=key, url=f"/static/uploads/{key}")

    def _save_s3(self, key: str, data: bytes, content_type: str) -> StoredFile:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            region_name=self.settings.s3_region,
            aws_access_key_id=self.settings.s3_access_key_id,
            aws_secret_access_key=self.settings.s3_secret_access_key,
        )
        client.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=data, ContentType=content_type)
        base = self.settings.public_storage_base_url
        url = f"{base.rstrip('/')}/{key}" if base else f"s3://{self.settings.s3_bucket}/{key}"
        return StoredFile(key=key, url=url)
