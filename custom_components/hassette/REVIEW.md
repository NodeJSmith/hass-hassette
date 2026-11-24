# REVIEW.md — custom_components/hassette/

## Public Surface
Does the change alter any entity's `unique_id` (`app_unique_id` in `entity.py`, `declared_unique_id` in
`coordinator.py`), an instance device's identifier (`instance_identifier`), a key the config
flow stores in `entry.data`, or the `hassette.send` fields or `hassette_message` event payload? Each
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
and `async_remove_config_entry_device` both check it, as does `HassetteDeclaredEntity.available`. The
one exception is the coordinator's `remove_undeclared` diff, which runs inside the poll before
`self.data` is set and so gates on that poll's own `bootstrap_released`. Does a new entity, action or
removal path make a decision about an app without going through `app_list_trusted` (or, inside the
poll, the fresh health), re-deriving the gate inline or using a weaker check, and so act on an app
list that may be incomplete?

## Sibling Parity
`switch.py`, `button.py` and `sensor.py` each build app entities through `add_app_entities` and
declared entities through `add_declared_entities`. Does a new or changed platform go through them
too, or does it add entities on its own and miss the registry check that keeps a user-deleted entity
from coming back? For declared entities, does the add rule still agree with `may_add_declared`, which
the coordinator uses to register the parent app device first?

## Action Metadata
For a new or changed action, do `services.yaml`, the `services` block of `translations/en.json`,
`icons.json` and the schema in `services.py` agree on the action's fields and which are required?

## Push and Poll Merge
`coordinator.data` has two writers: the poll, through `merge_listing`, and the `/ws` stream, through
`apply_entity_push`. Revisions decide between them: a push older than the held listing or the entity's
last pushed revision is dropped, and a poll keeps any pushed value newer than its own listing. Does a new
or changed write to `coordinator.data` go through one of those two, or does it replace the data
directly and let an older value overwrite a newer one? Does a push path still avoid
`async_set_updated_data`, which postpones the next poll?
