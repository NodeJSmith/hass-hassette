# REVIEW.md — custom_components/hassette/

## Public Surface
Does the change alter any entity's `unique_id` (`app_unique_id` in `entity.py`), a key the config
flow stores in `entry.data`, or the `hassette.send` fields or `hassette_action` event payload? Each
of those is something users' entities, history or automations depend on. If one changed, does the
issue ask for it, and is there a migration?

## Error Table Order
`errors.py` maps client exceptions through ordered tables where the first matching class wins. Does
a new or moved row sit after its own base class (as `UnexpectedResponseError` must precede
`ResponseValidationError`), so it can never match? Is each new key in `translations/en.json` and in
the key sets `tests/test_translations.py` checks?

## Availability Parity
`HassetteCoordinator.app_list_trusted` is the one gate for whether hassette's app list can be acted
on: a good poll (within tolerance) taken after `bootstrap_released`. `HassetteAppEntity.available`
and `async_remove_config_entry_device` both check it. Does a new entity, action or removal path make
a decision about an app without going through `app_list_trusted`, re-deriving the gate inline or
using a weaker check, and so act on an app list that may be incomplete?

## Sibling Parity
`switch.py`, `button.py` and `sensor.py` each build entities through `add_app_entities`. Does a new
or changed platform go through it too, or does it add entities on its own and miss the registry
check that keeps a user-deleted entity from coming back?

## Action Metadata
For a new or changed action, do `services.yaml`, the `services` block of `translations/en.json`,
`icons.json` and the schema in `services.py` agree on the action's fields and which are required?
