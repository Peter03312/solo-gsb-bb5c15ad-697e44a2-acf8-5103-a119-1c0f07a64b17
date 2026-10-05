"""API 输入/输出数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WindowIn(BaseModel):
    start: str = Field(..., description="ISO 8601 时间；不带偏移量时按时区字段解析")
    end: str
    timezone: str = Field("UTC", description="IANA 时区，如 Asia/Shanghai")


class ServiceIn(BaseModel):
    id: str
    name: str = ""
    timezone: str = Field("UTC", description="排程结果的本地展示时区")
    duration_minutes: int = Field(..., gt=0, description="部署占用时长")
    rollback_minutes: int = Field(0, ge=0, description="部署后必须可回滚的资源占用时长")
    resources: list[str] = Field(default_factory=list, description="占用的共享资源名")
    windows: list[WindowIn] = Field(..., min_length=1, description="候选发布时间窗")


class DependencyIn(BaseModel):
    upstream: str
    downstream: str
    gap_minutes: int = Field(0, ge=0, description="上游部署结束到下游部署开始的最小间隔")


class BlackoutIn(BaseModel):
    team: str = Field(..., description="团队名")
    start: str
    end: str
    timezone: str = "UTC"


class FreezeIn(BaseModel):
    label: str = Field(..., description="冻结名称，如“双十一封网”")
    start: str
    end: str
    timezone: str = "UTC"


class ServiceTeamIn(BaseModel):
    service: str
    team: str


class ScheduleRequest(BaseModel):
    services: list[ServiceIn]
    dependencies: list[DependencyIn] = Field(default_factory=list)
    service_teams: list[ServiceTeamIn] = Field(default_factory=list)
    team_blackouts: list[BlackoutIn] = Field(default_factory=list)
    freezes: list[FreezeIn] = Field(default_factory=list)
    max_solve_seconds: float = Field(10.0, gt=0, le=60)


class ScheduleItem(BaseModel):
    service_id: str
    name: str
    deploy_start_utc: str
    deploy_end_utc: str
    occupied_until_utc: str
    local_time: str
    timezone: str
    window_used: str
    resources: list[str]


class BlockingConstraint(BaseModel):
    code: str
    message: str


class ScheduleResponse(BaseModel):
    feasible: bool
    schedule: list[ScheduleItem] | None = None
    blockers: list[BlockingConstraint] | None = None
    status: str  # "optimal" | "feasible" | "infeasible" | "unknown"


# ---- 变更影响预演（/api/schedule/diff） ----


class ScheduleDiffRequest(BaseModel):
    base: ScheduleRequest = Field(..., description="当前排程请求")
    candidate: ScheduleRequest = Field(..., description="修改后的排程请求")


class ChangedService(BaseModel):
    service_id: str
    name: str
    deploy_start_before_utc: str = Field(..., description="变化前部署开始（UTC）")
    deploy_start_after_utc: str = Field(..., description="变化后部署开始（UTC）")
    deploy_start_delta_minutes: int | float = Field(..., description="部署开始变化分钟数（后-前）")
    occupied_until_before_utc: str = Field(..., description="变化前占用结束（UTC，含回滚）")
    occupied_until_after_utc: str = Field(..., description="变化后占用结束（UTC，含回滚）")
    occupied_until_delta_minutes: int | float = Field(..., description="占用结束变化分钟数（后-前）")


class DiffServicePoint(BaseModel):
    """仅出现在一侧请求中的服务及其在所属排程中的时刻。"""

    service_id: str
    name: str
    deploy_start_utc: str
    occupied_until_utc: str


class ScheduleDiff(BaseModel):
    changed: list[ChangedService] = Field(
        ..., description="两侧都存在、但开始时间或占用结束时间发生变化的服务")
    added: list[DiffServicePoint] = Field(..., description="仅在新请求中出现的服务")
    removed: list[DiffServicePoint] = Field(..., description="在新请求中被移除的服务")


class SideFailure(BaseModel):
    """某一侧请求求解失败（无解/超时未知）时的阻塞约束组合。"""

    status: str  # "infeasible" | "unknown"
    blockers: list[BlockingConstraint]


class ScheduleDiffResponse(BaseModel):
    status: str = Field(
        ..., description='ok | base_infeasible | candidate_infeasible | both_infeasible')
    diff: ScheduleDiff | None = Field(None, description="两侧均可行时给出；否则为 null")
    base: SideFailure | None = Field(None, description="当前请求无解时的阻塞组合")
    candidate: SideFailure | None = Field(None, description="新请求无解时的阻塞组合")
