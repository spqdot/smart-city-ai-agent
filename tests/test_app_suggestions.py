from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_stop_suggestions_are_visible_immediately_after_typing():
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=30).run()

    app.text_input(key="origin_query").set_value("Pragal").run()

    assert not app.exception
    origin_suggestions = next(
        widget for widget in app.radio
        if widget.label == "Starting stop suggestions"
    )
    assert "Pragal - Fertagus" in origin_suggestions.options
    assert "Pragal - MST" in origin_suggestions.options

    origin_suggestions.set_value("Pragal - MST").run()
    assert not app.exception
    assert app.radio[0].value == "Pragal - MST"
