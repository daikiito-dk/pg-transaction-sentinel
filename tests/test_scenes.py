from pathlib import Path

import pytest

from demos.catalog import EXTRA_RENDERERS, SCENES, render_extra_scene

APP = Path(__file__).resolve().parents[1] / "app.py"


def test_catalog_has_fixed_routes_and_rejects_arbitrary_imports():
    assert set(EXTRA_RENDERERS) < set(SCENES)
    assert not render_extra_scene("../../outside")
    assert not render_extra_scene("customer")


@pytest.mark.parametrize("scene", list(EXTRA_RENDERERS))
def test_independent_scenes_do_not_require_postgresql(monkeypatch, scene):
    from streamlit.testing.v1 import AppTest

    import config

    def unexpected_connection():
        raise AssertionError("Independent demos must not require an AML database connection.")

    monkeypatch.setattr(config, "get_engine", unexpected_connection)
    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    at = AppTest.from_file(str(APP), default_timeout=45)
    at.query_params["scene"] = scene
    at.run()
    assert not at.exception
    assert at.button(key=f"scene_{scene}")
    assert not any("Cannot connect" in error.value for error in at.error)
    assert any("not authentication" in caption.value for caption in at.caption)
