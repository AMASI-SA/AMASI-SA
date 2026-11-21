#!/usr/bin/env python3
"""Read-only PR #2039 external-party index and legacy-name preflight.

Run with an isolated environment containing pymongo==4.18.3 and
dnspython==2.9.2. No application module or dotenv file is loaded. The URI must
be independently confirmed from Emergent Production, not its preview terminal.
It is requested without echo; it is never accepted in argv or environment.

Example:
    python -B check_production_indexes.py \
        --database salla-analytics-test_database

Exit 0 means this bounded collection check passed, not approval to publish.
Exit 2 means blocked or unverified. No index, document, or lease is changed.
"""

import argparse
from collections import Counter
import getpass
from importlib.metadata import version
import ipaddress
import json
import logging
import re
import sys
from urllib.parse import unquote, urlsplit
import warnings


DATABASE = "salla-analytics-test_database"
COLLECTION = "mz2_external_persons_v2"
MAX_ROWS = 10000


def emit(result):
    print(json.dumps(result, sort_keys=True))


class SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's ordinary error could echo an accidentally supplied URI.
        emit({"status": "ERROR", "reason": "INVALID_ARGUMENTS"})
        raise SystemExit(2)


def production_uri_allowed(uri):
    """Reject obvious local/preview targets, without claiming remote identity."""
    parsed = urlsplit(uri)
    if parsed.scheme not in {"mongodb", "mongodb+srv"} or not parsed.netloc:
        return False
    if parsed.fragment:
        return False
    if unquote(parsed.path).strip("/") not in {"", "admin", DATABASE}:
        return False
    seeds = parsed.netloc.rsplit("@", 1)[-1].split(",")
    for seed in seeds:
        host = unquote(urlsplit("//" + seed).hostname or "").lower().rstrip(".")
        if not host or "/" in host or "\\" in host:
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                "." not in host
                or host == "host.docker.internal"
                or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host)
                or host.startswith(("localhost.", "127.", "0.", "169.254."))
                or host.endswith((".localhost", ".local", ".test", ".invalid",
                                  ".example", ".svc", ".cluster.local"))
                or re.search(r"(^|[.-])preview([.-]|$)", host)
            ):
                return False
        else:
            address = getattr(address, "ipv4_mapped", None) or address
            if address.is_loopback or address.is_unspecified or address.is_link_local:
                return False
    return True


def full_unique(indexes, fields):
    return any(
        list(index["key"].items()) == [(field, 1) for field in fields]
        and index.get("unique") is True
        and not index.get("sparse", False)
        and "partialFilterExpression" not in index
        and index.get("collation", {}).get("locale", "simple") == "simple"
        for index in indexes
    )


def valid_text(value):
    return isinstance(value, str) and bool(value.strip())


def inspect_collection(client, database):
    from pymongo.read_concern import ReadConcern

    col = client[database].get_collection(
        COLLECTION, read_concern=ReadConcern("majority"))
    before_indexes = list(col.list_indexes())
    before_count = col.count_documents({}, limit=MAX_ROWS + 1, maxTimeMS=20000)
    result = {
        "status": "BLOCKED",
        "index_count": len(before_indexes),
        "full_unique_name_index": full_unique(
            before_indexes, ("user_id", "name_lower")),
        "full_unique_id_index": full_unique(before_indexes, ("user_id", "id")),
        "entities_count_bounded": before_count,
        "row_limit_exceeded": before_count > MAX_ROWS,
    }
    if before_count > MAX_ROWS:
        result["reason"] = "BOUNDED_SCAN_LIMIT"
        return result

    # At most MAX_ROWS metadata records are fetched; no financial field is read.
    with col.find({}, {"_id": 0, "user_id": 1, "name": 1,
                       "display_name": 1, "name_lower": 1}) \
            .max_time_ms(20000).limit(MAX_ROWS) as cursor:
        rows = list(cursor)
    after_count = col.count_documents({}, limit=MAX_ROWS + 1, maxTimeMS=20000)
    after_indexes = list(col.list_indexes())
    result["entities_after_count_bounded"] = after_count
    result["row_limit_exceeded"] = max(before_count, after_count) > MAX_ROWS
    result["complete_scan"] = before_count == len(rows) == after_count
    result["index_definitions_unchanged"] = (
        sorted(before_indexes, key=lambda item: item["name"])
        == sorted(after_indexes, key=lambda item: item["name"]))

    stored_keys, normalized_keys = Counter(), Counter()
    invalid_owner = missing_name = missing_normalized_name = mismatches = 0
    fallback_names = 0
    for row in rows:
        owner, name, stored = row.get("user_id"), row.get("name"), row.get("name_lower")
        if not valid_text(name) and valid_text(row.get("display_name")):
            name = row["display_name"]
            fallback_names += 1
        owner_ok, name_ok, stored_ok = map(valid_text, (owner, name, stored))
        invalid_owner += not owner_ok
        missing_name += not name_ok
        missing_normalized_name += not stored_ok
        if owner_ok and stored_ok:
            stored_keys[(owner, stored)] += 1
        if name_ok:
            normalized = " ".join(name.split()).casefold()
            if owner_ok:
                normalized_keys[(owner, normalized)] += 1
            if stored_ok and stored != normalized:
                mismatches += 1

    result.update(
        rows_scanned=len(rows), invalid_owner_rows=invalid_owner,
        missing_or_invalid_name_rows=missing_name,
        missing_or_invalid_name_lower_rows=missing_normalized_name,
        display_name_fallback_rows=fallback_names,
        legacy_normalization_mismatch_rows=mismatches,
        stored_duplicate_groups=sum(n > 1 for n in stored_keys.values()),
        normalized_duplicate_groups=sum(n > 1 for n in normalized_keys.values()),
    )
    passed = (
        result["full_unique_name_index"] and result["full_unique_id_index"]
        and result["complete_scan"] and result["index_definitions_unchanged"]
        and bool(rows)  # Production was independently observed with seven rows.
        and not any((invalid_owner, missing_name, missing_normalized_name,
                     mismatches, result["stored_duplicate_groups"],
                     result["normalized_duplicate_groups"]))
    )
    result["status"] = "PASS" if passed else "BLOCKED"
    return result


def main():
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--database", required=True, choices=[DATABASE])
    args = parser.parse_args()
    client = None
    try:
        # Third-party diagnostics must not write connection details to stderr.
        logging.getLogger("pymongo").setLevel(logging.CRITICAL + 1)
        import pymongo

        if version("pymongo") != "4.18.3" or version("dnspython") != "2.9.2":
            emit({"status": "ERROR", "reason": "DEPENDENCY_VERSION_MISMATCH"})
            return 2
        with warnings.catch_warnings():
            # getpass warns BEFORE its insecure fallback; make that a hard stop.
            warnings.simplefilter("error", getpass.GetPassWarning)
            uri = getpass.getpass("Confirmed Production MongoDB URI (hidden): ")
        if not production_uri_allowed(uri):
            emit({"status": "BLOCKED", "reason": "URI_TARGET_NOT_ACCEPTED"})
            return 2
        with warnings.catch_warnings():
            # Invalid URI options must not be echoed by third-party warnings.
            warnings.simplefilter("error")
            client = pymongo.MongoClient(
                uri, connect=False, serverSelectionTimeoutMS=8000,
                connectTimeoutMS=8000, socketTimeoutMS=20000, timeoutMS=20000,
                retryWrites=False, retryReads=False, maxPoolSize=1,
                appname="mezan-pr2039-index-readonly")
            del uri
            with pymongo.timeout(20):
                result = inspect_collection(client, args.database)
        emit(result)
        return 0 if result["status"] == "PASS" else 2
    except getpass.GetPassWarning:
        emit({"status": "ERROR", "reason": "SECURE_INPUT_UNAVAILABLE"})
    except (EOFError, KeyboardInterrupt):
        emit({"status": "ERROR", "reason": "INPUT_CANCELLED"})
    except Exception as exc:
        emit({"status": "ERROR", "error_type": type(exc).__name__})
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return 2


if __name__ == "__main__":
    sys.exit(main())
