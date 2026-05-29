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


settings = Settings()
