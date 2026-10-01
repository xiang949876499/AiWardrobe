from io import BytesIO

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.wedding_service import WeddingPrivateStorage


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def put_object(self, **kwargs: object) -> None:
        self.calls.append(("put_object", kwargs))

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get_object", kwargs))
        return {"Body": BytesIO(b"private wedding asset")}

    def delete_object(self, **kwargs: object) -> None:
        self.calls.append(("delete_object", kwargs))


def test_wedding_s3_storage_uses_its_private_bucket_for_all_operations(monkeypatch) -> None:
    settings = Settings(
        storage_driver="s3",
        s3_bucket="public-wardrobe-bucket",
        wedding_s3_bucket="private-wedding-bucket",
    )
    storage = WeddingPrivateStorage(settings)
    client = FakeS3Client()
    monkeypatch.setattr(storage, "_s3_client", lambda: client)

    key = storage.put("project-1", b"private wedding asset", ".jpg", "image/jpeg")
    assert storage.read(key) == b"private wedding asset"
    storage.delete(key)

    assert [name for name, _ in client.calls] == ["put_object", "get_object", "delete_object"]
    assert all(call["Bucket"] == "private-wedding-bucket" for _, call in client.calls)
    assert all(str(call["Key"]).startswith("private-wedding/project-1/") for _, call in client.calls)


def test_s3_configuration_rejects_sharing_the_wardrobe_bucket() -> None:
    with pytest.raises(ValidationError, match="separate from wardrobe storage"):
        Settings(storage_driver="s3", s3_bucket="same", wedding_s3_bucket="same")
