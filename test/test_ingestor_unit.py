"""Hermetic pytest unit tests for ingestor.py pure functions."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


# Load the ingestor module dynamically to avoid import path issues
def load_ingestor_module():
    """Load ingestor.py using importlib, bypassing normal import machinery."""
    spec = importlib.util.spec_from_file_location(
        "ingestor",
        Path(__file__).resolve().parent.parent / "ingestor" / "ingestor.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Fixture to provide a fresh ingestor module for each test
@pytest.fixture
def ingestor():
    """Load ingestor module with default env vars."""
    return load_ingestor_module()


# =============================================================================
# Tests for _parse_dt
# =============================================================================

class TestParseDateTime:
    """Tests for _parse_dt function."""

    def test_parse_dt_tz_aware_utc(self, ingestor):
        """Parse UTC-aware ISO-8601 timestamp."""
        result = ingestor._parse_dt("2026-06-05T12:34:56.789123+00:00")
        assert result == "2026-06-05 12:34:56.789"

    def test_parse_dt_tz_aware_plus_offset(self, ingestor):
        """Parse ISO-8601 with positive offset; convert to UTC."""
        # 12:34:56+02:00 in UTC is 10:34:56
        result = ingestor._parse_dt("2026-06-05T12:34:56.789123+02:00")
        assert result == "2026-06-05 10:34:56.789"

    def test_parse_dt_tz_aware_minus_offset(self, ingestor):
        """Parse ISO-8601 with negative offset; convert to UTC."""
        # 12:34:56-05:00 in UTC is 17:34:56
        result = ingestor._parse_dt("2026-06-05T12:34:56.789123-05:00")
        assert result == "2026-06-05 17:34:56.789"

    def test_parse_dt_tz_naive_assumed_utc(self, ingestor):
        """Parse tz-naive ISO-8601 as UTC."""
        result = ingestor._parse_dt("2026-06-05T12:34:56")
        assert result == "2026-06-05 12:34:56.000"

    def test_parse_dt_tz_naive_with_millis(self, ingestor):
        """Parse tz-naive with milliseconds."""
        result = ingestor._parse_dt("2026-06-05T12:34:56.789")
        assert result == "2026-06-05 12:34:56.789"

    def test_parse_dt_microsecond_truncation(self, ingestor):
        """Milliseconds are truncated to 3 digits (microseconds // 1000)."""
        # .999999 microseconds -> 999 milliseconds
        result = ingestor._parse_dt("2026-06-05T12:34:56.999999")
        assert result == "2026-06-05 12:34:56.999"

        # .123456 microseconds -> 123 milliseconds
        result = ingestor._parse_dt("2026-06-05T12:34:56.123456")
        assert result == "2026-06-05 12:34:56.123"

    def test_parse_dt_none_input(self, ingestor):
        """None input returns None."""
        assert ingestor._parse_dt(None) is None

    def test_parse_dt_empty_string(self, ingestor):
        """Empty string returns None."""
        assert ingestor._parse_dt("") is None

    def test_parse_dt_invalid_string(self, ingestor):
        """Invalid ISO-8601 string returns None."""
        assert ingestor._parse_dt("not a date") is None
        assert ingestor._parse_dt("2026-13-01T00:00:00") is None
        assert ingestor._parse_dt("2026-06-32T00:00:00") is None


# =============================================================================
# Tests for _to_float
# =============================================================================

class TestToFloat:
    """Tests for _to_float function."""

    def test_to_float_integer_string(self, ingestor):
        """Parse integer string to float."""
        assert ingestor._to_float("22") == 22.0

    def test_to_float_decimal_string(self, ingestor):
        """Parse decimal string to float."""
        assert ingestor._to_float("21.5") == 21.5

    def test_to_float_negative(self, ingestor):
        """Parse negative number string."""
        assert ingestor._to_float("-15.3") == -15.3

    def test_to_float_scientific_notation(self, ingestor):
        """Parse scientific notation."""
        assert ingestor._to_float("1e3") == 1000.0

    def test_to_float_non_numeric_string(self, ingestor):
        """Non-numeric string returns None."""
        assert ingestor._to_float("on") is None
        assert ingestor._to_float("eco") is None
        assert ingestor._to_float("hello") is None

    def test_to_float_empty_string(self, ingestor):
        """Empty string returns None."""
        assert ingestor._to_float("") is None

    def test_to_float_none(self, ingestor):
        """None input returns None."""
        assert ingestor._to_float(None) is None

    def test_to_float_whitespace(self, ingestor):
        """Whitespace-only string returns None."""
        assert ingestor._to_float("   ") is None


# =============================================================================
# Tests for _row_from_new_state
# =============================================================================

class TestRowFromNewState:
    """Tests for _row_from_new_state function."""

    def test_row_numeric_sensor_valid(self, ingestor):
        """Numeric sensor with all fields produces valid row."""
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
            "last_changed": "2026-06-05T12:33:00.000+00:00",
            "attributes": {"unit_of_measurement": "°C"}
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["entity_id"] == "sensor.temperature"
        assert row["state"] == "21.5"
        assert row["state_float"] == 21.5
        assert row["last_updated"] == "2026-06-05 12:34:56.789"
        assert row["last_changed"] == "2026-06-05 12:33:00.000"

    def test_row_text_sensor_no_float(self, ingestor):
        """Text sensor has state_float=None."""
        new_state = {
            "entity_id": "climate.mode",
            "state": "eco",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
            "last_changed": "2026-06-05T12:33:00.000+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["state"] == "eco"
        assert row["state_float"] is None

    def test_row_missing_entity_id(self, ingestor):
        """Missing entity_id returns None."""
        new_state = {
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        assert ingestor._row_from_new_state(new_state) is None

    def test_row_empty_entity_id(self, ingestor):
        """Empty entity_id returns None."""
        new_state = {
            "entity_id": "",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        assert ingestor._row_from_new_state(new_state) is None

    def test_row_excluded_entity(self, ingestor, monkeypatch):
        """Entity in EXCLUDE_ENTITIES returns None."""
        monkeypatch.setattr(ingestor, "EXCLUDE_ENTITIES", {"sensor.excluded", "sensor.bad"})
        new_state = {
            "entity_id": "sensor.excluded",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        assert ingestor._row_from_new_state(new_state) is None

    def test_row_not_excluded_entity(self, ingestor, monkeypatch):
        """Entity not in EXCLUDE_ENTITIES is included."""
        monkeypatch.setattr(ingestor, "EXCLUDE_ENTITIES", {"sensor.excluded"})
        new_state = {
            "entity_id": "sensor.included",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["entity_id"] == "sensor.included"

    def test_row_missing_last_updated(self, ingestor):
        """Missing last_updated returns None."""
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
        }
        assert ingestor._row_from_new_state(new_state) is None

    def test_row_invalid_last_updated(self, ingestor):
        """Invalid last_updated returns None."""
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "not a date",
        }
        assert ingestor._row_from_new_state(new_state) is None

    def test_row_last_changed_fallback(self, ingestor):
        """last_changed falls back to last_updated if missing."""
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["last_changed"] == row["last_updated"]

    def test_row_last_changed_invalid_fallback(self, ingestor):
        """Invalid last_changed falls back to last_updated."""
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
            "last_changed": "invalid",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["last_changed"] == row["last_updated"]

    def test_row_include_attributes_true(self, ingestor, monkeypatch):
        """With INCLUDE_ATTRIBUTES=True, attributes is JSON string."""
        monkeypatch.setattr(ingestor, "INCLUDE_ATTRIBUTES", True)
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
            "attributes": {"unit": "°C", "icon": "mdi:thermometer"}
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["attributes"] != ""
        # Should be valid JSON that parses back to the original dict
        parsed_attrs = json.loads(row["attributes"])
        assert parsed_attrs == {"unit": "°C", "icon": "mdi:thermometer"}

    def test_row_include_attributes_false(self, ingestor, monkeypatch):
        """With INCLUDE_ATTRIBUTES=False, attributes is empty string."""
        monkeypatch.setattr(ingestor, "INCLUDE_ATTRIBUTES", False)
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
            "attributes": {"unit": "°C", "icon": "mdi:thermometer"}
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["attributes"] == ""

    def test_row_include_attributes_no_attributes_key(self, ingestor, monkeypatch):
        """Missing attributes key defaults to empty dict."""
        monkeypatch.setattr(ingestor, "INCLUDE_ATTRIBUTES", True)
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        parsed_attrs = json.loads(row["attributes"])
        assert parsed_attrs == {}

    def test_row_null_state_becomes_empty_string(self, ingestor):
        """None state is converted to empty string."""
        new_state = {
            "entity_id": "sensor.test",
            "state": None,
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["state"] == ""
        assert row["state_float"] is None

    def test_row_missing_state_becomes_empty_string(self, ingestor):
        """Missing state is treated as None -> empty string."""
        new_state = {
            "entity_id": "sensor.test",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert row["state"] == ""
        assert row["state_float"] is None

    def test_row_returns_all_required_fields(self, ingestor):
        """Row contains all required fields."""
        new_state = {
            "entity_id": "sensor.temperature",
            "state": "21.5",
            "last_updated": "2026-06-05T12:34:56.789+00:00",
            "last_changed": "2026-06-05T12:33:00.000+00:00",
        }
        row = ingestor._row_from_new_state(new_state)
        assert row is not None
        assert set(row.keys()) == {
            "entity_id", "state", "state_float", "attributes",
            "last_changed", "last_updated"
        }


# =============================================================================
# Tests for _env
# =============================================================================

class TestEnv:
    """Tests for _env function."""

    def test_env_returns_value_when_set(self, ingestor):
        """_env returns the value when env var is set."""
        with patch.dict("os.environ", {"TEST_VAR": "test_value"}):
            assert ingestor._env("TEST_VAR") == "test_value"

    def test_env_returns_default_when_unset(self, ingestor):
        """_env returns default when env var is not set."""
        with patch.dict("os.environ", {}, clear=False):
            result = ingestor._env("NONEXISTENT_VAR", "default_value")
            assert result == "default_value"

    def test_env_returns_empty_string_default(self, ingestor):
        """_env returns empty string as default."""
        with patch.dict("os.environ", {}, clear=False):
            result = ingestor._env("NONEXISTENT_VAR")
            assert result == ""
