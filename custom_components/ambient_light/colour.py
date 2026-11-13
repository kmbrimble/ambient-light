"""Pure colour and timing maths: no Home Assistant imports, fully deterministic."""

from __future__ import annotations

import colorsys
import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from .const import (
    BLEND_OKLAB,
    BLEND_WHEEL,
    BLENDS,
    CONF_BLEND,
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


Rgb = tuple[float, float, float]

# At full saturation and value the fastest-moving sRGB channel changes 255 units per 60 degrees of hue.
CHANNEL_PER_DEGREE = 255.0 / 60.0
PATH_SAMPLES = 512


def hue_to_rgb_float(hue: float) -> Rgb:
    """Full-saturation, full-value hue (degrees) to 0-255 floats."""
    r, g, b = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, 1.0, 1.0)
    return r * 255.0, g * 255.0, b * 255.0


def hue_to_rgb(hue: float) -> tuple[int, int, int]:
    """Full-saturation, full-value hue (degrees) to 0-255 RGB."""
    r, g, b = hue_to_rgb_float(hue)
    return round(r), round(g), round(b)


def channel_limit(max_step: float) -> float:
    """Largest allowed per-step change on any 0-255 sRGB channel for a max hue step in degrees.

    Verified on the strip: 2 degrees at full saturation (about 8.5 units on the fastest channel) is the
    largest step that looks smooth. The same limit applies whatever the blend.
    """
    return max_step * CHANNEL_PER_DEGREE


# -- OKLab (Bjorn Ottosson) ---------------------------------------------------------------
def _to_linear(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(x: float) -> float:
    x = min(1.0, max(0.0, x))  # clamp to the sRGB gamut
    return 255.0 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055)


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1 / 3), x)


def rgb_to_oklab(rgb: Rgb) -> Rgb:
    r, g, b = (_to_linear(c) for c in rgb)
    l_ = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab_to_rgb(lab: Rgb) -> Rgb:
    """OKLab to 0-255 sRGB floats, clamped to the gamut."""
    lightness, a, b = lab
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        _from_linear(4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_),
        _from_linear(-1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_),
        _from_linear(-0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_),
    )


def lerp_oklab(lab0: Rgb, lab1: Rgb, frac: float) -> Rgb:
    return tuple(a + (b - a) * frac for a, b in zip(lab0, lab1))  # type: ignore[return-value]


def oklab_path_rate(lab0: Rgb, lab1: Rgb) -> float:
    """Steepest channel change per unit of path (0 to 1), measured on the final clamped 0-255 sRGB path."""
    prev = oklab_to_rgb(lab0)
    steepest = 0.0
    for k in range(1, PATH_SAMPLES + 1):
        cur = oklab_to_rgb(lerp_oklab(lab0, lab1, k / PATH_SAMPLES))
        steepest = max(steepest, *(abs(c - p) for c, p in zip(cur, prev)))
        prev = cur
    return steepest * PATH_SAMPLES * 1.02  # small margin for slope that changes within a sample


def rgb_to_hs_v(rgb: Rgb) -> tuple[float, float, float]:
    """0-255 RGB to (hue degrees, saturation 0-100, value 0-1)."""
    h, s, v = colorsys.rgb_to_hsv(*(min(255.0, max(0.0, c)) / 255.0 for c in rgb))
    return h * 360.0, s * 100.0, v


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
    blend: str = DEFAULTS[CONF_BLEND]

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
        blend = options.get(CONF_BLEND, DEFAULTS[CONF_BLEND])
        return cls(
            blend=blend if blend in BLENDS else DEFAULTS[CONF_BLEND],
            **{a: float(options.get(k, DEFAULTS[k])) for a, k in keys.items()},
        )


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
    """The shared colour clock. Give every light the same instance and a time.

    A fade is planned in one blend and keeps it to the end: ``wheel`` moves the HSV hue the shortest way
    round at full saturation, ``oklab`` blends in a straight line in OKLab. Either way ``hue`` is the
    logical hue (the colour being faded to), which stays meaningful where the displayed colour is
    desaturated. A leg that starts off the wheel (an OKLab fade still under way) is always planned in
    OKLab, so a switch to the wheel never jumps.
    """

    def __init__(self, settings: Settings, rng: random.Random | None = None) -> None:
        self.settings = settings
        self.rng = rng or random.Random()
        self.mode = MODE_RANDOM
        self._hue = 0.0  # Rainbow phase / base hue
        self._fade_blend = BLEND_WHEEL
        self._fade_start = 0.0
        self._fade_delta = 0.0
        self._end_hue = 0.0
        self._lab0: Rgb = (0.0, 0.0, 0.0)
        self._lab1: Rgb = (0.0, 0.0, 0.0)
        self._fade_t0 = 0.0
        self._fade_dur = 0.0
        self._next_pick = 0.0
        self._sign = 1
        self._last_t = 0.0

    @property
    def blend(self) -> str:
        return self.settings.blend

    # -- helpers ---------------------------------------------------------
    def _frac(self, now: float) -> float:
        if self._fade_dur <= 0 or now >= self._fade_t0 + self._fade_dur:
            return 1.0
        return max(0.0, (now - self._fade_t0) / self._fade_dur)

    def _fade_value(self, now: float) -> float:
        """Logical hue of the fade: the interpolated hue (wheel) or the hue being faded to (OKLab)."""
        if self._fade_blend == BLEND_OKLAB:
            return self._end_hue
        return (self._fade_start + self._fade_delta * self._frac(now)) % 360.0

    def _fade_rgb(self, now: float) -> Rgb:
        if self._fade_blend == BLEND_OKLAB:
            return oklab_to_rgb(lerp_oklab(self._lab0, self._lab1, self._frac(now)))
        return hue_to_rgb_float(self._fade_value(now))

    def _on_wheel(self, now: float) -> bool:
        """True if the displayed colour is a full-saturation wheel colour."""
        return self._fade_blend == BLEND_WHEEL or self._frac(now) >= 1.0

    def current(self, now: float) -> float:
        """Current logical hue without advancing the schedule."""
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

    def oklab_duration(self, lab0: Rgb, lab1: Rgb, primary_rate: float) -> float:
        """Smallest time (s) for which no primary step exceeds the per-channel limit."""
        limit = channel_limit(self.settings.max_step) * max(primary_rate, 1e-6)
        return oklab_path_rate(lab0, lab1) / limit

    # -- control ---------------------------------------------------------
    def start(self, mode: str, now: float, hue: float | None = None) -> None:
        """(Re)start in a mode from a hue (default: a random one)."""
        if hue is None:
            hue = self.rng.uniform(0.0, 360.0)
        self.mode = mode
        self._hue = hue % 360.0
        self._fade_blend = BLEND_WHEEL
        self._fade_start = self._hue
        self._fade_delta = 0.0
        self._end_hue = self._hue
        self._fade_t0 = now
        self._fade_dur = 0.0
        self._next_pick = now  # pick straight away
        self._last_t = now
        self._sign = self.rng.choice((1, -1))

    def set_mode(self, mode: str, now: float) -> None:
        """Switch mode continuing from the current hue."""
        if mode == self.mode:
            return
        if self.mode != MODE_RAINBOW and mode != MODE_RAINBOW and not self._on_wheel(now):
            # Random <-> Drift during an OKLab fade: keep the fade and the displayed colour; the
            # new mode's pick (at once) is planned in OKLab from the colour on display.
            self.mode = mode
            self._next_pick = now
            return
        hue = self.current(now)
        if self.mode != MODE_RAINBOW and not self._on_wheel(now):
            # Into Rainbow from an unfinished OKLab fade: Rainbow lives on the wheel, so start at the
            # hue nearest the colour on display (the one unavoidable snap, a user-initiated change).
            hue = rgb_to_hs_v(self._fade_rgb(now))[0]
        self.start(mode, now, hue=hue)

    # -- sampling --------------------------------------------------------
    def _advance(self, now: float, primary_rate: float) -> None:
        if self.mode == MODE_RAINBOW:
            dt = max(0.0, now - self._last_t)
            self._hue = (self._hue + dt * 360.0 / self.effective_lap(primary_rate)) % 360.0
            self._last_t = max(self._last_t, now)
        elif now >= self._next_pick:
            pick_t = self._next_pick if now - self._next_pick < self.settings.interval else now
            self._pick(pick_t, primary_rate)

    def hue_at(self, now: float, primary_rate: float) -> float:
        """Advance the schedule to ``now`` (idempotent) and return the logical hue."""
        self._advance(now, primary_rate)
        return self._hue if self.mode == MODE_RAINBOW else self._fade_value(now)

    def rgb_at(self, now: float, primary_rate: float) -> Rgb:
        """Advance the schedule to ``now`` and return the colour as 0-255 floats (unrounded)."""
        self._advance(now, primary_rate)
        if self.mode == MODE_RAINBOW:
            return hue_to_rgb_float(self._hue)
        return self._fade_rgb(now)

    def _pick(self, at: float, primary_rate: float) -> None:
        s = self.settings
        start_rgb = self._fade_rgb(at)
        blend = s.blend if self._on_wheel(at) else BLEND_OKLAB
        base = self._fade_value(at)
        if self.mode == MODE_DRIFT:
            if self.rng.random() >= s.momentum:
                self._sign = -self._sign
            delta = self._sign * self.rng.uniform(s.min_jump, s.max_jump)
        else:
            delta = hue_delta(base, self.rng.uniform(0.0, 360.0))
        self._fade_blend = blend
        self._fade_start = base
        self._fade_delta = delta
        self._end_hue = (base + delta) % 360.0
        self._fade_t0 = at
        if blend == BLEND_OKLAB:
            self._lab0 = rgb_to_oklab(start_rgb)
            self._lab1 = rgb_to_oklab(hue_to_rgb_float(self._end_hue))
            natural = self.oklab_duration(self._lab0, self._lab1, primary_rate)
        else:
            natural = abs(delta) / self.effective_speed(primary_rate)
        self._fade_dur = s.fixed_fade if s.fixed_fade > 0 else natural
        self._next_pick = at + s.interval

    @property
    def pick_count_state(self) -> tuple[float, float]:
        """Fade start/delta; handy for tests."""
        return self._fade_start, self._fade_delta
