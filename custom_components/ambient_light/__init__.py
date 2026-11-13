"""Ambient Light: software-generated colour cycles for lights that cannot fade."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.service import async_extract_entity_ids
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_BRIGHTNESS_PCT,
    ATTR_TRANSITION,
    CONF_BLEND,
    DEFAULTS,
    DOMAIN,
    HOOKS,
    SERVICE_SET_BRIGHTNESS,
)
from .engine import Engine

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)

SET_BRIGHTNESS_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_BRIGHTNESS_PCT): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional(ATTR_TRANSITION): vol.All(vol.Coerce(float), vol.Range(min=0, max=3600)),
    }
)

PLATFORMS = [Platform.LIGHT, Platform.SELECT]

type AmbientConfigEntry = ConfigEntry[Engine]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service at component level, before any entity exists.

    Registering it from the light platform (after the entity is added) lets an automation that
    fires on the entity's first state write run before the service exists.
    """

    async def _set_brightness(call: ServiceCall) -> None:
        registry = er.async_get(hass)
        engines: list[Engine] = []
        for entity_id in sorted(await async_extract_entity_ids(call)):
            reg = registry.async_get(entity_id)
            if reg is None or reg.platform != DOMAIN or reg.config_entry_id is None:
                raise ServiceValidationError(
                    f"{entity_id} is not an {DOMAIN} light; target the virtual ambient light"
                )
            entry = hass.config_entries.async_get_entry(reg.config_entry_id)
            if entry is None or entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(f"{entity_id} is not loaded")
            engines.append(entry.runtime_data)
        if not engines:
            raise ServiceValidationError("No ambient light targeted")
        transition = call.data.get(ATTR_TRANSITION)
        for engine in engines:
            engine.set_brightness(max(1.0, call.data[ATTR_BRIGHTNESS_PCT]), transition)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_BRIGHTNESS, _set_brightness, schema=SET_BRIGHTNESS_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AmbientConfigEntry) -> bool:
    """Create the engine and the virtual light."""
    hooks = hass.data.get(DOMAIN, {}).get(HOOKS, {})
    entry.runtime_data = Engine(
        hass,
        dict(entry.options),
        clock=hooks.get("clock"),
        rng=hooks.get("rng"),
        run_senders=hooks.get("run_senders", True),
        create_task=lambda coro, name: entry.async_create_background_task(hass, coro, name),
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmbientConfigEntry) -> bool:
    """Cancel all tasks and unload the light."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
    return ok


async def _async_options_updated(hass: HomeAssistant, entry: AmbientConfigEntry) -> None:
    """Options changed: reload, unless only the blend changed (that applies live)."""
    engine = entry.runtime_data
    if engine.options_changed(dict(entry.options)):
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        engine.set_blend(entry.options.get(CONF_BLEND, DEFAULTS[CONF_BLEND]))
