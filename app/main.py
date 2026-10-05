"""FastAPI 入口：HTTP API + 操作页面静态托管。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .models import (
    BlockingConstraint,
    ChangedService,
    DiffServicePoint,
    ScheduleDiff,
    ScheduleDiffRequest,
    ScheduleDiffResponse,
    ScheduleItem,
    ScheduleRequest,
    ScheduleResponse,
    SideFailure,
)
from .scheduler import SolveResult, solve
from .times import to_local_display
from .validator import Normalized, validate_and_normalize

app = FastAPI(
    title="跨时区发布窗口编排",
    description="在时区/DST、依赖先后、禁发、冻结与资源约束下求可执行发布排程",
    version="1.0.0",
)

WEB_DIR = Path(__file__).parent / "web"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def _solve_side(req: ScheduleRequest) -> tuple[Normalized | None, SolveResult | None, list[dict]]:
    """校验并求解一份排程请求。

    返回 (norm, result, errors)：errors 非空表示输入校验失败（对应 422），
    此时其余两项为 None；否则 norm/result 有效。
    """
    if not req.services:
        empty = SolveResult(
            feasible=False, status="infeasible", starts=None,
            blockers=[{"code": "empty_services", "message": "至少需要一个服务"}],
        )
        return None, empty, []

    norm, errors = validate_and_normalize(req)
    if errors:
        # 输入本身有问题（DST 重复/不存在、坏时区、依赖环、区间非法等）。
        return None, None, errors

    assert norm is not None
    return norm, solve(norm), []


def _build_items(norm: Normalized, result: SolveResult) -> list[ScheduleItem]:
    assert result.starts is not None
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
    return items


@app.post("/api/schedule", response_model=ScheduleResponse)
def schedule(req: ScheduleRequest) -> ScheduleResponse:
    norm, result, errors = _solve_side(req)
    if errors:
        # 422：输入本身有问题（DST 重复/不存在、坏时区、依赖环、区间非法等）。
        return JSONResponse(status_code=422, content={"errors": errors})

    assert norm is not None and result is not None
    if not result.feasible:
        return ScheduleResponse(
            feasible=False,
            status=result.status,
            blockers=[BlockingConstraint(**b) for b in (result.blockers or [])],
        )

    return ScheduleResponse(
        feasible=True, status=result.status, schedule=_build_items(norm, result))


@app.post("/api/schedule/diff", response_model=ScheduleDiffResponse)
def schedule_diff(req: ScheduleDiffRequest) -> ScheduleDiffResponse:
    """变更影响预演：分别求解当前请求与新请求，按服务 ID 对齐输出差异。

    任一请求输入错误（422，逐侧给出完整错误）或无解（200，逐侧给出阻塞
    约束组合）时不输出差异结果；两侧均可行时输出 changed/added/removed，
    完全无变化时三者皆为空列表。
    """
    base_norm, base_res, base_err = _solve_side(req.base)
    cand_norm, cand_res, cand_err = _solve_side(req.candidate)

    if base_err or cand_err:
        return JSONResponse(status_code=422, content={"errors": {
            "base": base_err or None,
            "candidate": cand_err or None,
        }})

    assert base_res is not None and cand_res is not None
    base_fail = _side_failure(base_res)
    cand_fail = _side_failure(cand_res)
    if base_fail or cand_fail:
        if base_fail and cand_fail:
            status = "both_infeasible"
        elif base_fail:
            status = "base_infeasible"
        else:
            status = "candidate_infeasible"
        return ScheduleDiffResponse(
            status=status, diff=None, base=base_fail, candidate=cand_fail)

    assert base_norm is not None and cand_norm is not None
    return ScheduleDiffResponse(
        status="ok", diff=_diff(base_norm, base_res, cand_norm, cand_res))


def _side_failure(result: SolveResult) -> SideFailure | None:
    if result.feasible:
        return None
    return SideFailure(
        status=result.status,
        blockers=[BlockingConstraint(**b) for b in (result.blockers or [])],
    )


def _occupied_until(norm: Normalized, starts: dict[str, int], sid: str) -> int:
    s = norm.services[sid]
    return starts[sid] + s.duration + s.rollback


def _minutes(delta_seconds: int) -> int | float:
    m = delta_seconds / 60
    return int(m) if m == int(m) else round(m, 2)


def _point(norm: Normalized, starts: dict[str, int], sid: str) -> DiffServicePoint:
    return DiffServicePoint(
        service_id=sid,
        name=norm.services[sid].name,
        deploy_start_utc=_iso(starts[sid]),
        occupied_until_utc=_iso(_occupied_until(norm, starts, sid)),
    )


def _diff(base_norm: Normalized, base_res: SolveResult,
          cand_norm: Normalized, cand_res: SolveResult) -> ScheduleDiff:
    assert base_res.starts is not None and cand_res.starts is not None
    b_starts, c_starts = base_res.starts, cand_res.starts

    added = [_point(cand_norm, c_starts, sid)
             for sid in sorted(c_starts) if sid not in b_starts]
    removed = [_point(base_norm, b_starts, sid)
               for sid in sorted(b_starts) if sid not in c_starts]

    changed: list[ChangedService] = []
    for sid in sorted(b_starts):
        if sid not in c_starts:
            continue
        b_start, c_start = b_starts[sid], c_starts[sid]
        b_occ = _occupied_until(base_norm, b_starts, sid)
        c_occ = _occupied_until(cand_norm, c_starts, sid)
        if b_start == c_start and b_occ == c_occ:
            continue
        changed.append(ChangedService(
            service_id=sid,
            name=cand_norm.services[sid].name,
            deploy_start_before_utc=_iso(b_start),
            deploy_start_after_utc=_iso(c_start),
            deploy_start_delta_minutes=_minutes(c_start - b_start),
            occupied_until_before_utc=_iso(b_occ),
            occupied_until_after_utc=_iso(c_occ),
            occupied_until_delta_minutes=_minutes(c_occ - b_occ),
        ))
    # 输出稳定可复现：按变化后的开始时刻排序。
    changed.sort(key=lambda c: (c.deploy_start_after_utc, c.service_id))

    return ScheduleDiff(changed=changed, added=added, removed=removed)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
