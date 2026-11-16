"""Shared base for the entities on the entry's device, and the one way they change options."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_FOLLOWER_SETTINGS, CONF_SCALE, DOMAIN
from .engine import Engine, clamp_scale


def device_info(entry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=entry.title)


def set_options(hass: HomeAssistant, entry, changes: dict[str, Any]) -> None:
    """Apply ``changes`` to the engine live and persist them in the entry options (no reload).

    The engine is updated first so the entity reads its new value at once; the entry's update listener
    then re-applies the same options, which is a no-op.
    """
    options = {**entry.options, **changes}
    entry.runtime_data.apply_options(options)
    hass.config_entries.async_update_entry(entry, options=options)


def set_follower_scale(hass: HomeAssistant, entry, entity_id: str, scale: float) -> None:
    """Store one follower's brightness ratio, leaving its other settings alone."""
    fsettings = {k: dict(v or {}) for k, v in (entry.options.get(CONF_FOLLOWER_SETTINGS) or {}).items()}
    fsettings.setdefault(entity_id, {})[CONF_SCALE] = clamp_scale(scale)
    set_options(hass, entry, {CONF_FOLLOWER_SETTINGS: fsettings})


def follower_label(hass: HomeAssistant, entity_id: str) -> str:
    """A follower's friendly name, built the way HA builds it, so it does not depend on startup order.

    The registries are loaded before any integration sets up, the follower's state may not exist yet. So the
    entity registry comes first (device name + entity name for a ``has_entity_name`` entity, as HA composes
    it), then the state's name for an entity that is not in the registry, then the prettified entity id.
    """
    if (reg := er.async_get(hass).async_get(entity_id)) is not None:
        base = reg.name or reg.original_name
        device = dr.async_get(hass).async_get(reg.device_id) if reg.device_id else None
        dname = (device.name_by_user or device.name) if device else None
        if reg.has_entity_name and dname:
            return f"{dname} {base}" if base else dname
        if base:
            return base
    if (state := hass.states.get(entity_id)) is not None and state.name:
        return state.name
    return entity_id.split(".", 1)[-1].replace("_", " ").title()


class AmbientConfigEntity(Entity):
    """A config-category entity on the entry's device; refreshes when the engine reports a change."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry, key: str) -> None:
        self._entry = entry
        self._engine: Engine = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._engine.add_listener(self._on_engine_change))

    @callback
    def _on_engine_change(self) -> None:
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()
