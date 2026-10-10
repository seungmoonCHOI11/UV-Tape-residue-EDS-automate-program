"""Durable Point persistence and end-of-job reconciliation.

v23.7.47 deliberately treats a Point as complete only after its database row,
analysis row and every generated image asset are verifiably present.  Writes are
idempotent, so a transient network error can safely retry the whole Point.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclass
class PersistenceError(RuntimeError):
    stage: str
    detail: str
    attempts: int = 1

    def __post_init__(self):
        RuntimeError.__init__(self, f"{self.stage}: {self.detail}")


def _digits(value: Any) -> int:
    m = re.search(r"\d+", str(value or ""))
    return int(m.group(0)) if m else 0


def record_key(record: dict) -> tuple[int, int, int, int]:
    return (
        _digits(record.get("power")),
        _digits(record.get("time")),
        int(record.get("wafer") or 0),
        int(record.get("point") or 0),
    )


def manifest_key(meta: dict) -> tuple[int, int, int, int]:
    return (
        int(meta.get("power") or 0),
        int(meta.get("time") or meta.get("time_sec") or 0),
        int(meta.get("wafer") or 0),
        int(meta.get("point") or 0),
    )


def manifest_id(meta: dict) -> str:
    p, t, w, pt = manifest_key(meta)
    return f"{p}W_{t}s_W{w}_P{pt}"


def build_manifest(sequence: Iterable[dict]) -> list[dict]:
    out = []
    for i, meta in enumerate(sequence, 1):
        row = dict(meta)
        row["sequence_index"] = i
        row["id"] = manifest_id(row)
        out.append(row)
    return out


def _notify(callback: Callable | None, **payload):
    if callback:
        try:
            callback(payload)
        except Exception:
            pass


def persist_point(
    project_id: str,
    record: dict,
    db_module,
    r2_storage,
    *,
    attempts: int = 3,
    backoff: tuple[float, ...] = (1.0, 2.0, 4.0),
    sleep_fn: Callable[[float], None] = time.sleep,
    verify: bool = True,
    status_callback: Callable | None = None,
) -> dict:
    """Persist one Point with bounded retry and read-after-write verification.

    The whole operation is intentionally idempotent: R2 keys are deterministic and
    Supabase writes use the Power/Time/Wafer/Point unique key.  Therefore a request
    whose response was lost after the server committed can simply be repeated.
    """
    source_id = str(record.get("source_point_id") or record.get("manifest_id") or record.get("id"))
    record["source_point_id"] = source_id
    power, time_sec, wafer, point = record_key(record)
    if not all([power, time_sec, wafer, point]):
        raise PersistenceError("manifest", f"Invalid Point key for {source_id}")

    last_stage = "save"
    last_exc: Exception | None = None
    max_attempts = max(1, int(attempts))

    for attempt in range(1, max_attempts + 1):
        try:
            r2_assets = {}
            prefix = f"projects/{project_id}/points/{source_id}/"

            last_stage = "r2_upload"
            for asset_type, local_path in (record.get("assets") or {}).items():
                lp = Path(local_path)
                if not lp.exists():
                    raise FileNotFoundError(f"Generated asset missing before upload: {asset_type} -> {lp}")
                ext = lp.suffix.lower() or ".jpg"
                key = f"{prefix}{asset_type}{ext}"
                r2_storage.upload_file(lp, key, "image/jpeg")
                r2_assets[asset_type] = key
            record["r2_assets"] = r2_assets

            last_stage = "supabase_point"
            row = db_module.upsert_point(project_id, record)
            db_id = str(row["id"])

            last_stage = "supabase_analysis"
            db_module.upsert_analysis(db_id, record.get("features") or {})

            last_stage = "supabase_assets"
            for asset_type, key in r2_assets.items():
                db_module.upsert_asset(db_id, asset_type, key)

            if verify:
                last_stage = "verify_db"
                stored = db_module.get_point_by_key(project_id, power, time_sec, wafer, point)
                if not stored:
                    raise RuntimeError("Point row was not found after upsert.")
                verified_id = str(stored["id"])
                analysis = db_module.get_analysis(verified_id)
                if not analysis:
                    raise RuntimeError("Analysis row was not found after upsert.")
                db_assets = {a["asset_type"]: a["storage_path"] for a in db_module.get_assets(verified_id)}
                missing_db_assets = sorted(set(r2_assets) - set(db_assets))
                if missing_db_assets:
                    raise RuntimeError(f"Asset rows missing after upsert: {', '.join(missing_db_assets)}")

                last_stage = "verify_r2"
                remote_keys = set(r2_storage.list_keys(prefix))
                missing_remote = sorted(set(r2_assets.values()) - remote_keys)
                if missing_remote:
                    raise RuntimeError(f"R2 objects missing after upload: {', '.join(missing_remote[:5])}")
                db_id = verified_id

            record["db_id"] = db_id
            record["id"] = db_id
            record["persistence_attempts"] = attempt
            record["persistence_verified"] = bool(verify)
            _notify(status_callback, status="ok", point_id=source_id, attempt=attempt, stage="verified")
            return {"db_id": db_id, "source_id": source_id, "attempt": attempt}

        except Exception as exc:  # retry the full idempotent Point write
            last_exc = exc
            delay = backoff[min(attempt - 1, len(backoff) - 1)] if backoff else 0.0
            _notify(
                status_callback,
                status="retry" if attempt < max_attempts else "failed",
                point_id=source_id,
                attempt=attempt,
                max_attempts=max_attempts,
                stage=last_stage,
                error=f"{type(exc).__name__}: {exc}",
                retry_in=delay if attempt < max_attempts else 0,
            )
            if attempt < max_attempts and delay > 0:
                sleep_fn(delay)

    detail = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "Unknown persistence error"
    raise PersistenceError(last_stage, detail, max_attempts)


def reconcile_project(
    project_id: str,
    manifest: list[dict],
    db_module,
    r2_storage,
    *,
    expected_assets_by_id: dict[str, set[str]] | None = None,
    essential_assets: set[str] | None = None,
) -> dict:
    """Compare the manifest against the actual Supabase + R2 state.

    One project-wide R2 listing is used to avoid a HEAD request for every image.
    """
    expected_assets_by_id = expected_assets_by_id or {}
    essential_assets = essential_assets or {"sem", "eds_map", "c_map", "o_map"}
    rows = db_module.get_points_with_data(project_id)
    by_key = {}
    for row, analysis, assets in rows:
        key = (int(row["power"]), int(row["time_sec"]), int(row["wafer"]), int(row["point"]))
        by_key[key] = (row, analysis, assets or {})

    remote_keys = set(r2_storage.list_keys(f"projects/{project_id}/points/"))
    complete = []
    incomplete = []
    for meta in manifest:
        mid = meta.get("id") or manifest_id(meta)
        key = manifest_key(meta)
        found = by_key.get(key)
        issue = {
            "id": mid,
            "sequence_index": int(meta.get("sequence_index") or 0),
            "power": f"{key[0]}W",
            "time": f"{key[1]}s",
            "wafer": key[2],
            "point": key[3],
            "missing": [],
        }
        if not found:
            issue["missing"].append("point_row")
            incomplete.append(issue)
            continue
        row, analysis, assets = found
        if not analysis:
            issue["missing"].append("analysis_row")

        required = set(expected_assets_by_id.get(mid) or essential_assets)
        missing_asset_rows = sorted(a for a in required if a not in assets)
        if missing_asset_rows:
            issue["missing"].append("asset_rows:" + ",".join(missing_asset_rows))

        expected_paths = [assets[a] for a in required if a in assets]
        missing_r2 = sorted(path for path in expected_paths if path not in remote_keys)
        if missing_r2:
            issue["missing"].append("r2_objects:" + ",".join(missing_r2[:8]))

        if issue["missing"]:
            issue["db_id"] = str(row["id"])
            incomplete.append(issue)
        else:
            complete.append({"id": mid, "db_id": str(row["id"]), "sequence_index": issue["sequence_index"]})

    return {
        "expected": len(manifest),
        "complete_count": len(complete),
        "incomplete_count": len(incomplete),
        "complete": complete,
        "incomplete": incomplete,
        "complete_ids": [x["id"] for x in complete],
        "incomplete_ids": [x["id"] for x in incomplete],
    }
