"""Merchant-scoped acceptance configuration fence at the injected DB boundary.

Only semantic acceptance writes participate. Financial and unrelated fields
delegate unchanged. Provider calls never occur inside these transactions.
Privileged clients bypassing the application DB adapter are not governed here.
"""
import json
import inspect
from pymongo.collection import Collection

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

FENCES = "order_review_acceptance_config_versions"
FIELDS = {
    "settings": {"g47_inventory.component_lifecycle_starts_at", "g47_inventory.revision", "g47_inventory.version"},
    "mezan_products_v2": {"id", "salla_product_id", "mezan_product_id", "sku", "revision", "version"},
    "mezan_product_operation_profiles_v2": {"id", "salla_product_id", "fulfillment_type", "inventory_policy", "stockout_policy", "low_stock_threshold", "revision", "version"},
    "mezan_product_resource_bindings_v2": {"id", "salla_product_id", "resource_id", "quantity", "revision", "version"},
    "mezan_product_option_cost_bindings_v2": {"id", "salla_product_id", "mode", "resource_id", "quantity", "option_id", "value_id", "option_name", "value_name", "revision", "version"},
    "mezan_cost_resources_v2": {"id", "kind", "track_inventory", "requires_preparation", "revision", "version"},
    "order_review_preparation_assignment_defaults": {"product_key", "preparation_route", "assigned_employee_id", "revision", "version"},
}
MUTATIONS = {"insert_one", "insert_many", "update_one", "update_many", "replace_one",
             "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace", "find_one_and_delete"}


def _fail(code):
    raise HTTPException(409, detail={"code": code})


def _touches(name, method, args):
    if method not in {"update_one", "update_many", "find_one_and_update"}:
        return True
    update = args[1]
    if not isinstance(update, dict):
        return True
    fields = {key for values in update.values() if isinstance(values, dict) for key in values}
    fields.update(value for value in update.get("$rename", {}).values() if isinstance(value, str))
    return any(a == b or a.startswith(b + ".") or b.startswith(a + ".")
               for a in fields for b in FIELDS[name] | {"user_id"})


class AcceptanceConfigCollection:
    def __init__(self, db, name, collection=None):
        self._db, self._name = db, name
        self._collection = collection if collection is not None else db[name]

    @property
    def database(self):
        return AcceptanceConfigDatabase(self._db)

    def with_options(self, *args, **kwargs):
        return AcceptanceConfigCollection(self._db, self._name, self._collection.with_options(*args, **kwargs))

    def __getattr__(self, method):
        if method == "aggregate":
            def aggregate(pipeline, *args, **kwargs):
                if any("$merge" in stage or "$out" in stage for stage in pipeline):
                    _fail("acceptance_config_aggregation_write_forbidden")
                return self._collection.aggregate(pipeline, *args, **kwargs)
            return aggregate
        if method == "bulk_write":
            async def reject_bulk(*args, **kwargs):
                _fail("acceptance_config_bulk_requires_scoped_transaction")
            return reject_bulk
        if method not in MUTATIONS:
            return getattr(self._collection, method)

        async def mutate(*args, **kwargs):
            parameters = (["documents"] if method == "insert_many" else ["document"]
                          if method == "insert_one" else ["filter", "replacement"]
                          if method in {"replace_one", "find_one_and_replace"} else ["filter", "update"]
                          if method in {"update_one", "update_many", "find_one_and_update"} else ["filter"])
            # Motor mirrors PyMongo positional options, including upsert and
            # session. Normalize them before deciding whether to fence a write.
            optional = list(inspect.signature(getattr(Collection, method)).parameters)[1 + len(parameters):]
            if len(args) > len(parameters):
                for key, value in zip(optional, args[len(parameters):]):
                    if key in kwargs:
                        raise TypeError(f"duplicate argument: {key}")
                    kwargs[key] = value
                if len(args) - len(parameters) > len(optional):
                    raise TypeError("too many positional arguments")
            args = list(args[:len(parameters)])
            for parameter in parameters[len(args):]:
                if parameter not in kwargs:
                    _fail("acceptance_config_mutation_arguments_required")
                args.append(kwargs.pop(parameter))
            if not kwargs.get("upsert") and not _touches(self._name, method, args):
                return await getattr(self._collection, method)(*args, **kwargs)
            insert = method in {"insert_one", "insert_many"}
            docs = (args[0] if method == "insert_many" else [args[0]]) if insert else []
            owners = [doc.get("user_id") for doc in docs] if insert else [args[0].get("user_id")]
            if not owners or any(not isinstance(value, str) or not value for value in owners):
                _fail("acceptance_config_owner_required")
            if len(set(owners)) != 1:
                _fail("acceptance_config_owner_required")
            owner = owners[0]
            if method in {"replace_one", "find_one_and_replace"} and args[1].get("user_id") != owner:
                _fail("acceptance_config_owner_conflict")
            if method in {"update_one", "update_many", "find_one_and_update"}:
                update = args[1]
                if not isinstance(update, dict):
                    _fail("acceptance_config_pipeline_not_supported")
                for operator, values in update.items():
                    if not isinstance(values, dict):
                        _fail("acceptance_config_update_invalid")
                    for field, value in values.items():
                        if (field == "user_id" and (operator not in {"$set", "$setOnInsert"} or value != owner)
                                or field.startswith("user_id.")
                                or operator == "$rename" and value == "user_id"):
                            _fail("acceptance_config_owner_conflict")

            async def commit(session):
                # Write before reading: same document as completion, including
                # creates/deletes of previously absent config rows.
                await self._db[FENCES].update_one({"_id": owner, "user_id": owner}, {"$inc": {"fence": 1}},
                                                  upsert=True, session=session)
                query = {"user_id": owner} if insert else args[0]
                projection = {key: 1 for key in FIELDS[self._name]}
                async def read(selector):
                    rows = await self._collection.find(selector, projection, session=session).to_list(10001)
                    if len(rows) > 10000:
                        _fail("acceptance_config_write_too_large")
                    return rows
                # Inserts only need their new identities; old rows are unchanged.
                before = [] if insert else await read(query)
                result = await getattr(self._collection, method)(*args, **{**kwargs, "session": session})
                ids = [row["_id"] for row in before]
                if method == "insert_one":
                    ids.append(result.inserted_id)
                elif method == "insert_many":
                    ids.extend(result.inserted_ids)
                elif getattr(result, "upserted_id", None) is not None:
                    ids.append(result.upserted_id)
                after_query = {"user_id": owner, "_id": {"$in": ids}} if insert else {
                    "$and": [{"user_id": owner}, {"$or": [query, {"_id": {"$in": ids}}]}]}
                after = await read(after_query)
                normalize = lambda rows: sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
                # Find-and-modify upserts return a document (or None), not an
                # upserted_id. A successful upsert with no prior match created
                # a row even when its new values no longer match the selector.
                inserted_by_upsert = bool(kwargs.get("upsert")) and not before
                if inserted_by_upsert or normalize(before) != normalize(after):
                    await self._db[FENCES].update_one({"_id": owner, "user_id": owner}, {"$inc": {"version": 1}}, session=session)
                return result

            existing = kwargs.get("session")
            if existing is not None:
                if not existing.in_transaction:
                    _fail("acceptance_config_transaction_required")
                return await commit(existing)
            hello = await self._db.command("hello")
            if not hello.get("setName"):
                raise HTTPException(503, detail={"code": "acceptance_config_replica_set_required"})
            async with await self._db.client.start_session() as session:
                return await session.with_transaction(commit, read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority", j=True), read_preference=ReadPreference.PRIMARY)
        return mutate


class AcceptanceConfigDatabase:
    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        return AcceptanceConfigCollection(self._db, name) if name in FIELDS else self._db[name]

    def __getattr__(self, name):
        return self[name] if name in FIELDS else getattr(self._db, name)

    def get_collection(self, name, *args, **kwargs):
        if name in FIELDS and (args or kwargs):
            _fail("acceptance_config_collection_options_not_supported")
        return self[name] if name in FIELDS else self._db.get_collection(name, *args, **kwargs)

    def with_options(self, *args, **kwargs):
        return AcceptanceConfigDatabase(self._db.with_options(*args, **kwargs))
