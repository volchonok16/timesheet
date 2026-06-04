from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class TfsAuthIn(ApiModel):
    base_url: str | None = None
    project: str
    project_id: str | None = None
    domain: str | None = None
    pat: str | None = None
    username: str | None = None
    password: str | None = None
    cookie: str | None = None
    extra_headers: dict[str, str] | None = None


class AuthDefaultsOut(ApiModel):
    base_url: str
    project: str
    project_id: str | None = None
    app_url: str
    api_url: str
    version: str


class AuthLoginOut(ApiModel):
    session_id: str
    base_url: str
    project: str
    project_id: str | None = None


class AuthStatusOut(ApiModel):
    authenticated: bool
    tfs_display_name: str | None = None
    tfs_unique_name: str | None = None
    tracking_stream_enabled: bool = False


class WorkItemOut(ApiModel):
    id: int
    title: str
    work_item_type: str
    state: str
    area_path: str
    kind: Literal["change_request", "requirement", "error", "task", "other"]
    tfs_url: str
    completed_work: float = 0


class RoleOut(ApiModel):
    id: str
    label: str


class ActivityOut(ApiModel):
    id: str
    label: str
    role_id: str
    comment_template: str = ""


class TimeEntryIn(ApiModel):
    parent_work_item_id: int
    role: str
    activity: str
    entry_date: date
    hours: float = Field(ge=0, le=24)
    minutes: int = Field(default=0, ge=0, le=45)
    subtract: bool = False
    comment: str | None = None
    cost_project: str | None = None


class CostProjectOptionsOut(ApiModel):
    field_name: str
    options: list[str]
    default_value: str | None = None
    parent_value: str | None = None


class TimeEntryOut(ApiModel):
    id: int
    parent_work_item_id: int
    tracking_work_item_id: int | None
    role: str
    activity: str
    entry_date: date
    hours: float
    comment: str | None
    cost_project: str | None = None
    created_at: datetime


class DayTotalOut(ApiModel):
    date: date
    hours: float


class TrackingRowOut(ApiModel):
    tracking_work_item_id: int | None
    role: str
    activity: str
    title: str
    tfs_url: str = ""
    daily_hours: dict[str, float] = Field(default_factory=dict)
    total_hours: float = 0


class TimesheetGroupOut(ApiModel):
    parent: WorkItemOut
    children: list[WorkItemOut] = Field(default_factory=list)
    tracking_rows: list[TrackingRowOut] = Field(default_factory=list)
    total_hours: float = 0


class TimesheetOut(ApiModel):
    period_start: date
    period_end: date
    view: Literal["week", "month"]
    day_totals: list[DayTotalOut]
    total_hours: float
    closed_day_totals: list[DayTotalOut] = Field(default_factory=list)
    closed_total_hours: float = 0
    groups: list[TimesheetGroupOut]
    closed_groups: list[TimesheetGroupOut] = Field(default_factory=list)


class CalendarDayOut(ApiModel):
    date: date
    hours: float
    entries_count: int


class MonthCalendarOut(ApiModel):
    year: int
    month: int
    days: list[CalendarDayOut]
    total_hours: float


class CalendarOut(ApiModel):
    scope: Literal["month", "quarter", "year"] = "month"
    month: int | None = None
    year: int
    quarter: int | None = None
    days: list[CalendarDayOut] = Field(default_factory=list)
    months: list[MonthCalendarOut] = Field(default_factory=list)
    total_hours: float


class StatsSummaryOut(ApiModel):
    today_hours: float
    today_goal: float
    week_hours: float
    week_goal: float
    week_start: date
    week_end: date


class TimesheetSyncOut(ApiModel):
    imported: int
    skipped: int
    tasks_scanned: int
    purged: int = 0
    removed_dupes: int = 0
    deltas_in_period: int = 0
    deltas_total: int = 0
    tsapi_probe_rows: int | None = None
    period_start: date
    period_end: date
    cached: bool = False
    source: str | None = None
    message: str | None = None
    stream_ok: bool | None = None
    tsapi_errors: list[str] | None = None


class RecentEntryOut(ApiModel):
    id: int
    parent_work_item_id: int
    parent_title: str
    parent_kind: str
    role: str
    activity: str
    entry_date: date
    hours: float
    cost_project: str | None = None
    created_at: datetime
