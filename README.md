# Hassette for Home Assistant

A Home Assistant custom integration for [Hassette](https://github.com/NodeJSmith/hassette), the async
automation framework. It connects Home Assistant to one hassette server and gives each hassette app a
device with three entities:

| Entity | Example | What it does |
|---|---|---|
| Switch | `switch.motion_lights` | On while the app runs. Turn it off to stop the app, on to start it. |
| Reload button | `button.motion_lights_reload` | Stops the app, re-reads its config, and starts it again. |
| Status sensor | `sensor.motion_lights_status` | The app's lifecycle status (below). |

All app devices sit under a **Hassette** hub device, which shows the server's version and links to
hassette's web UI. Each app device's **Visit** link opens that app's page in hassette.

## Requirements

- Home Assistant 2026.9.0 or newer
- [HACS](https://hacs.xyz)
- A hassette server that Home Assistant can reach over HTTP. hassette 0.56 or newer: an older hassette
  serves an API this integration can't use, and Home Assistant raises a repair telling you to upgrade.

## Install

1. In HACS, open the menu → **Custom repositories**, add `https://github.com/NodeJSmith/hass-hassette`
   with type **Integration**.
2. Find **Hassette** in HACS, download it, and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**, pick **Hassette**, and fill in:

| Field | What to enter |
|---|---|
| URL | hassette's address with its port, such as `http://192.168.1.10:8126`. Include any path prefix if hassette sits behind a reverse proxy at a sub-path. Don't put a username or password in the URL. |
| API token | hassette's web API token: `web_api.auth_token` from hassette's config, or the contents of `<data_dir>/.web_api_token` that hassette writes on first start. |
| Verify SSL certificate | Leave on unless hassette serves HTTPS with a self-signed certificate. |

Only one hassette server can be added.

### Token or trusted proxy

You can leave the token blank if hassette lists Home Assistant's address in `web_api.trusted_proxies`.
hassette then admits any request from that address that carries no token. That means everything else
at Home Assistant's address (other add-ons, other containers sharing its IP) gets full access to
hassette's API too. A token limits access to what holds the token, so prefer it.

## Entities

Each app is a device, named after the app's display name, under a **Hassette** hub device. The
device and its entities are tied to the app's key in hassette's config (`app_key`), not to its name:

- Renaming the app's display name in hassette renames the device on the next poll. Entity ids keep
  the names they were created with, so automations keep working.
- Renaming the `app_key` makes it a different app to Home Assistant: a new device appears, and the
  old one stays with its entities unavailable until you delete it.
- Two apps with the same display name get entity ids with a `_2` suffix on the second one.

### Status sensor states

| State | Meaning |
|---|---|
| `running` | Every instance of the app is running. |
| `degraded` | Some instances run and at least one has failed. |
| `failed` | Every instance has failed. The `error_message` attribute holds the error. |
| `stopped` | The app isn't running: never started, or stopped. |
| `disabled` | The app is disabled in hassette's config. |
| `blocked` | hassette prevented the app from starting, for example with its `--app` filter. |
| `unknown` | hassette reported a status newer than this integration knows. The raw value is in the `server_status` attribute. Update the integration. |

While an app is `failed` or `degraded`, the sensor's `error_message` attribute shows hassette's error
message, cut to 300 characters. Neither `error_message` nor `server_status` is stored in Home
Assistant's history.

### Switch and button

The switch is on for `running` and `degraded`, and off for `stopped` and `failed`. The switch and the
reload button are unavailable while the app is `disabled`, `blocked` or `unknown`, since hassette
can't start them from here.

A start or reload waits for the app to finish initializing, up to 45 seconds. If hassette doesn't
answer in time, Home Assistant reports that the outcome is unknown, and the app's real state shows up
on the next refresh.

### Updates

The integration polls hassette every 30 seconds, and again right after every switch or button
action. One failed poll is ignored; after two in a row, the app entities become unavailable until
hassette answers again.

## Known limitations

- **A stop doesn't survive a hassette restart.** Stopping an app from Home Assistant stops it until
  hassette restarts, then hassette starts it again as its config says. To keep an app off, disable it
  in hassette's config.
- **Apps are controlled as a whole.** An app with several instances has one device, and the switch
  starts or stops all of them.
- **Removed apps keep their devices.** When an app leaves hassette's config, its device stays, with
  its entities unavailable, so a temporary config change doesn't lose your areas and customizations.
  To delete it, open the device and choose **Delete**. That option is offered only once hassette no
  longer has the app in its config. If the app comes back, it reuses its device.
- **While hassette starts up,** every app entity is unavailable until hassette lets apps start.
  Removing an app's device is also refused until then, and while hassette is unreachable, since its
  app list may be incomplete.

## Troubleshooting

**Failed to connect, or timed out connecting.** Check that the URL works from where Home Assistant
runs, not just from your browser. For Home Assistant OS, open the Terminal add-on and run
`curl -s <url>/api/health/live`.

**hassette's API redirected to ….** Something in front of hassette, usually a forward-auth proxy
such as Cloudflare Access, Authelia or Authentik, sent Home Assistant to its login page. hassette's
own token already guards the API, so add a bypass rule for the `/api/` path on that proxy, or point
the integration at an address that skips the proxy (hassette's LAN address). If the redirect target
is just the `https://` form of your URL, use that URL instead.

**Something other than hassette answered the request.** A switch or button press got back a page
that isn't hassette's JSON, typically a proxy's login page answering with success. The action may not
have run. Add the same `/api/` bypass as for a redirect, above.

**hassette's API refused the request (403).** A firewall or proxy rule in front of hassette is
blocking Home Assistant. hassette itself answers a bad token with 401, not 403.

**hassette rejected the token.** Home Assistant asks for a new token (**Reconfigure** or the
re-authentication prompt). Copy the current one from hassette's config or `.web_api_token` file.

**A "hassette is too old" repair.** Upgrade hassette. The integration retries on its own and clears
the repair once hassette answers with a supported version.

**More detail.** Open the integration's page and choose **Enable debug logging**. That covers the
integration and the `hassette_client` library. Reproduce the problem, then disable debug logging to
download the log.

## Changing the URL or token

Open the integration's page and choose **Reconfigure**. Your devices and their settings are kept.

## Remove

1. **Settings → Devices & services → Hassette →** the menu → **Delete**.
2. To remove the files too, delete **Hassette** in HACS and restart Home Assistant.

Nothing needs undoing on hassette's side, unless you added Home Assistant to `web_api.trusted_proxies`
for this integration.

## Development

The repo uses [mise](https://mise.jdx.dev), [uv](https://docs.astral.sh/uv/) and
[prek](https://prek.j178.dev):

```bash
mise install                              # Python, uv and prek at the pinned versions
uv sync                                   # dev and test dependencies, including Home Assistant
prek install -t pre-commit -t pre-push    # lint on commit; pyright and a secret scan on push
uv run pytest
```

CI runs the tests against the newest Home Assistant and against the oldest one `hacs.json` claims,
plus hassfest and HACS validation. Releases are cut by release-please; each GitHub release carries the
`hassette.zip` that HACS installs.
