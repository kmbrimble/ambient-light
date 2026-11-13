"""The ambient engine: one shared clock, one sender per light, context-keyed pause/release."""

from __future__ import annotations

import asyncio
import colorsys
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .colour import (
    BrightnessFade,
    ColourClock,
    Settings,
    Rgb,
    brightness_255,
    hue_to_rgb_float,
    min_rainbow_lap,
    rgb_to_hs_v,
)
from .const import (
    BLENDS,
    CONF_BLEND,
    CONF_FOLLOWER_SETTINGS,
    CONF_FOLLOWERS,
    CONF_MODE,
    CONF_OFFSET,
    CONF_PRIMARY,
    CONF_SCALE,
    DEFAULTS,
    MODE_RAINBOW,
    MODES,
    RATE_CAP_DEFAULT,
    SLOW_PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

EWMA_ALPHA = 0.3
MIN_RATE = 0.2
WARN_INTERVAL = 600.0
CONTEXT_MEMORY = 512
INFO_INTERVAL = 600.0
# An empty-context change counts as someone else only if it moves this far from what we last sent.
# Anything smaller is rounding or an echo (tuya_local rewrites state from the device's own report).
# Colour is compared hue-agnostically (hue is unstable at low saturation): the largest difference on any
# channel of the two colours scaled to full value. 12 degrees of hue at full saturation moves the fastest
# channel by 12 x 255/60 = 51, so saturated colours keep the old 12 degree tolerance.
COLOUR_TOLERANCE = 12.0 * 255.0 / 60.0
BRIGHTNESS_TOLERANCE = 0.15 * 255


# Colour modes the engine never sends: a follower reporting one was changed by something else.
NON_COLOUR_MODES = {"color_temp", "white", "brightness", "onoff"}


def _gone(state) -> bool:
    """True for None, unavailable and unknown: a light that is not really there."""
    return state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def _has_origin(ctx) -> bool:
    """True if the context was started by a user or by another service call."""
    return bool(ctx.user_id or ctx.parent_id)


def _chroma_bright(data) -> tuple[Rgb | None, float, float | None]:
    """(colour at full value, value 0-1, brightness 0-255) from service data or state attributes.

    The colour is hs_color if present, else rgb_color, scaled so its largest channel is 255. The value is
    the largest channel of rgb_color / 255 (1 when only hs is given): a light that reports raw rgb and one
    that folds the dimming into brightness then give the same ``brightness x value``. None where absent.
    """
    hs = data.get("hs_color")
    rgb = data.get("rgb_color")
    colour: Rgb | None = None
    if hs:
        colour = tuple(c * 255.0 for c in colorsys.hsv_to_rgb(float(hs[0]) / 360.0, float(hs[1]) / 100.0, 1.0))
    elif rgb and max(rgb) > 0:
        top = float(max(rgb))
        colour = tuple(c * 255.0 / top for c in rgb)
    value = max(rgb) / 255.0 if rgb and max(rgb) > 0 else 1.0
    b = data.get("brightness")
    return colour, value, (float(b) if b is not None else None)


def _colour_diff(a: Rgb, b: Rgb) -> float:
    return max(abs(x - y) for x, y in zip(a, b))


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def rate_cap_for(hass: HomeAssistant, entity_id: str) -> float:
    """Default command-rate cap for an entity, from its platform."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is not None:
        return SLOW_PLATFORMS.get(entry.platform, RATE_CAP_DEFAULT)
    return RATE_CAP_DEFAULT


@dataclass(eq=False)
class Slot:
    """Per-light sender state."""

    entity_id: str
    primary: bool
    cap: float
    scale: float = 1.0
    offset: float = 0.0
    ewma: float | None = None
    in_flight: bool = False
    last_sent: tuple | None = None
    sent_colour: Rgb | None = None  # colour last sent, scaled to full value
    sent_value: float = 1.0  # its value (max channel / 255) for rgb commands
    sent_bright: float | None = None
    task: asyncio.Task | None = None
    sent: int = 0
    skipped: int = 0
    win_t0: float = 0.0
    win_n: int = 0
    last_rate: float = 0.0
    unavailable: bool = False

    @property
    def sent_hue(self) -> float | None:
        """Hue of the colour last sent (for logs and tests; unstable at low saturation)."""
        return None if self.sent_colour is None else rgb_to_hs_v(self.sent_colour)[0]

    @property
    def sent_sat(self) -> float | None:
        return None if self.sent_colour is None else rgb_to_hs_v(self.sent_colour)[1]

    @property
    def rate(self) -> float:
        """Effective steps/s = min(cap, 0.9 / EWMA round trip)."""
        if not self.ewma or self.ewma <= 0:
            return self.cap
        return max(MIN_RATE, min(self.cap, 0.9 / self.ewma))


class Engine:
    """Drives the primary and followers. All time comes from ``clock``."""

    def __init__(
        self,
        hass: HomeAssistant,
        options: dict[str, Any],
        *,
        clock: Callable[[], float] | None = None,
        rng: random.Random | None = None,
        run_senders: bool = True,
        create_task: Callable[..., asyncio.Task] | None = None,
    ) -> None:
        self.hass = hass
        self._clock = clock or time.monotonic
        self._run_senders = run_senders
        self._create_task = create_task or (
            lambda coro, name: hass.async_create_background_task(coro, name)
        )
        self.settings = Settings.from_options(options)
        # Options the engine was built from, less the blend: a change to anything else needs a reload.
        self._base_options = {k: v for k, v in options.items() if k != CONF_BLEND}
        self.default_mode = options.get(CONF_MODE, DEFAULTS[CONF_MODE])
        self.colour = ColourClock(self.settings, rng)
        self.bright = BrightnessFade(100.0)

        self.primary_id: str = options[CONF_PRIMARY]
        fsettings = options.get(CONF_FOLLOWER_SETTINGS, {})
        self.slots: dict[str, Slot] = {
            self.primary_id: Slot(self.primary_id, True, rate_cap_for(hass, self.primary_id))
        }
        for eid in options.get(CONF_FOLLOWERS, []):
            fs = fsettings.get(eid, {})
            self.slots[eid] = Slot(
                eid,
                False,
                rate_cap_for(hass, eid),
                scale=float(fs.get(CONF_SCALE, 1.0)),
                offset=float(fs.get(CONF_OFFSET, 0.0)),
            )
        self.follower_ids = [e for e in self.slots if e != self.primary_id]

        self.on = False
        self.paused = False
        self.mode = self.default_mode
        self.released: set[str] = set()
        self.started = False
        self.primary_unavailable_count = 0
        self.failed_command_count = 0
        self._listeners: list[Callable[[], None]] = []
        self._ctx_ids: deque[str] = deque(maxlen=CONTEXT_MEMORY)
        self._ctx_set: set[str] = set()
        self._unsub: Callable[[], None] | None = None
        self._last_warn = -WARN_INTERVAL
        self._info_last: dict[str, float] = {}
        self._diag_t0 = self._clock()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after any change of engine state; returns the unsubscribe function."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def on_change(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------------
    # Context bookkeeping
    # ------------------------------------------------------------------
    def new_context(self) -> Context:
        """A fresh Context that we remember as ours (before the call is made)."""
        ctx = Context()
        if len(self._ctx_ids) == self._ctx_ids.maxlen:
            self._ctx_set.discard(self._ctx_ids[0])
        self._ctx_ids.append(ctx.id)
        self._ctx_set.add(ctx.id)
        return ctx

    def is_ours(self, ctx: Context | None) -> bool:
        """True if the context (or its parent) was issued by this engine."""
        return ctx is not None and (ctx.id in self._ctx_set or ctx.parent_id in self._ctx_set)

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @property
    def primary_rate(self) -> float:
        return self.slots[self.primary_id].rate

    @property
    def blend(self) -> str:
        return self.settings.blend

    def set_blend(self, blend: str) -> None:
        """Change the blend live. Fades already under way finish as planned; the next one uses it."""
        if blend not in BLENDS:
            raise ValueError(f"unknown blend {blend}")
        if blend != self.settings.blend:
            self.settings.blend = blend
            self.on_change()

    def options_changed(self, options: dict[str, Any]) -> bool:
        """True if ``options`` differ from the engine's in anything but the blend (needs a reload)."""
        return {k: v for k, v in options.items() if k != CONF_BLEND} != self._base_options

    @property
    def adopted(self) -> list[str]:
        return [e for e in self.follower_ids if e not in self.released]

    @property
    def brightness_target_pct(self) -> float:
        return self.bright.target

    @property
    def min_lap(self) -> float:
        return min_rainbow_lap(self.settings.max_step, self.primary_rate)

    @property
    def active(self) -> bool:
        return self.started and self.on and not self.paused

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    @callback
    def async_begin(self) -> None:
        """Called once HA has started: subscribe and (if on) begin sending."""
        if self.started:
            return
        self.started = True
        self._unsub = async_track_state_change_event(
            self.hass, list(self.slots), self._handle_state_event
        )
        pstate = self.hass.states.get(self.primary_id)
        if _gone(pstate):
            self.slots[self.primary_id].unavailable = True
            self.primary_unavailable_count += 1
        self._sync_tasks()

    async def async_shutdown(self) -> None:
        """Cancel everything (unload)."""
        self.started = False
        if self._unsub:
            self._unsub()
            self._unsub = None
        await self._cancel_tasks()

    def _sync_tasks(self) -> None:
        """Spawn or cancel the sender tasks to match the active flag."""
        for slot in self.slots.values():
            running = slot.task is not None and not slot.task.done()
            if self.active and self._run_senders and not running:
                slot.task = self._create_task(
                    self._sender_loop(slot), f"ambient_light sender {slot.entity_id}"
                )
            elif not self.active and running:
                slot.task.cancel()

    async def _cancel_tasks(self) -> None:
        tasks = [s.task for s in self.slots.values() if s.task is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for s in self.slots.values():
            s.task = None
            s.in_flight = False

    def _reset_sent(self) -> None:
        for s in self.slots.values():
            s.last_sent = None
            s.sent_colour = s.sent_bright = None

    # ------------------------------------------------------------------
    # Commands from the virtual light
    # ------------------------------------------------------------------
    async def async_turn_on(
        self,
        mode: str | None = None,
        brightness_pct: float | None = None,
        transition: float | None = None,
    ) -> None:
        """Start or resume. Clears ``paused``."""
        now = self._clock()
        was_on = self.on
        if mode is not None and mode not in MODES:
            raise ValueError(f"unknown mode {mode}")
        if not was_on:
            self.released.clear()
            self.mode = mode or self.default_mode
            self.colour.start(self.mode, now, hue=self._last_hue)
            if brightness_pct is not None:
                if transition:
                    self.bright.set_target(1, now, 0)
                    self.bright.set_target(brightness_pct, now, transition)
                else:
                    self.bright.set_target(brightness_pct, now, 0)
        else:
            if mode is not None and mode != self.mode:
                self.mode = mode
                self.colour.set_mode(mode, now)
            if brightness_pct is not None:
                fade = self.settings.brightness_fade if transition is None else transition
                self.bright.set_target(brightness_pct, now, fade)
        self.on = True
        self.paused = False
        self._reset_sent()
        self._sync_tasks()
        self.on_change()

    async def async_turn_off(self) -> None:
        """Stop, switch the primary and adopted followers off, clear paused."""
        self.on = False
        self.paused = False
        self._last_hue = self.colour.hue_at(self._clock(), self.primary_rate)
        await self._cancel_tasks()
        self._reset_sent()
        if self.started:
            targets = [self.primary_id, *self.adopted]
            await asyncio.gather(*(self._send_off(e) for e in targets))
        self.on_change()

    async def _send_off(self, entity_id: str) -> None:
        try:
            await self.hass.services.async_call(
                "light",
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
                context=self.new_context(),
            )
        except Exception as err:  # noqa: BLE001 - never let one light break turn_off
            self._note_failure(entity_id, err)

    def set_brightness(self, pct: float, transition: float | None = None) -> None:
        """Update the target; fade if running. Never resumes or turns on."""
        now = self._clock()
        fade = self.settings.brightness_fade if transition is None else transition
        if not self.active:
            fade = 0.0
        self.bright.set_target(pct, now, fade)
        self.on_change()

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------
    _last_hue: float | None = None

    def restore_data(self) -> dict[str, Any]:
        hue = self._last_hue
        if self.on:
            hue = self.colour.current(self._clock())
        return {
            "on": self.on,
            "paused": self.paused,
            "mode": self.mode,
            "brightness_pct": self.bright.target,
            "hue": hue,
            "released": sorted(self.released),
        }

    def apply_restored(self, data: dict[str, Any]) -> None:
        now = self._clock()
        mode = data.get("mode")
        if mode in MODES:
            self.mode = mode
        pct = data.get("brightness_pct")
        if isinstance(pct, int | float):
            self.bright.set_target(float(pct), now, 0)
        hue = data.get("hue")
        self._last_hue = float(hue) if isinstance(hue, int | float) else None
        self.released = {e for e in data.get("released", []) if e in self.follower_ids}
        self.on = bool(data.get("on"))
        self.paused = bool(data.get("paused")) and self.on
        if self.on:
            # Colour starts afresh from the restored hue; no fade needed.
            self.colour.start(self.mode, now, hue=self._last_hue)

    # ------------------------------------------------------------------
    # State listeners (pause / release / adopt)
    # ------------------------------------------------------------------
    @callback
    def _handle_state_event(self, event: Event) -> None:
        eid = event.data["entity_id"]
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if eid == self.primary_id:
            self._primary_event(old, new)
        else:
            self._follower_event(eid, old, new)

    def _primary_event(self, old, new) -> None:
        slot = self.slots[self.primary_id]
        if _gone(new):
            if not slot.unavailable:
                self.primary_unavailable_count += 1
            slot.unavailable = True
            return
        slot.unavailable = False
        recovered = _gone(old)
        if recovered:
            slot.last_sent = None  # a returning light has lost our colour: resend
            slot.sent_colour = slot.sent_bright = None
        if not self.active:
            return
        ctx = new.context
        if recovered:
            # Integration-originated recovery (empty context) or our own echo: carry on.
            if self.is_ours(ctx) or not _has_origin(ctx):
                return
            rule = "recovery_with_origin"
        elif new.state == STATE_OFF and old.state != STATE_OFF:
            # While active the engine never turns the primary off, so ANY transition to off is
            # someone else, whatever the context (HA reuses a context for ~5 s after our commands).
            rule = "off"
        elif self.is_ours(ctx):
            return
        elif _has_origin(ctx):
            rule = "origin"
        else:
            # An empty context is not evidence of a human (device pushes, rounded echoes).
            rule = self._deviation(slot, new)
            if rule is None:
                return
        self._log_decision("Pausing", self.primary_id, rule, new, slot)
        self.paused = True
        self._sync_tasks()
        self.on_change()

    def _follower_event(self, eid: str, old, new) -> None:
        slot = self.slots[eid]
        if _gone(new):
            slot.unavailable = True
            return
        recovered = slot.unavailable or _gone(old)
        slot.unavailable = False
        if recovered:
            slot.last_sent = None  # a returning light has lost our colour: resend
            slot.sent_colour = slot.sent_bright = None
        if not self.on:
            return
        # The engine only ever sends hs/rgb: a follower that BECOMES colour-temp/white/brightness-only
        # was changed on the device, whatever the context says.
        mode = new.attributes.get("color_mode")
        if (
            new.state == STATE_ON
            and mode in NON_COLOUR_MODES
            and (old is None or old.attributes.get("color_mode") != mode)
        ):
            self._release(eid, "colour_mode", new)
            return
        ctx = new.context
        if self.is_ours(ctx):
            return
        if recovered and not _has_origin(ctx):
            return  # startup / integration re-reporting: adopted stays adopted, released stays released
        if new.state == STATE_OFF and (eid in self.released or recovered):
            if eid in self.released:
                self.released.discard(eid)
                slot.last_sent = None
                self.on_change()
            return
        if _has_origin(ctx):
            self._release(eid, "origin", new)
        elif new.state == STATE_OFF:
            self._release(eid, "off", new)
        else:
            rule = self._deviation(slot, new)
            if rule is not None:
                self._release(eid, rule, new)

    def _deviation(self, slot: Slot, new) -> str | None:
        """Rule name if ``new`` moved materially away from what we last sent, else None."""
        colour, value, bright = _chroma_bright(new.attributes)
        if colour is not None and slot.sent_colour is not None:
            if _colour_diff(colour, slot.sent_colour) > COLOUR_TOLERANCE:
                return "colour_deviation"
        if bright is not None and slot.sent_bright is not None:
            if abs(bright * value - slot.sent_bright * slot.sent_value) > BRIGHTNESS_TOLERANCE:
                return "brightness_deviation"
        return None

    def _log_decision(self, action: str, eid: str, rule: str, new, slot: Slot) -> None:
        """Debug always; info at most once per light per 10 min (these lights are not in the recorder)."""
        colour, _value, bright = _chroma_bright(new.attributes)
        hs = rgb_to_hs_v(colour) if colour is not None else (None, None, None)
        ctx = new.context
        msg = (
            "%s %s: rule=%s context(user_id=%s parent_id=%s ours=%s) state=%s "
            "sent(hue=%s sat=%s bright=%s) reported(hue=%s sat=%s bright=%s)"
        )
        args = (
            action, eid, rule, ctx.user_id, ctx.parent_id, self.is_ours(ctx), new.state,
            _r(slot.sent_hue), _r(slot.sent_sat), slot.sent_bright, _r(hs[0]), _r(hs[1]), bright,
        )
        _LOGGER.debug(msg, *args)
        now = self._clock()
        if now - self._info_last.get(eid, -INFO_INTERVAL) >= INFO_INTERVAL:
            self._info_last[eid] = now
            _LOGGER.info(msg, *args)

    def _release(self, eid: str, rule: str, new) -> None:
        if eid not in self.released:
            self._log_decision("Releasing", eid, rule, new, self.slots[eid])
            self.released.add(eid)
            self.on_change()

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    async def _sender_loop(self, slot: Slot) -> None:
        due = self._clock()
        while True:
            await self.async_tick(slot)
            due += 1.0 / slot.rate
            delay = due - self._clock()
            if delay < 0:
                due = self._clock()
                delay = 0.0
            await asyncio.sleep(delay)

    async def async_tick_all(self) -> None:
        """One tick for every light, concurrently (test driver)."""
        await asyncio.gather(*(self.async_tick(s) for s in self.slots.values()))

    def build_command(self, slot: Slot, now: float) -> tuple[dict[str, Any], tuple]:
        """Service data and a comparable key for a light at time ``now``."""
        rgb = self.colour.rgb_at(now, self.primary_rate)
        b = brightness_255(self.bright.value_at(now))
        data: dict[str, Any] = {"entity_id": slot.entity_id}
        if slot.primary:
            ints = tuple(round(c) for c in rgb)
            data.update(rgb_color=list(ints), brightness=b)
            return data, (ints, b)
        state = self.hass.states.get(slot.entity_id)
        modes = (state.attributes.get("supported_color_modes") if state else None) or []
        if self.mode == MODE_RAINBOW:
            # Rainbow always rides the wheel; followers may be phase-offset.
            hue = (self.colour.current(now) + slot.offset) % 360.0
            rgb = hue_to_rgb_float(hue)
        if "hs" in modes:
            # Integer hue/saturation: the device reports back exactly what was sent. hs has no value, so
            # the colour's value (below 1 for a mid-blend colour) goes into the brightness instead.
            h, sat, v = rgb_to_hs_v(rgb)
            hs = (round(h) % 360, round(sat))
            b = max(1, round(b * slot.scale * v))
            data.update(hs_color=list(hs), brightness=b)
            return data, (hs, b)
        b = max(1, round(b * slot.scale))
        ints = tuple(round(c) for c in rgb)
        data.update(rgb_color=list(ints), brightness=b)
        return data, (ints, b)

    async def async_tick(self, slot: Slot) -> None:
        """Sample the shared clock and send ONE command, or skip. Never queues."""
        if not self.active:
            return
        if slot.in_flight:
            slot.skipped += 1
            return
        state = self.hass.states.get(slot.entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            return
        if not slot.primary and slot.entity_id in self.released:
            return
        now = self._clock()
        data, key = self.build_command(slot, now)
        if key == slot.last_sent:
            return
        ctx = self.new_context()
        slot.sent_colour, slot.sent_value, slot.sent_bright = _chroma_bright(data)  # before the await: echoes land during it
        slot.in_flight = True
        t0 = self._clock()
        try:
            await self.hass.services.async_call(
                "light", "turn_on", data, blocking=True, context=ctx
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            slot.last_sent = None
            self._note_failure(slot.entity_id, err)
        else:
            slot.last_sent = key
            slot.sent += 1
            rtt = max(0.0, self._clock() - t0)
            slot.ewma = rtt if slot.ewma is None else EWMA_ALPHA * rtt + (1 - EWMA_ALPHA) * slot.ewma
        finally:
            slot.in_flight = False

    def _note_failure(self, entity_id: str, err: Exception) -> None:
        self.failed_command_count += 1
        _LOGGER.debug("Command to %s failed: %r", entity_id, err)
        now = self._clock()
        if now - self._last_warn >= WARN_INTERVAL:
            self._last_warn = now
            _LOGGER.warning("Command to %s failed (further failures logged at debug): %s", entity_id, err)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def capacity_rates(self) -> dict[str, float]:
        """Effective steps/s each light can take (min of cap and measured capacity), 0.1 resolution."""
        return {s.entity_id: round(s.rate, 1) for s in self.slots.values()}

    def measure_rates(self) -> dict[str, float]:
        """Measured SENT steps/s per light since the previous call (rounded to 0.5); idle time counts."""
        now = self._clock()
        out: dict[str, float] = {}
        for s in self.slots.values():
            elapsed = now - self._diag_t0
            if elapsed > 1.0:
                s.last_rate = (s.sent - s.win_n) / elapsed
                s.win_n = s.sent
            out[s.entity_id] = round(s.last_rate * 2) / 2
        if now - self._diag_t0 > 1.0:
            self._diag_t0 = now
        return out
