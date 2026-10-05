"""FastAPI 入口：HTTP API + 操作页面静态托管。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    BlockingConstraint,
    ScheduleItem,
    ScheduleRequest,
    ScheduleResponse,
)
from .scheduler import solve
from .times import to_local_display
from .validator import validate_and_normalize

app = FastAPI(
    title="跨时区发布窗口编排",
    description="在时区/DST、依赖先后、禁发、冻结与资源约束下求可执行发布排程",
    version="1.0.0",
)

WEB_DIR = Path(__file__).parent / "web"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/schedule", response_model=ScheduleResponse)
def schedule(req: ScheduleRequest) -> ScheduleResponse:
    if not req.services:
        return ScheduleResponse(
            feasible=False, status="infeasible",
            blockers=[BlockingConstraint(code="empty_services",
                                        message="至少需要一个服务")],
        )

    norm, errors = validate_and_normalize(req)
    if errors:
        # 422：输入本身有问题（DST 重复/不存在、坏时区、依赖环、区间非法等）。
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422, content={"errors": errors})

    result = solve(norm)
    assert norm is not None

    if not result.feasible:
        return ScheduleResponse(
            feasible=False,
            status=result.status,
            blockers=[BlockingConstraint(**b) for b in (result.blockers or [])],
        )

    items: list[ScheduleItem] = []
    for sid, st in sorted(result.starts.items(), key=lambda kv: kv[1]):
        s = norm.services[sid]
        end = st + s.duration
        occ = st + s.duration + s.rollback
        used = next(
            (w for w in s.windows if w.start <= st and occ <= w.end),
            None,
        )
        window_text = (
            f"{_iso(used.start)} ~ {_iso(used.end)} UTC（{used.label}）"
            if used else "（窗口信息缺失）"
        )
        items.append(ScheduleItem(
            service_id=sid,
            name=s.name,
            deploy_start_utc=_iso(st),
            deploy_end_utc=_iso(end),
            occupied_until_utc=_iso(occ),
            local_time=to_local_display(datetime.fromtimestamp(st, tz=timezone.utc), s.timezone),
            timezone=s.timezone,
            window_used=window_text,
            resources=s.resources,
        ))

    return ScheduleResponse(feasible=True, status=result.status, schedule=items)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
