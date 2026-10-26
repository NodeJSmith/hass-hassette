# Changelog

## [0.4.0](https://github.com/NodeJSmith/hass-hassette/compare/v0.3.0...v0.4.0) (2026-10-26)

### Breaking Changes

- **Requires hassette 0.60 or newer.** The integration now uses hassette-client 0.60.0, which needs the API schema first served by hassette 0.60.0. Upgrade your hassette server before updating the integration. With an older server, Home Assistant raises a repair asking you to upgrade it. (#26)

## [0.3.0](https://github.com/NodeJSmith/hass-hassette/compare/v0.2.0...v0.3.0) (2026-10-19)

### Breaking Changes

- **Requires hassette 0.58 or newer.** hassette 0.59 stopped sending the `role` field in its health response, which v0.2.0 required, so setup failed with `cannot_connect` against hassette 0.59. The integration now uses hassette-client 0.59.0, which reads the new response, and needs the API schema first served by hassette 0.58.0. With an older server, Home Assistant raises a repair asking you to upgrade it. (#21)

## [0.2.0](https://github.com/NodeJSmith/hass-hassette/compare/v0.1.0...v0.2.0) (2026-10-17)

### Features

- **Pair with the hassette add-on in one step.** On Home Assistant OS, the hassette add-on announces itself through Supervisor discovery, so a **Discovered** card appears and you pair with one confirmation instead of typing a hostname and token. After pairing, the integration follows the add-on: if the add-on's URL or token changes, it picks up the new connection without any input from you. Entries you set up by hand never contact Supervisor. (#13)

### Bug Fixes

- Updated the hassette client to 0.57.0. (#11)

## [0.1.0](https://github.com/NodeJSmith/hass-hassette/releases/tag/v0.1.0) (2026-10-09)

### Features

- **First release.** Connect Home Assistant to a hassette server and each hassette app appears as a device, with sensor, switch, and button entities kept current by a single polling request per interval. Server errors show as actionable Home Assistant messages instead of raw tracebacks. Installable through HACS. (#2)
