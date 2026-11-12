"""Ambient Light: software-generated colour cycles for lights that cannot fade."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, HOOKS
from .engine import Engine

PLATFORMS = [Platform.LIGHT]

type AmbientConfigEntry = ConfigEntry[Engine]


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
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AmbientConfigEntry) -> bool:
    """Cancel all tasks and unload the light."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
    return ok


async def _async_reload(hass: HomeAssistant, entry: AmbientConfigEntry) -> None:
    """Options changed: reload."""
    await hass.config_entries.async_reload(entry.entry_id)
