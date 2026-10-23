import json
import time
from dataclasses import asdict
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

from watchful.registry import register
from watchful.store.db import AlertRow, Camera, EventRow, WatchRow, _camera_from, _today, new_id
from watchful.vision.faces import Enrollment

TABLES = {
    "cameras": ([("id", "S")], []),
    "events": ([("camera_id", "S"), ("sk", "S")], [("by_id", "id")]),
    "watches": ([("id", "S")], []),
    "alerts": ([("id", "S")], [("by_fp", "fingerprint")]),
    "enrollments": ([("person_id", "S")], []),
    "counters": ([("key", "S")], []),
    "labels": ([("camera_id", "S"), ("group_id", "S")], []),
}


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

    def _ensure(self) -> None:
        existing = set(self.db.meta.client.list_tables()["TableNames"])
        for name, (keys, indexes) in TABLES.items():
            full = f"{self.prefix}-{name}"
            if full in existing:
                continue
            attrs = {k: t for k, t in keys} | {attr: "S" for _, attr in indexes}
            spec = dict(TableName=full, BillingMode="PAY_PER_REQUEST",
                        KeySchema=[{"AttributeName": k, "KeyType": "HASH" if i == 0 else "RANGE"}
                                   for i, (k, _) in enumerate(keys)],
                        AttributeDefinitions=[{"AttributeName": k, "AttributeType": t} for k, t in attrs.items()])
            if indexes:
                spec["GlobalSecondaryIndexes"] = [{"IndexName": n, "Projection": {"ProjectionType": "ALL"},
                                                   "KeySchema": [{"AttributeName": a, "KeyType": "HASH"}]}
                                                  for n, a in indexes]
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
        with self.t["events"].batch_writer() as batch:
            for r in rows:
                r.id = r.id or new_id("evt")
                batch.put_item(Item={"camera_id": r.camera_id, "sk": _sk(r.ts, r.id), "id": r.id,
                                     "ts": Decimal(str(r.ts)), "data": json.dumps(asdict(r))})

    def query_events(self, camera_id=None, kinds=None, zone=None, cls=None, since=None, until=None,
                     limit=500) -> list[EventRow]:
        if camera_id is not None:
            lo = f"{since:017.3f}" if since is not None else "0"
            hi = f"{until:017.3f}#~" if until is not None else "~"
            items = self._all(self.t["events"].query,
                              KeyConditionExpression=Key("camera_id").eq(camera_id) & Key("sk").between(lo, hi))
        else:
            items = self._all(self.t["events"].scan)
        rows = [EventRow(**json.loads(i["data"])) for i in items]
        rows = [r for r in rows if (not kinds or r.kind in kinds) and (zone is None or r.zone == zone)
                and (cls is None or r.cls == cls) and (since is None or r.ts >= since)
                and (until is None or r.ts <= until)]
        return sorted(rows, key=lambda r: r.ts)[:limit]

    def get_event(self, event_id: str) -> EventRow | None:
        items = self.t["events"].query(IndexName="by_id", KeyConditionExpression=Key("id").eq(event_id))["Items"]
        return EventRow(**json.loads(items[0]["data"])) if items else None

    def delete_events_before(self, ts: float) -> int:
        old = [i for i in self._all(self.t["events"].scan) if float(i["ts"]) < ts]
        with self.t["events"].batch_writer() as batch:
            for i in old:
                batch.delete_item(Key={"camera_id": i["camera_id"], "sk": i["sk"]})
        return len(old)

    # alerts
    def add_alert(self, row: AlertRow) -> AlertRow:
        row.id = row.id or new_id("alt")
        self.t["alerts"].put_item(Item={"id": row.id, "camera_id": row.camera_id, "ts": Decimal(str(row.ts)),
                                        "fingerprint": row.fingerprint, "data": json.dumps(asdict(row))})
        return row

    def get_alert(self, alert_id: str) -> AlertRow | None:
        item = self.t["alerts"].get_item(Key={"id": alert_id}).get("Item")
        return AlertRow(**json.loads(item["data"])) if item else None

    def update_alert(self, row: AlertRow) -> None:
        self.t["alerts"].update_item(Key={"id": row.id}, UpdateExpression="set #d = :d",
                                     ExpressionAttributeNames={"#d": "data"},
                                     ExpressionAttributeValues={":d": json.dumps(asdict(row))})

    def list_alerts(self, camera_id=None, limit=100) -> list[AlertRow]:
        items = [i for i in self._all(self.t["alerts"].scan) if camera_id is None or i["camera_id"] == camera_id]
        items.sort(key=lambda i: float(i["ts"]), reverse=True)
        return [AlertRow(**json.loads(i["data"])) for i in items[:limit]]

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
