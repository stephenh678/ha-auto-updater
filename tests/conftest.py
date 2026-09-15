"""Pytest configuration and Home Assistant stubs for unit testing."""
import sys
from unittest.mock import MagicMock
from datetime import datetime, timezone

# Build lightweight stub for homeassistant module hierarchy if homeassistant is not installed
try:
    import homeassistant
except ImportError:
    ha_mock = MagicMock()
    
    # Setup datetime util mock
    dt_util_mock = MagicMock()
    dt_util_mock.now.side_effect = lambda: datetime.now(timezone.utc)
    dt_util_mock.parse_datetime.side_effect = lambda s: datetime.fromisoformat(s) if s else None
    dt_util_mock.as_utc.side_effect = lambda d: d.replace(tzinfo=timezone.utc) if d else None

    # Setup module stubs
    sys.modules["homeassistant"] = ha_mock
    sys.modules["homeassistant.components"] = ha_mock
    sys.modules["homeassistant.components.persistent_notification"] = MagicMock(async_create=MagicMock())
    sys.modules["homeassistant.config_entries"] = MagicMock(ConfigEntry=MagicMock)
    sys.modules["homeassistant.core"] = MagicMock(HomeAssistant=MagicMock, ServiceCall=MagicMock)
    sys.modules["homeassistant.helpers"] = MagicMock()
    sys.modules["homeassistant.helpers.config_validation"] = MagicMock(
        entity_id=MagicMock(),
        multi_select=MagicMock(return_value=MagicMock()),
    )
    sys.modules["homeassistant.helpers.entity_platform"] = MagicMock()
    sys.modules["homeassistant.helpers.entity_registry"] = MagicMock()
    sys.modules["homeassistant.helpers.event"] = MagicMock()
    sys.modules["homeassistant.helpers.typing"] = MagicMock()
    sys.modules["homeassistant.loader"] = MagicMock()
    sys.modules["homeassistant.helpers.issue_registry"] = MagicMock()
    def _async_redact_data(data, to_redact):
        if isinstance(data, dict):
            return {
                k: ("**REDACTED**" if k in to_redact else _async_redact_data(v, to_redact))
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [_async_redact_data(v, to_redact) for v in data]
        return data

    class _NumberSelectorMode:
        BOX = "box"
        SLIDER = "slider"

    sys.modules["homeassistant.util"] = MagicMock(dt=dt_util_mock)
    sys.modules["homeassistant.components.diagnostics"] = MagicMock(async_redact_data=_async_redact_data)
    sys.modules["homeassistant.components.sensor"] = MagicMock()
    sys.modules["homeassistant.components.binary_sensor"] = MagicMock()
    sys.modules["homeassistant.components.switch"] = MagicMock()
    sys.modules["homeassistant.components.button"] = MagicMock()
    sys.modules["homeassistant.components.select"] = MagicMock()
    class _DummySelector:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, data):
            return data

    sys.modules["homeassistant.helpers.selector"] = MagicMock(
        EntitySelector=_DummySelector,
        EntitySelectorConfig=_DummySelector,
        NumberSelector=_DummySelector,
        NumberSelectorConfig=_DummySelector,
        NumberSelectorMode=_NumberSelectorMode,
        TimeSelector=_DummySelector,
        TimeSelectorConfig=_DummySelector,
    )
