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
    CONF_FOLLOWER_RATE_CAP,
    CONF_FOLLOWER_SETTINGS,
    CONF_FOLLOWERS,
    CONF_MAX_STEP,
    CONF_MOMENTUM,
    CONF_NAME,
    CONF_OFFSET,
    CONF_PRIMARY,
    CONF_PRIMARY_RATE_CAP,
    CONF_SCALE,
    DEFAULTS,
    DOMAIN,
    ENTITY_OPTIONS,
    SCALE_PCT_MAX,
    SCALE_PCT_MIN,
    SCALE_PCT_STEP,
    MAX_STEP_WARN,
    RATE_CAP_MAX,
    RATE_CAP_MIN,
    RATE_CAP_STEP,
)
from .engine import clamp_scale, rate_cap_for, resolve_cap

# Form field for a follower's brightness, in percent of the primary's (stored as the ``scale`` ratio).
CONF_BRIGHTNESS = "brightness"
CAP_KEYS = (CONF_PRIMARY_RATE_CAP, CONF_FOLLOWER_RATE_CAP)


def _num(lo: float, hi: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    cfg: dict[str, Any] = {
        "min": lo, "max": hi, "step": step, "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        cfg["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**cfg))


def settings_schema(current: dict[str, Any], primary_cap_default: float = 10.0) -> vol.Schema:
    """Schema for the 'settings' step; defaults come from ``current``.

    The rate caps are optional and carry a suggested value rather than a default: clearing the follower
    field (or leaving the primary at its platform default) stores nothing, which means "platform default".
    """

    def d(key: str) -> Any:
        return current.get(key, DEFAULTS[key])

    primary_cap = current.get(CONF_PRIMARY_RATE_CAP, primary_cap_default)
    follower_cap = {"suggested_value": current[CONF_FOLLOWER_RATE_CAP]} if CONF_FOLLOWER_RATE_CAP in current else {}
    return vol.Schema(
        {
            vol.Required(CONF_MAX_STEP, default=d(CONF_MAX_STEP)): _num(0.5, 30, 0.5, "°"),
            vol.Required(CONF_FIXED_FADE, default=d(CONF_FIXED_FADE)): _num(0, 3600, 1, "s"),
            vol.Required(CONF_MOMENTUM, default=d(CONF_MOMENTUM)): _num(0, 1, 0.05),
            vol.Required(CONF_BRIGHTNESS_FADE, default=d(CONF_BRIGHTNESS_FADE)): _num(0, 3600, 1, "s"),
            vol.Optional(CONF_PRIMARY_RATE_CAP, description={"suggested_value": primary_cap}): _num(
                RATE_CAP_MIN, RATE_CAP_MAX, RATE_CAP_STEP, "/s"
            ),
            vol.Optional(CONF_FOLLOWER_RATE_CAP, description=follower_cap): _num(
                RATE_CAP_MIN, RATE_CAP_MAX, RATE_CAP_STEP, "/s"
            ),
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
    def _store_caps(self, user_input: dict[str, Any]) -> None:
        """Keep a rate cap only when it says something: a primary cap equal to the platform default is dropped."""
        primary = user_input.get(CONF_PRIMARY_RATE_CAP)
        if primary is not None and float(primary) != rate_cap_for(self.hass, self._data[CONF_PRIMARY]):
            self._data[CONF_PRIMARY_RATE_CAP] = float(primary)
        follower = user_input.get(CONF_FOLLOWER_RATE_CAP)
        if follower is not None:
            self._data[CONF_FOLLOWER_RATE_CAP] = float(follower)

    def _warnings(self, user_input: dict[str, Any]) -> str:
        warnings = []
        if user_input[CONF_MAX_STEP] > MAX_STEP_WARN:
            warnings.append(
                f"A maximum step of {user_input[CONF_MAX_STEP]:g}° is above {MAX_STEP_WARN:g}° and looks visibly jumpy."
            )
        fade = user_input[CONF_FIXED_FADE]
        if fade > 0:
            rate = resolve_cap(self.hass, self._data[CONF_PRIMARY], user_input.get(CONF_PRIMARY_RATE_CAP))
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
            warning = self._warnings(user_input)
            if warning and user_input != self._ack:
                # Soft warning: shown once; submitting the same values again accepts them.
                self._ack = dict(user_input)
            else:
                self._data.update(user_input)
                for key in CAP_KEYS:  # absent = platform default
                    self._data.pop(key, None)
                self._store_caps(user_input)
                self._pending = list(self._data[CONF_FOLLOWERS])
                self._data[CONF_FOLLOWER_SETTINGS] = {}
                return await self.async_step_follower()
        current = {**self._current(), **(user_input or {})}
        if user_input is not None:
            for key in CAP_KEYS:  # a field the user cleared stays cleared when the form is re-shown
                if key not in user_input:
                    current.pop(key, None)
        return self.async_show_form(
            step_id="settings",
            data_schema=settings_schema(current, rate_cap_for(self.hass, self._data[CONF_PRIMARY])),
            errors=errors,
            description_placeholders={"warning": warning},
        )

    # -- per-follower -----------------------------------------------------------
    async def async_step_follower(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            eid = self._pending.pop(0)
            self._data[CONF_FOLLOWER_SETTINGS][eid] = {
                CONF_SCALE: clamp_scale(float(user_input[CONF_BRIGHTNESS]) / 100.0),
                CONF_OFFSET: float(user_input[CONF_OFFSET]) % 360.0,
            }
        if not self._pending:
            return await self._finish()
        eid = self._pending[0]
        prev = self._current().get(CONF_FOLLOWER_SETTINGS, {}).get(eid, {})
        schema = vol.Schema(
            {
                vol.Required(CONF_BRIGHTNESS, default=round(clamp_scale(prev.get(CONF_SCALE, 1.0)) * 100)): _num(
                    SCALE_PCT_MIN, SCALE_PCT_MAX, SCALE_PCT_STEP, "%"
                ),
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
    """Edit the set-once settings; saving reloads the entry. Entity-owned values (mode, speed, ...) are kept."""

    def _current(self) -> dict[str, Any]:
        return dict(self.config_entry.options)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._data = {}
        return await self._devices("init", user_input, with_name=False)

    async def _finish(self) -> ConfigFlowResult:
        # The entities own these; the flow does not ask for them, so carry the stored values over.
        kept = {k: v for k, v in self._current().items() if k in ENTITY_OPTIONS}
        return self.async_create_entry(data={**kept, **self._data})
