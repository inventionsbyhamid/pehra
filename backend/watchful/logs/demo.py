"""A plausible day of the gate log and the staff register for the judges' demo (demo.py's seeding hook calls seed):
residents' cars out in the morning and back in the evening, cabs and deliveries in and out, a few plates the reader
could not read, one cab still inside past the overstay time, and made up staff with their first in and last out.

Nothing here comes from a real person: plates are rendered with OpenCV (the passage picture is the rendered plate),
names are made up, and each staff member's face templates are random unit vectors that match no face."""
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from sqlalchemy import select

from watchful.db import models as m
from watchful.logs import register
from watchful.logs.readers import FACES, PLATES
from watchful.store.blobs import society_key

COLOURS = {"white": ((250, 250, 250), (20, 20, 20)), "yellow": ((30, 205, 245), (20, 20, 20)),
           "green": ((60, 150, 30), (250, 250, 250))}
STATES = ("MH", "KA", "DL", "TN", "UP", "HR", "GJ", "TS")
STAFF = (("Sunita", "maid", 7.5, 12.0), ("Kamla", "cook", 8.0, 14.5), ("Ravi", "driver", 8.75, 19.0),
         ("Mohan", "guard", 6.0, 18.25), ("Geeta", "maid", 9.25, 17.5), ("Imran", "gardener", 7.0, 11.5))


def plate_text(rng: random.Random) -> tuple[str, str]:
    """A made up plate in the common layout, compact and as printed."""
    state, district = rng.choice(STATES), rng.randint(1, 50)
    series = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2))
    number = rng.randint(1000, 9999)
    return f"{state}{district:02d}{series}{number}", f"{state} {district:02d} {series} {number}"


def render_plate(printed: str, colour: str) -> bytes:
    """An Indian plate drawn with OpenCV: an embossed border, the blue IND strip, the number centred."""
    bg, fg = COLOURS.get(colour, COLOURS["white"])
    w, h = 400, 96
    img = np.full((h, w, 3), bg, np.uint8)
    cv2.rectangle(img, (4, 4), (w - 5, h - 5), fg, 2, cv2.LINE_AA)
    cv2.rectangle(img, (8, 8), (36, h - 9), (150, 70, 20), -1)
    cv2.putText(img, "IND", (10, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (250, 250, 250), 1, cv2.LINE_AA)
    (tw, th), _ = cv2.getTextSize(printed, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
    scale = min((w - 56) / tw, (h - 16) * 0.62 / th)
    (tw, th), _ = cv2.getTextSize(printed, cv2.FONT_HERSHEY_SIMPLEX, scale, 3)
    cv2.putText(img, printed, (40 + (w - 48 - tw) // 2, (h + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, scale, fg, 3,
                cv2.LINE_AA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def _day_plan(rng: random.Random, now: datetime, tz: ZoneInfo) -> list[dict]:
    """Passages yesterday and today, oldest first: residents out in the morning and back in the evening (the same
    plates both days), cabs and deliveries in and out within the hour, a green plate, and a cab still inside."""
    today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []

    def add(direction, at, colour, cls, plate):
        out.append({"direction": direction, "at": at.astimezone(ZoneInfo("UTC")), "colour": colour, "cls": cls,
                    "plate": plate[0], "printed": plate[1]})

    residents = [(plate_text(rng), rng.choice(["car"] * 5 + ["motorcycle"])) for _ in range(7)]
    for midnight in (today - timedelta(days=1), today):
        h = lambda x, base=midnight: base + timedelta(hours=x)  # noqa: E731
        for plate, cls in residents:
            add("out", h(rng.uniform(7.5, 10.0)), "white", cls, plate)
            add("in", h(rng.uniform(17.5, 21.5)), "white", cls, plate)
        for _ in range(7):
            plate, cls = plate_text(rng), rng.choice(["car", "car", "car", "truck", "motorcycle"])
            start = rng.uniform(6.0, 22.0)
            add("in", h(start), "yellow", cls, plate)
            add("out", h(start + rng.uniform(0.1, 0.7)), "yellow", cls, plate)
        green = plate_text(rng)
        add("in", h(11.2), "green", "car", green)
        add("out", h(13.4), "green", "car", green)
    add("in", now - timedelta(hours=2.6), "yellow", "car", plate_text(rng))  # still inside, past the overstay time
    return sorted((x for x in out if x["at"] <= now), key=lambda x: x["at"])


def seed(s, workspace_id: str, cameras: dict[str, str], now: datetime, blobs=None, rows: list | None = None,
         staff_rows: list | None = None, seed_value: int = 7) -> dict[str, int]:
    """Fills the society's gate log and staff register for yesterday and today, in the caller's transaction `s`.
    `cameras` maps demo.json keys to camera ids: passages go on main_gate (else service_gate), attendance on
    service_gate (else main_gate); the gate camera gets plate reading and the staff camera the staff register. Only
    what is in the past of `now` is written, and outs pair with ins as the gate log pairs them. `rows` may give the
    passages instead ({"direction", "at", "colour", "cls", "plate"}). Returns how many rows of each were made."""
    from watchful.logs.ledger import _pair, add_overstay_watch
    from watchful.tuning import LedgerTuning, Tuning
    rng = random.Random(seed_value)
    society = s.get(m.Workspace, workspace_id)
    tz = ZoneInfo(society.timezone if society and society.timezone else "Asia/Kolkata")
    gate = cameras.get("main_gate") or cameras.get("service_gate")
    staff_cam = cameras.get("service_gate") or cameras.get("main_gate")
    for cid, reader in ((gate, PLATES), (staff_cam, FACES)):
        row = s.get(m.Camera, cid) if cid else None
        if row is not None and row.workspace_id == workspace_id:
            st = dict(row.settings_json or {})
            st["readers"] = sorted(set(st.get("readers") or []) | {reader})
            row.settings_json = st
    made = {"passages": 0, "staff": 0, "attendance": 0}
    if gate:
        add_overstay_watch(s, workspace_id, Tuning())  # as turning plate reading on gives it
        pictures: dict[str, str] = {}
        for n, v in enumerate(rows or _day_plan(rng, now, tz)):
            # now and then a resident's plate the reader could not read; never a cab's, so the cab still inside is
            # not taken for one that went out unread
            text = None if n % 9 == 4 and v["colour"] == "white" else v["plate"]
            key = pictures.get(v["plate"])
            if blobs is not None and key is None:
                key = pictures[v["plate"]] = society_key(workspace_id, gate, "passages", f"demo_{len(pictures)}.jpg")
                blobs.put(key, render_plate(v.get("printed") or v["plate"], v["colour"]), "image/jpeg")
            p = m.VehiclePassage(workspace_id=workspace_id, camera_id=gate, at=v["at"], direction=v["direction"],
                                 cls=v["cls"], plate_text=text, plate_conf=round(rng.uniform(0.62, 0.95), 3),
                                 plate_colour=v["colour"], picture_key=key, line="gate line",
                                 reads_json=[text, text] if text else [None], alerted_json=[])
            s.add(p)
            s.flush()
            if p.direction == "out":
                _pair(s, workspace_id, p, LedgerTuning())
            made["passages"] += 1
    have = {r for r in s.scalars(select(m.Staff.name).where(m.Staff.workspace_id == workspace_id))}
    local_today = now.astimezone(tz).date()
    for name, role, came, left in staff_rows or STAFF:
        if name in have:
            continue
        staff = m.Staff(workspace_id=workspace_id, name=name, role=role, consent_at=now - timedelta(days=20),
                        consent_text=register.consent_text(name), active=True, created_at=now - timedelta(days=20))
        s.add(staff)
        s.flush()
        for _ in range(3):
            s.add(m.StaffFace(workspace_id=workspace_id, staff_id=staff.id, embedding=m.pack_vec(rng_vec(rng))))
        made["staff"] += 1
        for back in (1, 0):
            day = local_today - timedelta(days=back)
            midnight = datetime.combine(day, datetime.min.time(), tz)
            first = midnight + timedelta(hours=came + rng.uniform(-0.3, 0.3))
            last = min(midnight + timedelta(hours=left + rng.uniform(-0.4, 0.4)), now)
            if first > now:
                continue
            s.add(m.Attendance(workspace_id=workspace_id, staff_id=staff.id, day=day.isoformat(), first_seen=first,
                               last_seen=last if last > first else None,
                               camera_ids_json=[staff_cam] if staff_cam else []))
            made["attendance"] += 1
    return made


def rng_vec(rng: random.Random, n: int = 128) -> list[float]:
    """A random unit vector: the shape of a face template, matching no face."""
    v = np.array([rng.gauss(0, 1) for _ in range(n)], np.float32)
    return (v / np.linalg.norm(v)).tolist()
