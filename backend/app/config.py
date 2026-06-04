from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://timesheet:timesheet@postgres:5432/timesheet",
        alias="DATABASE_URL",
    )

    tfs_base_url: str = Field(default="https://tfs.t2.ru/tfs/Main", alias="TFS_BASE_URL")
    tfs_project: str = Field(default="Tele2", alias="TFS_PROJECT")
    tfs_project_id: str | None = Field(
        default="c56fb5fe-9752-462a-82ae-0b9e10364510",
        alias="TFS_PROJECT_ID",
    )
    tfs_verify_tls: bool = Field(default=True, alias="TFS_VERIFY_TLS")
    tfs_timeout_seconds: float = Field(default=45, alias="TFS_TIMEOUT_SECONDS")
    tfs_api_version: str = Field(default="6.1", alias="TFS_API_VERSION")
    tfs_batch_size: int = Field(default=100, alias="TFS_BATCH_SIZE")
    tfs_request_delay_seconds: float = Field(default=0.15, alias="TFS_REQUEST_DELAY_SECONDS")
    tfs_sync_max_tasks: int = Field(default=25, alias="TFS_SYNC_MAX_TASKS")
    tfs_sync_max_parents: int = Field(default=18, alias="TFS_SYNC_MAX_PARENTS")
    tfs_sync_parallel: int = Field(default=5, alias="TFS_SYNC_PARALLEL")
    tfs_sync_ttl_seconds: int = Field(default=600, alias="TFS_SYNC_TTL_SECONDS")
    tfs_sync_update_lookback_days: int = Field(default=21, alias="TFS_SYNC_UPDATE_LOOKBACK_DAYS")

    oscar_api_base_url: str | None = Field(
        default="https://oscar.k8s-mn.ds.t2.ru",
        alias="OSCAR_API_BASE_URL",
    )
    oscar_sync_enabled: bool = Field(default=True, alias="OSCAR_SYNC_ENABLED")

    task_type_name: str = Field(default="Задача", alias="TFS_TASK_TYPE")
    requirement_type_name: str = Field(default="Требование", alias="TFS_REQUIREMENT_TYPE")
    change_request_type_name: str = Field(default="Запрос на изменение", alias="TFS_CHANGE_REQUEST_TYPE")
    error_type_name: str = Field(default="Ошибка", alias="TFS_ERROR_TYPE")
    cost_project_field: str = Field(
        default="project.control",
        alias="TFS_COST_PROJECT_FIELD",
    )
    cost_project_default: str = Field(
        default="B2B 2026",
        alias="TFS_COST_PROJECT_DEFAULT",
    )
    remaining_work_field: str = Field(
        default="Microsoft.VSTS.Scheduling.RemainingWork",
        alias="TFS_REMAINING_WORK_FIELD",
    )

    app_public_url: str = Field(default="http://localhost:30080", alias="APP_PUBLIC_URL")
    api_public_url: str = Field(default="http://localhost:30080", alias="API_PUBLIC_URL")
    cors_allow_origins: str = Field(default="", alias="CORS_ALLOW_ORIGINS")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")

    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        origins = [
            "http://localhost:30080",
            "http://127.0.0.1:30080",
            "https://tfs.t2.ru",
        ]
        for url in (self.app_public_url, self.api_public_url):
            value = url.rstrip("/")
            if value:
                origins.append(value)
        for item in self.cors_allow_origins.split(","):
            value = item.strip().rstrip("/")
            if value:
                origins.append(value)
        return list(dict.fromkeys(origins))


settings = Settings()
