from __future__ import annotations

import zipfile

import pytest


@pytest.mark.asyncio
async def test_cognitive_bundle_exports_and_verifies(application) -> None:
    result = await application.portability.export(include_artifacts=False)

    archive = application.settings.export_dir / result["archive"]
    assert archive.is_file()
    assert result["verification"]["valid"] is True
    with zipfile.ZipFile(archive) as bundle:
        assert "manifest.json" in bundle.namelist()
        assert "data/eck.db" in bundle.namelist()


def test_cognitive_bundle_rejects_unsafe_name(application) -> None:
    with pytest.raises(ValueError):
        application.portability.bundle_path("../brain.zip")
