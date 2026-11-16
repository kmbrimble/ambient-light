# Ambient Light

A Home Assistant integration that runs smooth, software-generated colour cycles on lights that cannot fade natively. It
drives one RGB light (the primary) and any number of follower lights from a single shared clock, and exposes one
virtual light per scene to switch it on and off.

## Install

Open the **Marketplace (formerly HACS)** in Home Assistant, add `https://github.com/kmbrimble/ambient-light` as a custom
repository (type: Integration), install **Ambient Light**, and restart Home Assistant.

## Configure

Settings > Devices & services > Add integration > **Ambient Light**. Choose a name (it becomes the virtual light, for
example `light.living_room_ambient`), the primary light, optional follower lights, and the cycle settings. Use **Configure** on the entry to change the set-once settings later. The everyday controls are entities on the device card and
apply at once, with no reload: **Colour mode**, **Blend**, **Colour change speed**, **Time between colours**, **Drift smallest / largest move**,
**Rainbow cycle time**, and a **vs main light** number per follower (10 to 200 % of the primary's brightness, capped at the light's maximum).

- **Effects:** Random, Drift and Rainbow, selectable on the virtual light.
- **Blend:** how Random and Drift move between colours. `Wheel` (default) sweeps the hue round the colour wheel; `OKLab` blends in
  a straight, perceptually even line with no detour through other hues. Switch any time with the **Blend** select entity
  (`select.<name>_blend`). Rainbow always uses the wheel.
- **Pause:** if something else changes or turns off the primary light, the cycle pauses. Turn the virtual light on to resume.
- **Command rate caps:** `primary_rate_cap` and `follower_rate_cap` (commands per second, 0.5 to 10) limit how fast
  commands are sent. The defaults suit most lights; lower the primary cap if a light drops offline during a cycle. Fades get
  slower, steps stay smooth, and changes apply without a reload. The configured caps show in the light's `rate_cap` attribute.
- **Services:**
  - `ambient_light.set_brightness` changes the brightness target without turning the light on or resuming a pause.
  - `ambient_light.readopt_followers` (`only_off`, default true) puts released followers back under the cycle's control,
    only those currently off by default. It never turns the light on or resumes a pause.

