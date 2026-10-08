from __future__ import annotations

import io
from fastapi.testclient import TestClient
from PIL import Image

from questbank.api.app import app
from questbank.api import factory_routes
from questbank.factory.store import InMemoryFactoryStore


def test_upload_general_asset_endpoint() -> None:
    # Initialize state
    store = InMemoryFactoryStore()
    factory_routes._STORE = store

    client = TestClient(app)

    # Create fake image bytes
    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    # Upload to assets upload endpoint
    response = client.post(
        "/api/factory/assets/upload",
        files={"file": ("test_image.png", img_bytes, "image/png")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "assetId" in data
    assert "url" in data
    assert data["url"] == f"/api/factory/assets/{data['assetId']}"

    # Verify we can retrieve the uploaded asset
    retrieve_response = client.get(f"/api/factory/assets/{data['assetId']}")
    assert retrieve_response.status_code == 200
    assert retrieve_response.headers["content-type"] == "image/png"
    assert len(retrieve_response.content) > 0
