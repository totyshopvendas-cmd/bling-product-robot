"""Tests for managed-host persistent-volume path selection."""
import storage_paths as paths


def test_defaults_keep_legacy_project_locations(monkeypatch):
    for name in (
        "TOTYSHOP_PERSIST_DIR",
        "LOCAL_DB_FILE",
        "TOTYSHOP_SECRETS_DIR",
        "TOTYSHOP_EXPORTS_DIR",
        "TOTYSHOP_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    assert paths.local_db_file() == paths.BACKEND_DIR / "data" / "local_db.json"
    assert paths.secrets_dir() == paths.BACKEND_DIR / ".secrets"
    assert paths.exports_dir() == paths.BACKEND_DIR / "exports"
    assert paths.pricing_data_dir() == paths.PROJECT_ROOT / "data"


def test_managed_volume_groups_all_persisted_files(monkeypatch, tmp_path):
    root = tmp_path / "persistent-volume"
    monkeypatch.setenv("TOTYSHOP_PERSIST_DIR", str(root))
    monkeypatch.delenv("LOCAL_DB_FILE", raising=False)

    assert paths.persist_dir() == root
    assert paths.local_db_file() == root / "local_db.json"
    assert paths.secrets_dir() == root / "secrets"
    assert paths.exports_dir() == root / "exports"
    assert paths.pricing_data_dir() == root / "data"


def test_specific_directory_overrides_win(monkeypatch, tmp_path):
    monkeypatch.setenv("TOTYSHOP_PERSIST_DIR", str(tmp_path / "volume"))
    monkeypatch.setenv("LOCAL_DB_FILE", str(tmp_path / "database.json"))
    monkeypatch.setenv("TOTYSHOP_SECRETS_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("TOTYSHOP_EXPORTS_DIR", str(tmp_path / "spreadsheets"))
    monkeypatch.setenv("TOTYSHOP_DATA_DIR", str(tmp_path / "prices"))

    assert paths.local_db_file() == tmp_path / "database.json"
    assert paths.secrets_dir() == tmp_path / "private"
    assert paths.exports_dir() == tmp_path / "spreadsheets"
    assert paths.pricing_data_dir() == tmp_path / "prices"
