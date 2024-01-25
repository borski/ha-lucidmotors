# Lucid Motors integrations for Home Assistant (HACS)

[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]

[![Community Forum][forum-shield]][forum]

_Integration to integrate with [python-lucidmotors][python-lucidmotors]._

**This integration will set up the following platforms.**

Platform | Description
-- | --
`binary_sensor` | Tons of info from the car.
`climate` | Precondition interior climate
`sensor` | Yet more info from the car.
`button` | Various actions like honk horn, flash lights, etc.
`device_tracker` | Where's your car? :)
`light` | Turn headlights on and off (and actually, *really* off)
`lock` | Doors
`cover` | Frunk, Trunk
`number` | Set charge limit
`select` | Alarm mode (on, off, silent/push notifications only)
`switch` | Start/stop charging
`update` | Update notifications, release notes, and remote installation

## Installation
There are two methods to install this installation:

### HACS Installation (easiest)
1. Add `https://github.com/borski/ha-lucidmotors` as a custom repository, with the integration type
1. Restart Home Assistant
1. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Lucid Motors"

### Manual Installation
1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
1. If you do not have a `custom_components` directory (folder) there, you need to create it.
1. In the `custom_components` directory (folder) create a new folder called `lucidmotors`.
1. Download _all_ the files from the `custom_components/lucidmotors/` directory (folder) in this repository.
1. Place the files you downloaded in the new directory (folder) you created.
1. Restart Home Assistant
1. In the HA UI go to "Configuration" -> "Integrations" click "+" and search for "Lucid Motors"

## Configuration is done in the UI

<!---->

## Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md)

***

[python-lucidmotors]: https://github.com/nshp/python-lucidmotors
[commits-shield]: https://img.shields.io/github/commit-activity/y/borski/ha-lucidmotors.svg?style=for-the-badge
[commits]: https://github.com/borski/ha-lucidmotors/commits/main
[forum-shield]: https://img.shields.io/badge/community-forum-brightgreen.svg?style=for-the-badge
[forum]: https://community.home-assistant.io/
[license-shield]: https://img.shields.io/github/license/borski/ha-lucidmotors.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainers-Michael%20Borohovski%20%40borski,%20Nick%20Shipp%20%40nshp-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/borski/ha-lucidmotors.svg?style=for-the-badge
[releases]: https://github.com/ludeeus/integration_blueprint/releases
