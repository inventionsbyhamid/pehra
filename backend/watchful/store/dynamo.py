import json
import time
from dataclasses import asdict
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from watchful.registry import register
from watchful.store.db import AlertRow, Camera, EventRow, WatchRow, _camera_from, _today, new_id
from watchful.vision.faces import Enrollment

# name: (key schema [(attr, type)], indexes [(index name, hash attr, range attr or None)])
TABLES = {
    "cameras": ([("id", "S")], []),
    "events": ([("camera_id", "S"), ("sk", "S")], [("by_id", "id", None), ("unembedded", "pending", "sk")]),
    "watches": ([("id", "S")], []),
    "alerts": ([("id", "S")], [("by_fp", "fingerprint", None), ("by_camera", "camera_id", "ts"),
                               ("by_time", "g", "ts")]),
    "enrollments": ([("person_id", "S")], []),
    "counters": ([("key", "S")], []),
    "labels": ([("camera_id", "S"), ("group_id", "S")], []),
}
ATTR_TYPES = {"ts": "N"}
INDEX_KINDS = {"scene", "track_end"}


def _sk(ts: float, event_id: str) -> str:
    return f"{ts:017.3f}#{event_id}"


@register("repo", "dynamodb")
class DynamoRepository:
    """The same repository on DynamoDB; tables are created on first use (on demand billing)."""

    def __init__(self, settings=None, prefix: str | None = None, region: str | None = None, name: str = "dynamodb",
                 **_):
        self.prefix = prefix or settings.ddb_table_prefix
        self.db = boto3.resource("dynamodb", region_name=region or settings.aws_region)
        self._ensure()
        self.t = {n: self.db.Table(f"{self.prefix}-{n}") for n in TABLES}
        self._seen_cams: set[str] = set()

    def _ensure(self) -> None:
        existing = set(self.db.meta.client.list_tables()["TableNames"])
        for name, (keys, indexes) in TABLES.items():
            full = f"{self.prefix}-{name}"
            if full in existing:
                continue
            attrs = {k: t for k, t in keys}
            for _, h, r in indexes:
                attrs.setdefault(h, ATTR_TYPES.get(h, "S"))
                if r:
                    attrs.setdefault(r, ATTR_TYPES.get(r, "S"))
            spec = dict(TableName=full, BillingMode="PAY_PER_REQUEST",
                        KeySchema=[{"AttributeName": k, "KeyType": "HASH" if i == 0 else "RANGE"}
                                   for i, (k, _) in enumerate(keys)],
                        AttributeDefinitions=[{"AttributeName": k, "AttributeType": t} for k, t in attrs.items()])
            if indexes:
                spec["GlobalSecondaryIndexes"] = [
                    {"IndexName": n, "Projection": {"ProjectionType": "ALL"},
                     "KeySchema": [{"AttributeName": h, "KeyType": "HASH"}] +
                                  ([{"AttributeName": r, "KeyType": "RANGE"}] if r else [])}
                    for n, h, r in indexes]
            self.db.create_table(**spec).wait_until_exists()

    @staticmethod
    def _all(call, **kw) -> list[dict]:
        items, start = [], None
        while True:
            page = call(**kw, **({"ExclusiveStartKey": start} if start else {}))
            items += page.get("Items", [])
            start = page.get("LastEvaluatedKey")
            if not start:
                return items

    # cameras and watches
    def add_camera(self, cam: Camera) -> Camera:
        cam.id = cam.id or new_id("cam")
        self.t["cameras"].put_item(Item={"id": cam.id, "seq": time.time_ns(), "data": json.dumps(asdict(cam))})
        return cam

    def get_camera(self, camera_id: str) -> Camera | None:
        item = self.t["cameras"].get_item(Key={"id": camera_id}).get("Item")
        return _camera_from(json.loads(item["data"])) if item else None

    def list_cameras(self) -> list[Camera]:
        items = sorted(self._all(self.t["cameras"].scan), key=lambda i: i["seq"])
        return [_camera_from(json.loads(i["data"])) for i in items]

    def update_camera(self, cam: Camera) -> None:
        self.t["cameras"].update_item(Key={"id": cam.id}, UpdateExpression="set #d = :d",
                                      ExpressionAttributeNames={"#d": "data"},
                                      ExpressionAttributeValues={":d": json.dumps(asdict(cam))})

    def delete_camera(self, camera_id: str) -> None:
        self.delete_camera_events(camera_id)
        for i in self._all(self.t["watches"].scan):
            if i.get("camera_id") == camera_id:
                self.t["watches"].delete_item(Key={"id": i["id"]})
        for i in self._all(self.t["alerts"].query, IndexName="by_camera",
                           KeyConditionExpression=Key("camera_id").eq(camera_id)):
            self.t["alerts"].delete_item(Key={"id": i["id"]})
        for i in self.t["labels"].query(KeyConditionExpression=Key("camera_id").eq(camera_id))["Items"]:
            self.t["labels"].delete_item(Key={"camera_id": camera_id, "group_id": i["group_id"]})
        self.t["cameras"].delete_item(Key={"id": camera_id})

    def delete_camera_events(self, camera_id: str) -> int:
        items = self._all(self.t["events"].query, KeyConditionExpression=Key("camera_id").eq(camera_id))
        with self.t["events"].batch_writer() as batch:
            for i in items:
                batch.delete_item(Key={"camera_id": camera_id, "sk": i["sk"]})
        return len(items)

    def add_watch(self, row: WatchRow) -> WatchRow:
        row.id = row.id or new_id("wat")
        self.t["watches"].put_item(Item={"id": row.id, "camera_id": row.camera_id, "seq": time.time_ns(),
                                         "data": json.dumps(asdict(row))})
        return row

    def list_watches(self, camera_id: str | None = None) -> list[WatchRow]:
        items = sorted(self._all(self.t["watches"].scan), key=lambda i: i["seq"])
        return [WatchRow(**json.loads(i["data"])) for i in items if camera_id is None or i["camera_id"] == camera_id]

    def update_watch(self, row: WatchRow) -> None:
        self.t["watches"].update_item(Key={"id": row.id}, UpdateExpression="set #d = :d",
                                      ExpressionAttributeNames={"#d": "data"},
                                      ExpressionAttributeValues={":d": json.dumps(asdict(row))})

    # events
    def add_events(self, rows: list[EventRow]) -> None:
        for cid in {r.camera_id for r in rows} - self._seen_cams:
            self.t["counters"].put_item(Item={"key": f"evcam#{cid}"})
            self._seen_cams.add(cid)
        with self.t["events"].batch_writer() as batch:
            for r in rows:
                r.id = r.id or new_id("evt")
                item = {"camera_id": r.camera_id, "sk": _sk(r.ts, r.id), "id": r.id, "ts": Decimal(str(r.ts)),
                        "data": json.dumps(asdict(r))}
                if r.embedding is None and r.kind in INDEX_KINDS and r.keyframe_key:
                    item["pending"] = "y"  # sparse index of work for the embedding indexer
                batch.put_item(Item=item)

    def _camera_events(self, camera_id: str, since, until, newest_first: bool, want: int, keep) -> list[EventRow]:
        lo = f"{since:017.3f}" if since is not None else "0"
        hi = f"{until:017.3f}#~" if until is not None else "~"
        out, start = [], None
        while len(out) < want:
            kw = dict(KeyConditionExpression=Key("camera_id").eq(camera_id) & Key("sk").between(lo, hi),
                      ScanIndexForward=not newest_first, Limit=max(want, 100))
            if start:
                kw["ExclusiveStartKey"] = start
            page = self.t["events"].query(**kw)
            out += [r for r in (EventRow(**json.loads(i["data"])) for i in page.get("Items", [])) if keep(r)]
            start = page.get("LastEvaluatedKey")
            if not start:
                break
        return out[:want]

    def query_events(self, camera_id=None, kinds=None, zone=None, cls=None, since=None, until=None,
                     limit=500) -> list[EventRow]:
        def keep(r):
            return (not kinds or r.kind in kinds) and (zone is None or r.zone == zone) and (cls is None or r.cls == cls)
        cams = [camera_id] if camera_id is not None else self._event_cameras()
        rows = []
        for cid in cams:
            rows += self._camera_events(cid, since, until, True, limit, keep)
        return sorted(sorted(rows, key=lambda r: -r.ts)[:limit], key=lambda r: r.ts)

    def _event_cameras(self) -> list[str]:
        """Every camera id that has events, kept in the small counters table."""
        items = self._all(self.t["counters"].scan)
        return [i["key"].split("#", 1)[1] for i in items if i["key"].startswith("evcam#")]

    def get_event(self, event_id: str) -> EventRow | None:
        items = self.t["events"].query(IndexName="by_id", KeyConditionExpression=Key("id").eq(event_id))["Items"]
        return EventRow(**json.loads(items[0]["data"])) if items else None

    def events_missing_embedding(self, kinds: list[str], limit: int = 64) -> list[EventRow]:
        page = self.t["events"].query(IndexName="unembedded", KeyConditionExpression=Key("pending").eq("y"),
                                      ScanIndexForward=False, Limit=limit * 2)
        rows = [EventRow(**json.loads(i["data"])) for i in page.get("Items", [])]
        return [r for r in rows if r.kind in kinds][:limit]

    def set_event_embedding(self, event_id: str, embedding: list[float]) -> None:
        items = self.t["events"].query(IndexName="by_id", KeyConditionExpression=Key("id").eq(event_id))["Items"]
        if items:
            i = items[0]
            data = json.loads(i["data"])
            data["embedding"] = [round(float(x), 5) for x in embedding]
            self.t["events"].update_item(Key={"camera_id": i["camera_id"], "sk": i["sk"]},
                                         UpdateExpression="set #d = :d remove pending",
                                         ExpressionAttributeNames={"#d": "data"},
                                         ExpressionAttributeValues={":d": json.dumps(data)})

    def delete_events_before(self, ts: float, keep_cameras: list[str] = ()) -> int:
        removed = 0
        for cid in [c for c in self._event_cameras() if c not in keep_cameras]:
            items = self._all(self.t["events"].query, KeyConditionExpression=Key("camera_id").eq(cid)
                              & Key("sk").lt(f"{ts:017.3f}"))
            with self.t["events"].batch_writer() as batch:
                for i in items:
                    batch.delete_item(Key={"camera_id": cid, "sk": i["sk"]})
            removed += len(items)
        return removed

    # alerts
    def add_alert(self, row: AlertRow) -> AlertRow:
        row.id = row.id or new_id("alt")
        self.t["alerts"].put_item(Item={"id": row.id, "camera_id": row.camera_id, "ts": Decimal(str(row.ts)),
                                        "g": "all", "fingerprint": row.fingerprint, "data": json.dumps(asdict(row))})
        return row

    def get_alert(self, alert_id: str) -> AlertRow | None:
        item = self.t["alerts"].get_item(Key={"id": alert_id}).get("Item")
        return AlertRow(**json.loads(item["data"])) if item else None

    def update_alert(self, row: AlertRow) -> None:
        self.t["alerts"].update_item(Key={"id": row.id}, UpdateExpression="set #d = :d",
                                     ExpressionAttributeNames={"#d": "data"},
                                     ExpressionAttributeValues={":d": json.dumps(asdict(row))})

    def list_alerts(self, camera_id=None, limit=100) -> list[AlertRow]:
        if camera_id is None:
            key, index = Key("g").eq("all"), "by_time"
        else:
            key, index = Key("camera_id").eq(camera_id), "by_camera"
        page = self.t["alerts"].query(IndexName=index, KeyConditionExpression=key, ScanIndexForward=False,
                                      Limit=limit)
        return [AlertRow(**json.loads(i["data"])) for i in page.get("Items", [])]

    def delete_alerts_before(self, ts: float, keep_cameras: list[str] = ()) -> int:
        removed = 0
        for cid in [c for c in self._event_cameras() if c not in keep_cameras]:
            for i in self._all(self.t["alerts"].query, IndexName="by_camera",
                               KeyConditionExpression=Key("camera_id").eq(cid) & Key("ts").lt(Decimal(str(ts)))):
                self.t["alerts"].delete_item(Key={"id": i["id"]})
                removed += 1
        return removed

    def alert_fingerprint_exists(self, fp: str, within_s: float) -> bool:
        items = self.t["alerts"].query(IndexName="by_fp", KeyConditionExpression=Key("fingerprint").eq(fp))["Items"]
        return any(float(i["ts"]) >= time.time() - within_s for i in items)

    # people and counters
    def add_enrollment(self, e: Enrollment) -> None:
        self.t["enrollments"].put_item(Item={"person_id": e.person_id, "seq": time.time_ns(),
                                             "data": json.dumps(asdict(e))})

    def list_enrollments(self) -> list[Enrollment]:
        items = sorted(self._all(self.t["enrollments"].scan), key=lambda i: i["seq"])
        return [Enrollment(**json.loads(i["data"])) for i in items]

    def delete_enrollment(self, person_id: str) -> None:
        self.t["enrollments"].delete_item(Key={"person_id": person_id})

    def record_ai_call(self) -> None:
        self.t["counters"].update_item(Key={"key": f"ai_calls#{_today()}"}, UpdateExpression="add n :one",
                                       ExpressionAttributeValues={":one": 1})

    def count_ai_calls_today(self) -> int:
        item = self.t["counters"].get_item(Key={"key": f"ai_calls#{_today()}"}).get("Item")
        return int(item["n"]) if item else 0

    def set_person_label(self, camera_id: str, group_id: str, label: str) -> None:
        self.t["labels"].put_item(Item={"camera_id": camera_id, "group_id": group_id, "label": label})

    def person_labels(self, camera_id: str) -> dict[str, str]:
        items = self.t["labels"].query(KeyConditionExpression=Key("camera_id").eq(camera_id))["Items"]
        return {i["group_id"]: i["label"] for i in items}
