"""The virtual ambient light."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_TRANSITION as LIGHT_ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.start import async_at_started

from . import AmbientConfigEntry
from .const import (
    ATTR_BRIGHTNESS_PCT,
    ATTR_TRANSITION,
    DOMAIN,
    EFFECT_BY_MODE,
    EFFECT_LIST,
    MODE_BY_EFFECT,
    SERVICE_SET_BRIGHTNESS,
)
from .engine import Engine

DIAG_INTERVAL = timedelta(seconds=60)

SET_BRIGHTNESS_SCHEMA = {
    vol.Required(ATTR_BRIGHTNESS_PCT): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    vol.Optional(ATTR_TRANSITION): vol.All(vol.Coerce(float), vol.Range(min=0, max=3600)),
}


@dataclass
class _Restored(ExtraStoredData):
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.data


async def async_setup_entry(
    hass: HomeAssistant, entry: AmbientConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add the virtual light and register its service."""
    async_add_entities([AmbientLight(entry)])
    async_get_current_platform().async_register_entity_service(
        SERVICE_SET_BRIGHTNESS, SET_BRIGHTNESS_SCHEMA, "async_set_brightness"
    )


class AmbientLight(LightEntity, RestoreEntity):
    """On/off, brightness and effect for one ambient scene."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_features = LightEntityFeature.EFFECT | LightEntityFeature.TRANSITION
    _attr_effect_list = EFFECT_LIST

    def __init__(self, entry: AmbientConfigEntry) -> None:
        self._engine: Engine = entry.runtime_data
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )
        self._diag: dict[str, Any] = {}

    # -- state ---------------------------------------------------------
    @property
    def is_on(self) -> bool:
        return self._engine.on

    @property
    def brightness(self) -> int | None:
        return max(1, round(self._engine.brightness_target_pct * 2.55)) if self._engine.on else None

    @property
    def effect(self) -> str | None:
        return EFFECT_BY_MODE[self._engine.mode] if self._engine.on else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        e = self._engine
        return {
            "paused": e.paused,
            "adopted_followers": e.adopted,
            "released_followers": sorted(e.released),
            "mode": e.mode,
            "brightness_target_pct": round(e.brightness_target_pct),
            **self._diag,
        }

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _Restored(self._engine.restore_data())

    def _snapshot_diag(self) -> dict[str, Any]:
        e = self._engine
        return {
            "step_rate": e.measure_rates(),
            "min_rainbow_lap_s": round(e.min_lap, 1),
            "primary_unavailable_count": e.primary_unavailable_count,
            "failed_command_count": e.failed_command_count,
        }

    # -- lifecycle -----------------------------------------------------
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_extra_data()) is not None:
            self._engine.apply_restored(last.as_dict())
        self._diag = self._snapshot_diag()
        self._engine.on_change = self._on_engine_change
        self.async_on_remove(async_at_started(self.hass, self._at_started))
        self.async_on_remove(
            async_track_time_interval(self.hass, self._minute, DIAG_INTERVAL)
        )

    @callback
    def _at_started(self, hass: HomeAssistant) -> None:
        self._engine.async_begin()

    @callback
    def _on_engine_change(self) -> None:
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()

    @callback
    def _minute(self, _now) -> None:
        diag = self._snapshot_diag()
        if diag != self._diag:
            self._diag = diag
            self.async_write_ha_state()

    # -- commands ------------------------------------------------------
    async def async_turn_on(self, **kwargs: Any) -> None:
        mode = None
        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            mode = MODE_BY_EFFECT.get(str(effect).lower())
            if mode is None:
                raise ServiceValidationError(
                    f"Unknown effect {effect}; choose one of {', '.join(EFFECT_LIST)}"
                )
        pct = None
        if (b := kwargs.get(ATTR_BRIGHTNESS)) is not None:
            pct = max(1.0, round(b / 2.55))
        await self._engine.async_turn_on(mode, pct, kwargs.get(LIGHT_ATTR_TRANSITION))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._engine.async_turn_off()

    async def async_set_brightness(
        self, brightness_pct: float, transition: float | None = None
    ) -> None:
        """Entity service: update the target; never resume, never turn on."""
        self._engine.set_brightness(max(1.0, brightness_pct), transition)
