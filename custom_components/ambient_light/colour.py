"""Pure colour and timing maths: no Home Assistant imports, fully deterministic."""

from __future__ import annotations

import colorsys
import random
from collections.abc import Callable
from dataclasses import dataclass

from .const import (
    DEFAULTS,
    CONF_BRIGHTNESS_FADE,
    CONF_FIXED_FADE,
    CONF_HUE_SPEED,
    CONF_INTERVAL,
    CONF_MAX_JUMP,
    CONF_MAX_STEP,
    CONF_MIN_JUMP,
    CONF_MOMENTUM,
    CONF_RAINBOW_LAP,
    MODE_DRIFT,
    MODE_RAINBOW,
    MODE_RANDOM,
)


def hue_delta(start: float, end: float) -> float:
    """Signed shortest-way-round difference end - start, in [-180, 180)."""
    return ((end - start + 180.0) % 360.0) - 180.0


def hue_to_rgb(hue: float) -> tuple[int, int, int]:
    """Full-saturation, full-value hue (degrees) to 0-255 RGB."""
    r, g, b = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, 1.0, 1.0)
    return round(r * 255), round(g * 255), round(b * 255)


def brightness_255(pct: float) -> int:
    """Brightness percent (1-100) to HA brightness 1-255."""
    return max(1, min(255, round(pct * 2.55)))


@dataclass(slots=True)
class Settings:
    """Numeric settings consumed by the engine."""

    interval: float = DEFAULTS[CONF_INTERVAL]
    hue_speed: float = DEFAULTS[CONF_HUE_SPEED]
    max_step: float = DEFAULTS[CONF_MAX_STEP]
    fixed_fade: float = DEFAULTS[CONF_FIXED_FADE]
    min_jump: float = DEFAULTS[CONF_MIN_JUMP]
    max_jump: float = DEFAULTS[CONF_MAX_JUMP]
    momentum: float = DEFAULTS[CONF_MOMENTUM]
    rainbow_lap: float = DEFAULTS[CONF_RAINBOW_LAP]
    brightness_fade: float = DEFAULTS[CONF_BRIGHTNESS_FADE]

    @classmethod
    def from_options(cls, options: dict) -> Settings:
        """Build from an options mapping, falling back to the defaults."""
        keys = {
            "interval": CONF_INTERVAL,
            "hue_speed": CONF_HUE_SPEED,
            "max_step": CONF_MAX_STEP,
            "fixed_fade": CONF_FIXED_FADE,
            "min_jump": CONF_MIN_JUMP,
            "max_jump": CONF_MAX_JUMP,
            "momentum": CONF_MOMENTUM,
            "rainbow_lap": CONF_RAINBOW_LAP,
            "brightness_fade": CONF_BRIGHTNESS_FADE,
        }
        return cls(**{a: float(options.get(k, DEFAULTS[k])) for a, k in keys.items()})


def min_rainbow_lap(max_step: float, primary_rate: float) -> float:
    """Shortest lap (s) that keeps each primary step <= max_step degrees."""
    return 360.0 / (max_step * max(primary_rate, 1e-6))


class BrightnessFade:
    """Linear brightness (percent) ramp."""

    def __init__(self, pct: float = 100.0) -> None:
        self.start = pct
        self.target = pct
        self.t0 = 0.0
        self.duration = 0.0

    def value_at(self, now: float) -> float:
        if self.duration <= 0 or now >= self.t0 + self.duration:
            return self.target
        frac = max(0.0, (now - self.t0) / self.duration)
        return self.start + (self.target - self.start) * frac

    def set_target(self, pct: float, now: float, transition: float) -> None:
        pct = min(100.0, max(1.0, pct))
        self.start = self.value_at(now) if transition > 0 else pct
        self.target = pct
        self.t0 = now
        self.duration = max(0.0, transition)


class ColourClock:
    """The shared hue clock. Give every light the same instance and a time."""

    def __init__(self, settings: Settings, rng: random.Random | None = None) -> None:
        self.settings = settings
        self.rng = rng or random.Random()
        self.mode = MODE_RANDOM
        self._hue = 0.0  # Rainbow phase / base hue
        self._fade_start = 0.0
        self._fade_delta = 0.0
        self._fade_t0 = 0.0
        self._fade_dur = 0.0
        self._next_pick = 0.0
        self._sign = 1
        self._last_t = 0.0

    # -- helpers ---------------------------------------------------------
    def _fade_value(self, now: float) -> float:
        if self._fade_dur <= 0 or now >= self._fade_t0 + self._fade_dur:
            return (self._fade_start + self._fade_delta) % 360.0
        frac = max(0.0, (now - self._fade_t0) / self._fade_dur)
        return (self._fade_start + self._fade_delta * frac) % 360.0

    def current(self, now: float) -> float:
        """Current primary hue without advancing the schedule."""
        if self.mode == MODE_RAINBOW:
            return self._hue % 360.0
        return self._fade_value(now)

    def effective_speed(self, primary_rate: float) -> float:
        """Hue speed (deg/s), limited to max_step per primary step."""
        s = self.settings
        return max(0.1, min(s.hue_speed, s.max_step * max(primary_rate, 1e-6)))

    def effective_lap(self, primary_rate: float) -> float:
        """Rainbow lap (s), clamped up so steps stay <= max_step."""
        return max(
            self.settings.rainbow_lap,
            min_rainbow_lap(self.settings.max_step, primary_rate),
        )

    # -- control ---------------------------------------------------------
    def start(self, mode: str, now: float, hue: float | None = None) -> None:
        """(Re)start in a mode from a hue (default: a random one)."""
        if hue is None:
            hue = self.rng.uniform(0.0, 360.0)
        self.mode = mode
        self._hue = hue % 360.0
        self._fade_start = self._hue
        self._fade_delta = 0.0
        self._fade_t0 = now
        self._fade_dur = 0.0
        self._next_pick = now  # pick straight away
        self._last_t = now
        self._sign = self.rng.choice((1, -1))

    def set_mode(self, mode: str, now: float) -> None:
        """Switch mode continuing from the current hue."""
        if mode != self.mode:
            self.start(mode, now, hue=self.current(now))

    # -- sampling --------------------------------------------------------
    def hue_at(self, now: float, primary_rate: float) -> float:
        """Advance the schedule to ``now`` (idempotent) and return the hue."""
        if self.mode == MODE_RAINBOW:
            dt = max(0.0, now - self._last_t)
            self._hue = (self._hue + dt * 360.0 / self.effective_lap(primary_rate)) % 360.0
            self._last_t = max(self._last_t, now)
            return self._hue
        if now >= self._next_pick:
            pick_t = self._next_pick if now - self._next_pick < self.settings.interval else now
            self._pick(pick_t, primary_rate)
        return self._fade_value(now)

    def _pick(self, at: float, primary_rate: float) -> None:
        s = self.settings
        cur = self._fade_value(at)
        if self.mode == MODE_DRIFT:
            if self.rng.random() >= s.momentum:
                self._sign = -self._sign
            delta = self._sign * self.rng.uniform(s.min_jump, s.max_jump)
        else:
            delta = hue_delta(cur, self.rng.uniform(0.0, 360.0))
        self._fade_start = cur
        self._fade_delta = delta
        self._fade_t0 = at
        self._fade_dur = (
            s.fixed_fade if s.fixed_fade > 0 else abs(delta) / self.effective_speed(primary_rate)
        )
        self._next_pick = at + s.interval

    @property
    def pick_count_state(self) -> tuple[float, float]:
        """Fade start/delta; handy for tests."""
        return self._fade_start, self._fade_delta
