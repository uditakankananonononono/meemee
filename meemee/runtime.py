from .agent import Agent
from .config import Settings
from .llm import OpenAICompatibleModel
from .memory import MemoryStore
from .policy import PolicyEngine
from .team import AgentTeam
from .tools import (
    BrowserNavigate,
    DelegateTasks,
    GitCommit,
    GitHubRepoSearch,
    GitInspect,
    ReadFile,
    ShellCommand,
    ToolRegistry,
    WriteFile,
)


def build_agent(settings: Settings | None = None, include_delegation: bool = True) -> Agent:
    settings = settings or Settings()
    registry = ToolRegistry()
    registry.register(GitHubRepoSearch(settings.github_token))
    registry.register(ReadFile(settings.workspace))
    registry.register(WriteFile(settings.workspace))
    registry.register(ShellCommand(settings.workspace, set(settings.shell_allowlist.split(","))))
    registry.register(GitInspect(settings.workspace))
    registry.register(GitCommit(settings.workspace))
    registry.register(BrowserNavigate(settings.workspace, settings.browser_headless, settings.data_dir / "browser-profiles"))
    if include_delegation:
        registry.register(DelegateTasks(lambda: AgentTeam(lambda: build_agent(settings, False))))
    model = OpenAICompatibleModel(
        settings.model_base_url, settings.model_name, settings.model_api_key, settings.request_timeout, settings.model_max_attempts
    )
    return Agent(model, registry, MemoryStore(settings.database_path), settings.max_steps, PolicyEngine.from_file(settings.policy_file))
