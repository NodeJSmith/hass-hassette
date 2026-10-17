"""Tests for pairing with the hassette add-on through Supervisor discovery, and refreshing from it."""

from collections.abc import Generator
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import AuthenticationError, HassetteConnectionError, HassetteTimeoutError
from homeassistant.components.hassio import AddonError
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import ADDON_NAME, CONF_ADDON_SLUG, DOMAIN

from .conftest import ENTRY_ID, SWITCH, http_error, make_health, poll

SLUG = "local_hassette"
ADDON_URL = "http://local-hassette:8126"
ADDON_TOKEN = "announced-token"  # noqa: S105 - a test fixture, not a credential
NEW_TOKEN = "rotated-token"  # noqa: S105 - a test fixture, not a credential
ANNOUNCE = {"host": "local-hassette", "port": 8126, "auth_token": ADDON_TOKEN}


def service_info(slug: str = SLUG, config: dict | None = None) -> HassioServiceInfo:
    return HassioServiceInfo(config=config or ANNOUNCE, name="Hassette", slug=slug, uuid="1234")


async def start_hassio_flow(hass: HomeAssistant, info: HassioServiceInfo) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=info)


@pytest.fixture
def addon_manager() -> Generator[MagicMock]:
    """Supervisor, as Core's ``AddonManager`` reads it: the add-on announces ``ANNOUNCE``."""
    instance = MagicMock()
    instance.async_get_addon_discovery_info = AsyncMock(return_value=dict(ANNOUNCE))
    with (
        patch("custom_components.hassette.addon.is_hassio", return_value=True),
        patch("custom_components.hassette.addon.AddonManager", return_value=instance) as factory,
    ):
        instance.factory = factory
        yield instance


@pytest.fixture
def discovered_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        source=SOURCE_HASSIO,
        title="Hassette",
        data={CONF_URL: ADDON_URL, CONF_API_TOKEN: ADDON_TOKEN, CONF_VERIFY_SSL: True, CONF_ADDON_SLUG: SLUG},
    )


@pytest.fixture
async def loaded_discovered_entry(
    hass: HomeAssistant, client: MagicMock, discovered_entry: MockConfigEntry
) -> MockConfigEntry:
    discovered_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(discovered_entry.entry_id)
    await hass.async_block_till_done()
    return discovered_entry


def pending_flow_sources(hass: HomeAssistant) -> list[str]:
    return [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()]


def fail_once(client: MagicMock, error: Exception) -> None:
    """Make the next health read fail with ``error`` and every later one succeed."""
    client.get_health.side_effect = [error, *[make_health()] * 10]


async def test_discovery_confirms_then_creates_entry_with_slug(hass: HomeAssistant, client: MagicMock) -> None:
    result = await start_hassio_flow(hass, service_info())
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_URL: ADDON_URL,
        CONF_API_TOKEN: ADDON_TOKEN,
        CONF_VERIFY_SSL: True,
        CONF_ADDON_SLUG: SLUG,
    }


async def test_discovery_from_another_addon_is_refused(hass: HomeAssistant, client: MagicMock) -> None:
    result = await start_hassio_flow(hass, service_info(slug="core_mariadb"))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_hassette_addon"


async def test_discovery_without_host_aborts_before_offering_a_card(hass: HomeAssistant, client: MagicMock) -> None:
    result = await start_hassio_flow(hass, service_info(config={"port": 8126}))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_discovery_shows_connection_errors_on_confirm(hass: HomeAssistant, client: MagicMock) -> None:
    client.get_health.side_effect = HassetteConnectionError("refused")
    result = await start_hassio_flow(hass, service_info())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_second_announcement_while_a_card_is_pending_is_aborted(hass: HomeAssistant, client: MagicMock) -> None:
    await start_hassio_flow(hass, service_info())
    result = await start_hassio_flow(hass, service_info(config={**ANNOUNCE, "auth_token": NEW_TOKEN}))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"
    assert len(hass.config_entries.flow.async_progress()) == 1


async def test_discovery_with_an_entry_is_aborted_by_core(
    hass: HomeAssistant, client: MagicMock, loaded_discovered_entry: MockConfigEntry
) -> None:
    result = await start_hassio_flow(hass, service_info())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_rejected_token_picks_up_the_announced_token(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_discovered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    addon_manager.async_get_addon_discovery_info.return_value = {**ANNOUNCE, "auth_token": NEW_TOKEN}
    fail_once(client, http_error(AuthenticationError, 401))
    await poll(hass, freezer)

    addon_manager.factory.assert_called_once_with(hass, ANY, ADDON_NAME, SLUG)
    assert loaded_discovered_entry.data[CONF_API_TOKEN] == NEW_TOKEN
    assert client.factory.call_args.kwargs["token"] == NEW_TOKEN
    assert hass.config_entries.flow.async_progress() == []
    assert hass.states.get(SWITCH).state == STATE_ON


@pytest.mark.parametrize("error", [HassetteConnectionError("refused"), HassetteTimeoutError("no answer")])
async def test_unreachable_server_picks_up_the_announced_host(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_discovered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    error: Exception,
) -> None:
    addon_manager.async_get_addon_discovery_info.return_value = {**ANNOUNCE, "host": "abc123-hassette"}
    # The first failed poll is tolerated and never reads the add-on; the second one does.
    client.get_health.side_effect = error
    await poll(hass, freezer)
    addon_manager.async_get_addon_discovery_info.assert_not_called()
    fail_once(client, error)
    await poll(hass, freezer)

    assert loaded_discovered_entry.data[CONF_URL] == "http://abc123-hassette:8126"
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_rejected_token_with_unchanged_announcement_starts_reauth(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_discovered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    await poll(hass, freezer)

    assert pending_flow_sources(hass) == [SOURCE_REAUTH]


async def test_unreadable_discovery_info_starts_reauth(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_discovered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    addon_manager.async_get_addon_discovery_info.side_effect = AddonError("no discovery")
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    await poll(hass, freezer)

    assert pending_flow_sources(hass) == [SOURCE_REAUTH]


async def test_manual_entry_never_reads_discovery(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    await poll(hass, freezer)

    addon_manager.factory.assert_not_called()
    assert pending_flow_sources(hass) == [SOURCE_REAUTH]


async def test_stale_token_at_startup_picks_up_the_announced_token(
    hass: HomeAssistant, client: MagicMock, addon_manager: MagicMock, discovered_entry: MockConfigEntry
) -> None:
    addon_manager.async_get_addon_discovery_info.return_value = {**ANNOUNCE, "auth_token": NEW_TOKEN}
    fail_once(client, http_error(AuthenticationError, 401))
    discovered_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(discovered_entry.entry_id)
    await hass.async_block_till_done()

    assert discovered_entry.state is ConfigEntryState.LOADED
    assert discovered_entry.data[CONF_API_TOKEN] == NEW_TOKEN
    assert client.factory.call_args.kwargs["token"] == NEW_TOKEN
    assert hass.config_entries.flow.async_progress() == []


async def reconfigure(hass: HomeAssistant, entry: MockConfigEntry, url: str, token: str) -> dict:
    result = await entry.start_reconfigure_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: url, CONF_API_TOKEN: token, CONF_VERIFY_SSL: True}
    )


async def test_reconfigure_with_the_same_url_keeps_following_the_addon(
    hass: HomeAssistant, client: MagicMock, loaded_discovered_entry: MockConfigEntry
) -> None:
    result = await reconfigure(hass, loaded_discovered_entry, ADDON_URL, NEW_TOKEN)

    assert result["reason"] == "reconfigure_successful"
    assert loaded_discovered_entry.data[CONF_API_TOKEN] == NEW_TOKEN
    assert loaded_discovered_entry.data[CONF_ADDON_SLUG] == SLUG
    assert loaded_discovered_entry.title == "Hassette"


async def test_reconfigure_with_a_new_url_stops_following_the_addon(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_discovered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    proxy_url = "https://hassette.example.com"
    result = await reconfigure(hass, loaded_discovered_entry, proxy_url, ADDON_TOKEN)
    assert result["reason"] == "reconfigure_successful"
    assert CONF_ADDON_SLUG not in loaded_discovered_entry.data

    # An outage past the tolerance window no longer reverts the entry to the add-on's address.
    client.get_health.side_effect = HassetteConnectionError("refused")
    await poll(hass, freezer)
    await poll(hass, freezer)

    addon_manager.factory.assert_not_called()
    assert loaded_discovered_entry.data[CONF_URL] == proxy_url


async def test_announcement_without_host_starts_reauth(
    hass: HomeAssistant,
    client: MagicMock,
    addon_manager: MagicMock,
    loaded_discovered_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    addon_manager.async_get_addon_discovery_info.return_value = {"port": 8126, "auth_token": NEW_TOKEN}
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    await poll(hass, freezer)

    assert loaded_discovered_entry.data[CONF_API_TOKEN] == ADDON_TOKEN
    assert pending_flow_sources(hass) == [SOURCE_REAUTH]


async def test_without_supervisor_a_rejected_token_starts_reauth(
    hass: HomeAssistant, client: MagicMock, loaded_discovered_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """An entry paired with the add-on, restored onto a Home Assistant without Supervisor."""
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    await poll(hass, freezer)

    assert pending_flow_sources(hass) == [SOURCE_REAUTH]
