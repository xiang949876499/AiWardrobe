from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.config import Settings


class PrivateAssetStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def write(self, user_id: str, content_type: str, data: bytes) -> str:
        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(content_type)
        if suffix is None:
            raise ValueError("unsupported_asset_content_type")
        key = f"{user_id}/{uuid4()}{suffix}"
        if self.settings.storage_driver == "s3":
            import boto3

            client = self._s3_client()
            client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=f"private-assets/{key}",
                Body=data,
                ContentType=content_type,
                CacheControl="private, no-store",
            )
            return f"private-assets/{key}"

        root = Path(self.settings.local_private_asset_dir).resolve()
        target = (root / key).resolve()
        if not target.is_relative_to(root):
            raise ValueError("invalid_asset_key")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        if self.settings.storage_driver == "s3":
            response = self._s3_client().get_object(Bucket=self.settings.s3_bucket, Key=key)
            return response["Body"].read()
        root = Path(self.settings.local_private_asset_dir).resolve()
        target = (root / key).resolve()
        if not target.is_relative_to(root):
            raise ValueError("invalid_asset_key")
        return target.read_bytes()

    def delete(self, key: str) -> None:
        if self.settings.storage_driver == "s3":
            self._s3_client().delete_object(Bucket=self.settings.s3_bucket, Key=key)
            return
        root = Path(self.settings.local_private_asset_dir).resolve()
        target = (root / key).resolve()
        if not target.is_relative_to(root):
            raise ValueError("invalid_asset_key")
        target.unlink(missing_ok=True)

    def _s3_client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            region_name=self.settings.s3_region,
            aws_access_key_id=self.settings.s3_access_key_id,
            aws_secret_access_key=self.settings.s3_secret_access_key,
        )
