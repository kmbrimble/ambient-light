"""Config flow and options flow.

Config flow steps:  user -> settings -> follower (repeated once per follower)
Options flow steps: init -> settings -> follower (repeated once per follower)
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BRIGHTNESS_FADE,
    CONF_FIXED_FADE,
    CONF_FOLLOWER_SETTINGS,
    CONF_FOLLOWERS,
    CONF_HUE_SPEED,
    CONF_INTERVAL,
    CONF_MAX_JUMP,
    CONF_MAX_STEP,
    CONF_MIN_JUMP,
    CONF_MODE,
    CONF_MOMENTUM,
    CONF_NAME,
    CONF_OFFSET,
    CONF_PRIMARY,
    CONF_RAINBOW_LAP,
    CONF_SCALE,
    DEFAULTS,
    DOMAIN,
    MAX_STEP_WARN,
    MODES,
)
from .engine import rate_cap_for

SETTING_KEYS = [
    CONF_MODE,
    CONF_INTERVAL,
    CONF_HUE_SPEED,
    CONF_MAX_STEP,
    CONF_FIXED_FADE,
    CONF_MIN_JUMP,
    CONF_MAX_JUMP,
    CONF_MOMENTUM,
    CONF_RAINBOW_LAP,
    CONF_BRIGHTNESS_FADE,
]


def _num(lo: float, hi: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    cfg: dict[str, Any] = {
        "min": lo, "max": hi, "step": step, "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        cfg["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))


def settings_schema(current: dict[str, Any]) -> vol.Schema:
    """Schema for the 'settings' step; defaults come from ``current``."""

    def d(key: str) -> Any:
        return current.get(key, DEFAULTS[key])

    return vol.Schema(
        {
            vol.Required(CONF_MODE, default=d(CONF_MODE)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=MODES, translation_key="mode", mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_INTERVAL, default=d(CONF_INTERVAL)): _num(1, 3600, 1, "s"),
            vol.Required(CONF_HUE_SPEED, default=d(CONF_HUE_SPEED)): _num(0.5, 360, 0.5, "°/s"),
            vol.Required(CONF_MAX_STEP, default=d(CONF_MAX_STEP)): _num(0.5, 30, 0.5, "°"),
            vol.Required(CONF_FIXED_FADE, default=d(CONF_FIXED_FADE)): _num(0, 3600, 1, "s"),
            vol.Required(CONF_MIN_JUMP, default=d(CONF_MIN_JUMP)): _num(1, 180, 1, "°"),
            vol.Required(CONF_MAX_JUMP, default=d(CONF_MAX_JUMP)): _num(1, 180, 1, "°"),
            vol.Required(CONF_MOMENTUM, default=d(CONF_MOMENTUM)): _num(0, 1, 0.05),
            vol.Required(CONF_RAINBOW_LAP, default=d(CONF_RAINBOW_LAP)): _num(10, 3600, 1, "s"),
            vol.Required(CONF_BRIGHTNESS_FADE, default=d(CONF_BRIGHTNESS_FADE)): _num(0, 3600, 1, "s"),
        }
    )


class _FlowMixin:
    """Shared step logic. Subclasses provide ``_finish`` and ``_current``."""

    hass: Any
    _data: dict[str, Any]
    _pending: list[str]
    _ack: dict[str, Any] | None = None

    def _current(self) -> dict[str, Any]:
        raise NotImplementedError

    async def _finish(self) -> ConfigFlowResult:
        raise NotImplementedError

    # -- first step (user / init) ------------------------------------------
    def _devices_schema(self, with_name: bool) -> vol.Schema:
        cur = self._current()
        light = selector.EntitySelectorConfig(domain="light")
        fields: dict[Any, Any] = {}
        if with_name:
            fields[vol.Required(CONF_NAME)] = selector.TextSelector()
        fields[vol.Required(CONF_PRIMARY, default=cur.get(CONF_PRIMARY, vol.UNDEFINED))] = (
            selector.EntitySelector(light)
        )
        fields[vol.Optional(CONF_FOLLOWERS, default=cur.get(CONF_FOLLOWERS, []))] = (
            selector.EntitySelector(
                selector.EntitySelectorConfig(domain="light", multiple=True)
            )
        )
        return vol.Schema(fields)

    async def _devices(self, step_id: str, user_input: dict[str, Any] | None, with_name: bool):
        errors: dict[str, str] = {}
        if user_input is not None:
            followers = list(dict.fromkeys(user_input.get(CONF_FOLLOWERS, [])))
            if user_input[CONF_PRIMARY] in followers:
                errors[CONF_FOLLOWERS] = "primary_is_follower"
            elif with_name and not user_input[CONF_NAME].strip():
                errors[CONF_NAME] = "name_required"
            else:
                self._data = {
                    CONF_PRIMARY: user_input[CONF_PRIMARY],
                    CONF_FOLLOWERS: followers,
                }
                if with_name:
                    self._data[CONF_NAME] = user_input[CONF_NAME].strip()
                return await self.async_step_settings()
        return self.async_show_form(
            step_id=step_id, data_schema=self._devices_schema(with_name), errors=errors
        )

    # -- settings --------------------------------------------------------------
    def _warnings(self, user_input: dict[str, Any]) -> str:
        warnings = []
        if user_input[CONF_MAX_STEP] > MAX_STEP_WARN:
            warnings.append(
                f"A maximum step of {user_input[CONF_MAX_STEP]:g}° is above {MAX_STEP_WARN:g}° and looks visibly jumpy."
            )
        fade = user_input[CONF_FIXED_FADE]
        if fade > 0:
            rate = rate_cap_for(self.hass, self._data[CONF_PRIMARY])
            implied = 180.0 / fade / rate
            if implied > MAX_STEP_WARN:
                warnings.append(
                    f"A fixed fade of {fade:g} s implies up to {implied:.1f}° per step on the "
                    f"primary light (above {MAX_STEP_WARN:g}° looks jumpy)."
                )
        return " ".join([*warnings, "Submit again to keep these values."]) if warnings else ""

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        warning = ""
        if user_input is not None:
            if user_input[CONF_MIN_JUMP] > user_input[CONF_MAX_JUMP]:
                errors[CONF_MAX_JUMP] = "jump_range"
            else:
                warning = self._warnings(user_input)
                if warning and user_input != self._ack:
                    # Soft warning: shown once; submitting the same values again accepts them.
                    self._ack = dict(user_input)
                else:
                    self._data.update(user_input)
                    self._pending = list(self._data[CONF_FOLLOWERS])
                    self._data[CONF_FOLLOWER_SETTINGS] = {}
                    return await self.async_step_follower()
        current = {**self._current(), **(user_input or {})}
        return self.async_show_form(
            step_id="settings",
            data_schema=settings_schema(current),
            errors=errors,
            description_placeholders={"warning": warning},
        )

    # -- per-follower -----------------------------------------------------------
    async def async_step_follower(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            eid = self._pending.pop(0)
            self._data[CONF_FOLLOWER_SETTINGS][eid] = {
                CONF_SCALE: float(user_input[CONF_SCALE]),
                CONF_OFFSET: float(user_input[CONF_OFFSET]) % 360.0,
            }
        if not self._pending:
            return await self._finish()
        eid = self._pending[0]
        prev = self._current().get(CONF_FOLLOWER_SETTINGS, {}).get(eid, {})
        schema = vol.Schema(
            {
                vol.Required(CONF_SCALE, default=prev.get(CONF_SCALE, 1.0)): _num(0.1, 1.0, 0.05),
                vol.Required(CONF_OFFSET, default=prev.get(CONF_OFFSET, 0.0)): _num(0, 360, 1, "°"),
            }
        )
        state = self.hass.states.get(eid)
        label = state.name if state else eid
        return self.async_show_form(
            step_id="follower",
            data_schema=schema,
            description_placeholders={"entity": label},
        )


class AmbientLightConfigFlow(_FlowMixin, ConfigFlow, domain=DOMAIN):
    """Create one entry per ambient scene."""

    VERSION = 1

    def _current(self) -> dict[str, Any]:
        return getattr(self, "_data", {})

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        self._data = {}
        return await self._devices("user", user_input, with_name=True)

    async def _finish(self) -> ConfigFlowResult:
        data = dict(self._data)
        title = data.pop(CONF_NAME)
        return self.async_create_entry(title=title, data={}, options=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AmbientLightOptionsFlow()


class AmbientLightOptionsFlow(_FlowMixin, OptionsFlow):
    """Edit devices and settings; saving reloads the entry."""

    def _current(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._data = {}
        return await self._devices("init", user_input, with_name=False)

    async def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(data=dict(self._data))
