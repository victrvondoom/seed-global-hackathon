from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    s = Settings(database_url=f"sqlite:///{tmp_path}/t.db", supplier_ack_delay_seconds=0, auto_reconcile=False,
                 fixed_now=datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc), gemini_api_key=None, google_maps_api_key=None)
    return create_app(s)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def auth(client, name="Rupesh"):
    tok = client.post("/api/auth/guest", json={"name": name}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}
