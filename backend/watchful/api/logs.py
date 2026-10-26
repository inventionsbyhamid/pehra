"""The society's logs for supervisors and above: the gate log (vehicle passages) and the staff register (first in and
last out), one day at a time in the society's time zone, each also as CSV. Every CSV download is audited."""
import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from watchful.auth.deps import CurrentMember, require_role
from watchful.logs import ledger, register
from watchful.store.blobs import in_society

RISKY = ("=", "+", "-", "@", "\t", "\r")  # a spreadsheet would run a cell starting with these as a formula


def mount(app: FastAPI, ctx) -> None:
    app.include_router(make_router(ctx))


def cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(RISKY) else text


def as_csv(header: list[str] | None, rows: list[list]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    if header is not None:
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

    def page_of(member: CurrentMember, day: str | None, q: str | None, camera_id: str | None, limit: int,
                after: str | None):
        ws = member.workspace.id
        repo = ctx.repo_for(ws)
        day = which_day(repo, day)
        rows = ledger.passages(repo, day, q, camera_id, max(1, min(limit, ledger.PAGE)), after)
        for r in rows:
            key = r.pop("picture_key")
            r["picture_url"] = ctx.blobs.url(key) if key and in_society(key, ws) else None
        return repo, day, rows

    @router.get("/api/logs/vehicles")
    def vehicles(day: str | None = None, q: str | None = None, camera_id: str | None = None, after: str | None = None,
                 limit: int = ledger.PAGE, member: CurrentMember = Depends(supervisor)):
        """A page of the day's gate log; `next` (a passage id) as `after` gives the next page, None at the end."""
        repo, day, rows = page_of(member, day, q, camera_id, limit, after)
        full = len(rows) == max(1, min(limit, ledger.PAGE))
        return {"day": day, "q": ledger.normalise_query(q) or None, "counts": ledger.day_counts(repo, day, q, camera_id),
                "passages": rows, "next": rows[-1]["id"] if rows and full else None}

    def export(member: CurrentMember, log: str, day: str, rows: int, **more) -> None:
        ctx.db.audit(member.workspace.id, member.user.id, "logs.export",
                     {"log": log, "day": day, "rows": rows, **{k: v for k, v in more.items() if v}})

    def stream(header: list[str], rows) -> StreamingResponse:
        def lines():
            yield as_csv(header, [])
            for row in rows:
                yield as_csv(None, [row])
        return StreamingResponse(lines(), media_type="text/csv; charset=utf-8")

    @router.get("/api/logs/vehicles.csv")
    def vehicles_csv(day: str | None = None, q: str | None = None, camera_id: str | None = None,
                     member: CurrentMember = Depends(supervisor)):
        """Every passage of the day, streamed a page at a time."""
        repo = ctx.repo_for(member.workspace.id)
        day = which_day(repo, day)
        counts = ledger.day_counts(repo, day, q, camera_id)
        export(member, "vehicles", day, counts["in"] + counts["out"], q=q, camera_id=camera_id)
        rows = ([*_split(r["at"]), r["direction"], r["vehicle"], r["plate"] or "not read", r["plate_colour"],
                 r["plate_conf"], r["camera"], r["line"], " ".join(_split(r["paired_at"])).strip(),
                 _stayed(r["stayed_s"])] for r in ledger.all_passages(repo, day, q, camera_id))
        res = stream(["Date", "Time", "Direction", "Vehicle", "Plate", "Plate colour", "Plate confidence", "Camera",
                      "Line", "Paired at", "Stayed (hours:minutes)"], rows)
        res.headers.update({"Content-Disposition": f'attachment; filename="gate_log_{day}.csv"',
                            "Cache-Control": "no-store"})
        return res

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
        export(member, "staff", day, len(rows))
        res = stream(["Date", "Name", "Role", "First seen", "Last seen", "Came in", "Went out", "Cameras",
                      "Still enrolled"],
                     ([day, r["name"], r["role"], _split(r["first_seen"])[1], _split(r["last_seen"])[1],
                       _split(r["came_in"])[1], _split(r["went_out"])[1], "; ".join(r["cameras"]),
                       "yes" if r["active"] else "no"] for r in rows))
        res.headers.update({"Content-Disposition": f'attachment; filename="staff_register_{day}.csv"',
                            "Cache-Control": "no-store"})
        return res

    return router
