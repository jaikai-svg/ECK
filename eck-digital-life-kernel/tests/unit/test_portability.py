from __future__ import annotations

import zipfile

import pytest


@pytest.mark.asyncio
async def test_cognitive_bundle_exports_and_verifies(application) -> None:
    application.rag.vectors.initialize()
    result = await application.portability.export(include_artifacts=False)

    archive = application.settings.export_dir / result["archive"]
    assert archive.is_file()
    assert result["verification"]["valid"] is True
    with zipfile.ZipFile(archive) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert "data/eck.db" in bundle.namelist()
        assert "memory/eck-rag.sqlite3" in bundle.namelist()
        assert "identity/soul/SOUL.md" in bundle.namelist()
        manifest = bundle.read("manifest.json").decode("utf-8")
        assert "eck-cognitive-bundle.v2" in manifest


def test_cognitive_bundle_rejects_unsafe_name(application) -> None:
    with pytest.raises(ValueError):
        application.portability.bundle_path("../brain.zip")
