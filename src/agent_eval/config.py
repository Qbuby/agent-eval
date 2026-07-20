from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = ".env"


class DatabaseSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    name: str = "agent_eval"

    model_config = SettingsConfigDict(env_prefix="DB_", env_file=_ENV_FILE, extra="ignore")

    @property
    def async_url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class LLMSettings(BaseSettings):
    provider: str = "openai"
    base_url: str = ""
    model: str = "gpt-4o"
    judge_model: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: int = 4096
    api_key: str = ""

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=_ENV_FILE, extra="ignore")


class EvalSettings(BaseSettings):
    batch_concurrency: int = 5
    failure_threshold: float = 0.7
    regression_threshold: float = 0.05

    model_config = SettingsConfigDict(env_prefix="EVAL_", env_file=_ENV_FILE, extra="ignore")


class LoopSettings(BaseSettings):
    target_score: float = 0.85
    max_iterations: int = 10
    min_improvement: float = 0.01
    stagnation_patience: int = 3
    regression_tolerance: float = 0.05
    enable_ab_test: bool = True
    ab_test_ratio: float = 0.3

    model_config = SettingsConfigDict(env_prefix="LOOP_", env_file=_ENV_FILE, extra="ignore")


class LangSmithSettings(BaseSettings):
    api_key: str = ""
    api_url: str = "https://api.smith.langchain.com"
    project_name: str = ""
    default_dataset: str = ""

    model_config = SettingsConfigDict(env_prefix="LANGSMITH_", env_file=_ENV_FILE, extra="ignore")


class LangfuseSettings(BaseSettings):
    host: str = ""
    public_key: str = ""
    secret_key: str = ""
    remote_write: bool = False  # default off; PR3a moves trace storage to LangSmith

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_", env_file=_ENV_FILE, extra="ignore")

    @property
    def configured(self) -> bool:
        return bool(self.host and self.public_key and self.secret_key)


class AuthSettings(BaseSettings):
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="AUTH_", env_file=_ENV_FILE, extra="ignore")


class SecuritySettings(BaseSettings):
    """Crypto material that is *not* tied to user-session JWTs.

    ``fernet_key`` encrypts at-rest secrets such as evaluator-provider API
    keys. Generate with ``python -c "from cryptography.fernet import Fernet;
    print(Fernet.generate_key().decode())"`` and store in ``.env``. Rotating
    this key invalidates all stored ciphertexts (re-enter API keys after
    rotation); see ``crypto.encrypt_secret`` for the migration helper.
    """
    fernet_key: str = ""

    model_config = SettingsConfigDict(env_prefix="SECURITY_", env_file=_ENV_FILE, extra="ignore")


class RoutingSettings(BaseSettings):
    enabled: bool = True
    max_retries: int = 3
    retry_delay_base: float = 2.0
    default_dataset: str = ""

    model_config = SettingsConfigDict(env_prefix="ROUTING_", env_file=_ENV_FILE, extra="ignore")


class GovernanceSettings(BaseSettings):
    dedup_strategy: str = "skip"
    require_expected_output: bool = False
    max_messages_per_example: int = 100
    max_examples_per_dataset: int = 10000
    retention_policy: str = "fifo"
    capacity_warning_threshold: float = 0.9

    model_config = SettingsConfigDict(env_prefix="GOV_", env_file=_ENV_FILE, extra="ignore")


class LoggingSettings(BaseSettings):
    level: str = "INFO"          # DEBUG | INFO | WARNING | ERROR
    format: str = "plain"        # plain | json
    debug: bool = False          # expose traceback to clients on 5xx
    request_body: bool = False   # log request body on 4xx/5xx (sensitive!)

    model_config = SettingsConfigDict(env_prefix="LOG_", env_file=_ENV_FILE, extra="ignore")


class FeishuSettings(BaseSettings):
    """飞书机器人集成。长连接（ws.Client）主动连飞书，无需公网回调。

    - ``enabled`` 关时不拉起长连接（默认关，凭证缺失也不影响 backend 启动）。
    - ``app_id`` / ``app_secret``：飞书自建应用凭证（开了长连接 + im 消息权限）。
    - ``judge_provider``：内置 agent 编排用哪个 evaluator_provider 的 name
      作 LLM（缺省用 kiro）。
    """
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    judge_provider: str = "kiro"
    # 编排 LLM 的模型串（覆盖 provider.default_model）。空则回退 provider 默认。
    # 只影响机器人，不动 kiro provider 的 default_model（评估器不受影响）。
    judge_model: str = "claude-opus-4-8"

    # ── 多维表格集成（Bitable）——用 user OAuth（user_access_token）访问 ──
    # bitable_enabled 关时不注册 Bitable 工具、不挂 OAuth 回调路由。
    bitable_enabled: bool = False
    # OAuth 回调地址：必须公网可达，且与飞书应用后台「安全设置 > 重定向 URL」
    # 完全一致。形如 https://<公网域名>/api/integrations/feishu/oauth/callback。
    oauth_redirect_uri: str = ""
    # 申请的用户授权 scope（空格分隔）。Bitable 读写至少需 bitable:app；
    # 必须含 offline_access 才会返回 refresh_token（否则 access 过期只能重新授权）。
    oauth_scopes: str = "bitable:app offline_access"

    # ── 评估完成通知：全局固定接收者 ──
    # 逗号分隔的飞书 open_id 列表。任一评估 run 完成后，除触发者 / 定时任务配置的
    # 收件人外，这里的每个 open_id 也会收到完成卡片（合并去重）。空则只通知触发者。
    notify_open_ids: str = ""

    model_config = SettingsConfigDict(env_prefix="FEISHU_", env_file=_ENV_FILE, extra="ignore")

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.app_id and self.app_secret)

    @property
    def bitable_configured(self) -> bool:
        """Bitable 集成是否可用：bot 已配 + 显式开 bitable + 配了 OAuth 回调。"""
        return bool(self.configured and self.bitable_enabled and self.oauth_redirect_uri)

    @property
    def notify_open_ids_list(self) -> list[str]:
        """全局固定通知接收者（逗号分隔 → 去空白的 open_id 列表）。"""
        return [s.strip() for s in self.notify_open_ids.split(",") if s.strip()]


class Settings(BaseSettings):
    db: DatabaseSettings = DatabaseSettings()
    llm: LLMSettings = LLMSettings()
    eval: EvalSettings = EvalSettings()
    loop: LoopSettings = LoopSettings()
    langsmith: LangSmithSettings = LangSmithSettings()
    langfuse: LangfuseSettings = LangfuseSettings()
    auth: AuthSettings = AuthSettings()
    security: SecuritySettings = SecuritySettings()
    routing: RoutingSettings = RoutingSettings()
    governance: GovernanceSettings = GovernanceSettings()
    logging: LoggingSettings = LoggingSettings()
    feishu: FeishuSettings = FeishuSettings()


settings = Settings()
