"""The society's logs for supervisors and above: the gate log (vehicle passages) and the staff register (first in and
last out), one day at a time in the society's time zone, each also as CSV. Every CSV download is audited."""
import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import Response

from watchful.auth.deps import CurrentMember, require_role
from watchful.logs import ledger, register
from watchful.store.blobs import in_society

RISKY = ("=", "+", "-", "@", "\t", "\r")  # a spreadsheet would run a cell starting with these as a formula


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(RISKY) else text


def as_csv(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    for row in rows:
        w.writerow([cell(v) for v in row])
    return out.getvalue()


def _split(iso: str | None) -> tuple[str, str]:
    if not iso:
        return "", ""
    dt = datetime.fromisoformat(iso)
    return dt.date().isoformat(), dt.strftime("%H:%M:%S")


def _stayed(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}"


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    supervisor = require_role("supervisor")

    def which_day(repo, day: str | None) -> str:
        if not day:
            return datetime.now(ledger.society_zone(repo)).date().isoformat()
        try:
            return date.fromisoformat(day).isoformat()
        except ValueError:
            raise HTTPException(400, "Pick a day from the calendar.")

    def vehicles_for(member: CurrentMember, day: str | None, q: str | None, camera_id: str | None):
        ws = member.workspace.id
        repo = ctx.repo_for(ws)
        day = which_day(repo, day)
        rows = ledger.passages(repo, day, q, camera_id)
        for r in rows:
            key = r.pop("picture_key")
            r["picture_url"] = ctx.blobs.url(key) if key and in_society(key, ws) else None
        return day, rows

    @router.get("/api/logs/vehicles")
    def vehicles(day: str | None = None, q: str | None = None, camera_id: str | None = None,
                 member: CurrentMember = Depends(supervisor)):
        day, rows = vehicles_for(member, day, q, camera_id)
        counts = {"in": sum(r["direction"] == "in" for r in rows), "out": sum(r["direction"] == "out" for r in rows),
                  "unread": sum(r["plate"] is None for r in rows)}
        return {"day": day, "q": ledger.normalise_query(q) or None, "counts": counts, "passages": rows}

    @router.get("/api/logs/vehicles.csv")
    def vehicles_csv(day: str | None = None, q: str | None = None, camera_id: str | None = None,
                     member: CurrentMember = Depends(supervisor)):
        day, rows = vehicles_for(member, day, q, camera_id)
        body = as_csv(["Date", "Time", "Direction", "Vehicle", "Plate", "Plate colour", "Plate confidence", "Camera",
                       "Line", "Paired at", "Stayed (hours:minutes)"],
                      [[*_split(r["at"]), r["direction"], r["vehicle"], r["plate"] or "not read", r["plate_colour"],
                        r["plate_conf"], r["camera"], r["line"], " ".join(_split(r["paired_at"])).strip(),
                        _stayed(r["stayed_s"])] for r in rows])
        ctx.db.audit(member.workspace.id, member.user.id, "logs.export",
                     {"log": "vehicles", "day": day, "q": q or None, "rows": len(rows)})
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="gate_log_{day}.csv"',
                                 "Cache-Control": "no-store"})

    def staff_for(member: CurrentMember, day: str | None):
        repo = ctx.repo_for(member.workspace.id)
        day = which_day(repo, day)
        return day, register.attendance(repo, day)

    @router.get("/api/logs/staff")
    def staff(day: str | None = None, member: CurrentMember = Depends(supervisor)):
        day, rows = staff_for(member, day)
        return {"day": day, "present": sum(r["present"] for r in rows), "staff": rows}

    @router.get("/api/logs/staff.csv")
    def staff_csv(day: str | None = None, member: CurrentMember = Depends(supervisor)):
        day, rows = staff_for(member, day)
        body = as_csv(["Date", "Name", "Role", "First in", "Last out", "Cameras", "Still enrolled"],
                      [[day, r["name"], r["role"], _split(r["first_in"])[1], _split(r["last_out"])[1],
                        "; ".join(r["cameras"]), "yes" if r["active"] else "no"] for r in rows])
        ctx.db.audit(member.workspace.id, member.user.id, "logs.export",
                     {"log": "staff", "day": day, "rows": len(rows)})
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="staff_register_{day}.csv"',
                                 "Cache-Control": "no-store"})

    return router
