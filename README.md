# Ambient Light

A Home Assistant integration that runs smooth, software-generated colour cycles on lights that cannot fade natively. It
drives one RGB light (the primary) and any number of follower lights from a single shared clock, and exposes one
virtual light per scene to switch it on and off.

## Install

Open the **Marketplace (formerly HACS)** in Home Assistant, add `https://github.com/kmbrimble/ambient-light` as a custom
repository (type: Integration), install **Ambient Light**, and restart Home Assistant.

## Configure

Settings > Devices & services > Add integration > **Ambient Light**. Choose a name (it becomes the virtual light, for
example `light.living_room_ambient`), the primary light, optional follower lights, and the cycle settings. Each follower
has its own brightness scale and Rainbow phase offset. Use **Configure** on the entry to change anything later.

- **Effects:** Random, Drift and Rainbow, selectable on the virtual light.
- **Pause:** if something else changes or turns off the primary light, the cycle pauses. Turn the virtual light on to resume.
- **Service:** `ambient_light.set_brightness` changes the brightness target without turning the light on or resuming a pause.

