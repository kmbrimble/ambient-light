"""Number entities on the device card: colour speed, interval, Rainbow lap, Drift jumps, follower brightness.

Each change applies live (no reload) and is stored in the entry options, so it survives a restart and is
what the options flow shows.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AmbientConfigEntry
from .const import (
    CONF_HUE_SPEED,
    CONF_INTERVAL,
    CONF_MAX_JUMP,
    CONF_MIN_JUMP,
    CONF_RAINBOW_LAP,
    SCALE_PCT_MAX,
    SCALE_PCT_MIN,
    SCALE_PCT_STEP,
)
from .entity import AmbientConfigEntity, follower_label, set_follower_scale, set_options


@dataclass(frozen=True)
class _Spec:
    key: str
    name: str
    conf: str
    unit: str
    lo: float
    hi: float
    step: float
    mode: NumberMode
    icon: str


SPECS = (
    _Spec("hue_speed", "Colour speed", CONF_HUE_SPEED, "°/s", 0.5, 60, 0.5, NumberMode.SLIDER, "mdi:speedometer"),
    _Spec("interval", "Interval", CONF_INTERVAL, UnitOfTime.SECONDS, 5, 600, 1, NumberMode.BOX, "mdi:timer-outline"),
    _Spec("rainbow_lap", "Rainbow lap", CONF_RAINBOW_LAP, UnitOfTime.SECONDS, 10, 3600, 5, NumberMode.BOX, "mdi:rotate-right"),
    _Spec("min_jump", "Drift min jump", CONF_MIN_JUMP, "°", 1, 180, 1, NumberMode.BOX, "mdi:arrow-collapse-horizontal"),
    _Spec("max_jump", "Drift max jump", CONF_MAX_JUMP, "°", 1, 180, 1, NumberMode.BOX, "mdi:arrow-expand-horizontal"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: AmbientConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    _prune_stale_brightness(hass, entry)
    entities: list[NumberEntity] = [AmbientSetting(entry, spec) for spec in SPECS]
    entities += [AmbientFollowerBrightness(entry, eid) for eid in entry.runtime_data.follower_ids]
    async_add_entities(entities)


def _prune_stale_brightness(hass: HomeAssistant, entry: AmbientConfigEntry) -> None:
    """Remove the brightness numbers of followers that are no longer configured."""
    reg = er.async_get(hass)
    wanted = {f"{entry.entry_id}_brightness_{eid}" for eid in entry.runtime_data.follower_ids}
    prefix = f"{entry.entry_id}_brightness_"
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if e.domain == "number" and e.unique_id.startswith(prefix) and e.unique_id not in wanted:
            reg.async_remove(e.entity_id)


class AmbientSetting(AmbientConfigEntity, NumberEntity):
    """One engine setting. ``settings`` holds the live value, so the entity never goes stale."""

    def __init__(self, entry: AmbientConfigEntry, spec: _Spec) -> None:
        super().__init__(entry, spec.key)
        self._spec = spec
        self._attr_name = spec.name
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_native_min_value = spec.lo
        self._attr_native_max_value = spec.hi
        self._attr_native_step = spec.step
        self._attr_mode = spec.mode
        self._attr_icon = spec.icon

    @property
    def native_value(self) -> float:
        s = self._engine.settings
        return float(getattr(s, self._spec.conf))

    async def async_set_native_value(self, value: float) -> None:
        changes = {self._spec.conf: float(value)}
        s = self._engine.settings
        # Keep min <= max by moving the other end along, so a slider never lands in an invalid range.
        if self._spec.conf == CONF_MIN_JUMP and value > s.max_jump:
            changes[CONF_MAX_JUMP] = float(value)
        elif self._spec.conf == CONF_MAX_JUMP and value < s.min_jump:
            changes[CONF_MIN_JUMP] = float(value)
        set_options(self.hass, self._entry, changes)


class AmbientFollowerBrightness(AmbientConfigEntity, NumberEntity):
    """A follower's brightness as a percentage of the primary's (100 = the same, above 100 = brighter)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = SCALE_PCT_MIN
    _attr_native_max_value = SCALE_PCT_MAX
    _attr_native_step = SCALE_PCT_STEP
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-percent"

    def __init__(self, entry: AmbientConfigEntry, follower: str) -> None:
        super().__init__(entry, f"brightness_{follower}")
        self._follower = follower

    @property
    def name(self) -> str:
        return f"{follower_label(self.hass, self._follower)} brightness"

    @property
    def native_value(self) -> float:
        return round(self._engine.slots[self._follower].scale * 100, 1)

    async def async_set_native_value(self, value: float) -> None:
        set_follower_scale(self.hass, self._entry, self._follower, value / 100.0)
