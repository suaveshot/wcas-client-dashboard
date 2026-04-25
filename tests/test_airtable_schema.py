"""airtable_schema unit tests. The pyairtable Api is mocked at the seam so
no real network call fires."""

import json
import os
from unittest.mock import MagicMock

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import airtable_schema


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("AIRTABLE_PAT", "test-pat-not-real")
    yield


def _write_config(tenant_root, tenant_id, base_id="appAAA1234567890", table_name="T"):
    cfg_dir = tenant_root / tenant_id
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "tenant_config.json").write_text(
        json.dumps({"airtable_bookings": {"base_id": base_id, "table_name": table_name}}),
        encoding="utf-8",
    )


def test_load_tenant_config_returns_empty_dict_when_missing():
    assert airtable_schema.load_tenant_config("missing") == {}


def test_whitelisted_base_id_returns_id(tmp_path):
    _write_config(tmp_path, "acme", base_id="appXXXXXXXXXXXXX1")
    assert airtable_schema.whitelisted_base_id("acme") == "appXXXXXXXXXXXXX1"


def test_whitelisted_base_id_returns_none_without_config(tmp_path):
    assert airtable_schema.whitelisted_base_id("acme") is None


def test_whitelisted_base_id_rejects_non_app_prefix(tmp_path):
    _write_config(tmp_path, "acme", base_id="not_a_base")
    assert airtable_schema.whitelisted_base_id("acme") is None


def test_fetch_schema_rejects_unknown_tenant(tmp_path):
    with pytest.raises(airtable_schema.AirtableSchemaError, match="no whitelisted"):
        airtable_schema.fetch_schema("never_seen", "appAAA1234567890")


def test_fetch_schema_rejects_base_outside_whitelist(tmp_path):
    _write_config(tmp_path, "acme", base_id="appAAA1234567890")
    with pytest.raises(airtable_schema.AirtableSchemaError, match="not in whitelist"):
        airtable_schema.fetch_schema("acme", "appBBB1234567890")


def test_fetch_schema_returns_tables_and_scrubbed_records(tmp_path, monkeypatch):
    _write_config(tmp_path, "acme", base_id="appAAA1234567890")

    fake_field = MagicMock(name="Email", type="email")
    fake_field.name = "Email"
    fake_field.type = "email"
    fake_table_meta = MagicMock()
    fake_table_meta.name = "Students"
    fake_table_meta.fields = [fake_field]
    fake_schema = MagicMock(tables=[fake_table_meta])

    fake_base = MagicMock()
    fake_base.schema.return_value = fake_schema

    fake_table = MagicMock()
    # .all() is now used for BOTH row count and sample fetch (iterate yielded
    # pages not records in this pyairtable version, broke row_count). Return
    # 2 rows so the row_count assertion below picks up 2.
    fake_table.all.return_value = [
        {"id": "rec1", "fields": {"Email": "real@example.com", "Phone": "(805) 555-1234"}},
        {"id": "rec2", "fields": {"Email": "other@example.com"}},
    ]

    fake_api = MagicMock()
    fake_api.base.return_value = fake_base
    fake_api.table.return_value = fake_table

    monkeypatch.setattr(airtable_schema, "_api", lambda: fake_api)

    result = airtable_schema.fetch_schema("acme", "appAAA1234567890")
    assert result["base_id"] == "appAAA1234567890"
    assert len(result["tables"]) == 1
    table = result["tables"][0]
    assert table["name"] == "Students"
    assert table["row_count"] == 2

    sample = table["sample_recent_records"][0]["fields"]
    # PII scrubbed - email and phone replaced with sentinels
    assert sample["Email"] == "[email]"
    assert sample["Phone"] == "[phone]"


def test_fetch_schema_raises_without_pat(tmp_path, monkeypatch):
    _write_config(tmp_path, "acme", base_id="appAAA1234567890")
    monkeypatch.delenv("AIRTABLE_PAT", raising=False)
    with pytest.raises(airtable_schema.AirtableSchemaError, match="AIRTABLE_PAT"):
        airtable_schema.fetch_schema("acme", "appAAA1234567890")
