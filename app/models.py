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
