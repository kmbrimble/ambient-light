"""Constants for the Ambient Light integration."""

from __future__ import annotations

DOMAIN = "ambient_light"

MODE_RANDOM = "random"
MODE_DRIFT = "drift"
MODE_RAINBOW = "rainbow"
MODES = [MODE_RANDOM, MODE_DRIFT, MODE_RAINBOW]
EFFECT_BY_MODE = {MODE_RANDOM: "Random", MODE_DRIFT: "Drift", MODE_RAINBOW: "Rainbow"}
MODE_BY_EFFECT = {v.lower(): k for k, v in EFFECT_BY_MODE.items()}
EFFECT_LIST = list(EFFECT_BY_MODE.values())

CONF_NAME = "name"
CONF_PRIMARY = "primary"
CONF_FOLLOWERS = "followers"
CONF_FOLLOWER_SETTINGS = "follower_settings"
CONF_SCALE = "scale"
CONF_OFFSET = "offset"
CONF_MODE = "mode"
CONF_INTERVAL = "interval"
CONF_HUE_SPEED = "hue_speed"
CONF_MAX_STEP = "max_step"
CONF_FIXED_FADE = "fixed_fade"
CONF_MIN_JUMP = "min_jump"
CONF_MAX_JUMP = "max_jump"
CONF_MOMENTUM = "momentum"
CONF_RAINBOW_LAP = "rainbow_lap"
CONF_BRIGHTNESS_FADE = "brightness_fade"
CONF_BLEND = "blend"
CONF_PRIMARY_RATE_CAP = "primary_rate_cap"
CONF_FOLLOWER_RATE_CAP = "follower_rate_cap"

BLEND_WHEEL = "wheel"
BLEND_OKLAB = "oklab"
BLENDS = [BLEND_WHEEL, BLEND_OKLAB]
# Labels of the blend select entity (the option values users pick in the UI and in automations).
BLEND_LABELS = {BLEND_WHEEL: "Wheel", BLEND_OKLAB: "OKLab"}
BLEND_BY_LABEL = {v: k for k, v in BLEND_LABELS.items()}

DEFAULTS = {
    CONF_MODE: MODE_RANDOM,
    CONF_INTERVAL: 30.0,
    CONF_HUE_SPEED: 8.0,
    CONF_MAX_STEP: 2.0,
    CONF_FIXED_FADE: 0.0,
    CONF_MIN_JUMP: 10.0,
    CONF_MAX_JUMP: 40.0,
    CONF_MOMENTUM: 0.7,
    CONF_RAINBOW_LAP: 120.0,
    CONF_BRIGHTNESS_FADE: 60.0,
    CONF_BLEND: BLEND_WHEEL,
}

# Maximum commands per second per light, by platform of the entity.
RATE_CAP_SLOW = 4.0
RATE_CAP_DEFAULT = 10.0
SLOW_PLATFORMS = {"meross_lan": RATE_CAP_SLOW}
# Range of the configurable cap (commands per second).
RATE_CAP_MIN = 0.5
RATE_CAP_MAX = 10.0
RATE_CAP_STEP = 0.5
# Options owned by device-card entities (v0.3.0). They are not asked for in the setup / options flow;
# the options flow carries the stored values over unchanged.
ENTITY_OPTIONS = (
    CONF_MODE, CONF_BLEND, CONF_INTERVAL, CONF_HUE_SPEED, CONF_RAINBOW_LAP, CONF_MIN_JUMP, CONF_MAX_JUMP,
)
# Options that apply live (no reload): the entity options and the rate caps. Follower brightness
# (``follower_settings[entity][scale]``) is live too; the rest of ``follower_settings`` is not.
LIVE_OPTIONS = (*ENTITY_OPTIONS, CONF_PRIMARY_RATE_CAP, CONF_FOLLOWER_RATE_CAP)

# Follower brightness relative to the primary: stored as a ratio (``scale``), shown as a percentage.
# 1.0 = 100 % = same as the primary. Above 1 the follower is brighter than the primary, clamped at 255.
SCALE_MIN = 0.1
SCALE_MAX = 2.0
SCALE_PCT_MIN = 10.0
SCALE_PCT_MAX = 200.0
SCALE_PCT_STEP = 5.0

# Per-step hue size above which the UI warns (degrees).
MAX_STEP_WARN = 4.0

SERVICE_SET_BRIGHTNESS = "set_brightness"
SERVICE_READOPT_FOLLOWERS = "readopt_followers"
ATTR_ONLY_OFF = "only_off"
ATTR_BRIGHTNESS_PCT = "brightness_pct"
ATTR_TRANSITION = "transition"

# Hooks for the test-suite (hass.data[DOMAIN]): "clock", "rng", "run_senders".
HOOKS = "_hooks"
