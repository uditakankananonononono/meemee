from .agent import Agent
from .config import Settings
from .context import ContextStore
from .llm import OpenAICompatibleModel
from .persistence import build_persistence
from .personal_model import PersonalModelStore
from .policy import PolicyEngine
from .team import AgentTeam
from .tools import (
    BrowserNavigate,
    DelegateTasks,
    GitCommit,
    GitHubCreatePullRequest,
    GitHubPushBranch,
    GitHubRepoSearch,
    GitInspect,
    ReadFile,
    ShellCommand,
    ToolRegistry,
    WriteFile,
)


def build_agent(settings: Settings | None = None, include_delegation: bool = True, memory=None, browser_sessions=None) -> Agent:
    settings = settings or Settings()
    registry = ToolRegistry()
    registry.register(GitHubRepoSearch(settings.github_token))
    registry.register(GitHubPushBranch(settings.github_token))
    registry.register(GitHubCreatePullRequest(settings.github_token))
    registry.register(ReadFile(settings.workspace))
    registry.register(WriteFile(settings.workspace))
    registry.register(ShellCommand(settings.workspace, set(settings.shell_allowlist.split(","))))
    registry.register(GitInspect(settings.workspace))
    registry.register(GitCommit(settings.workspace))
    registry.register(BrowserNavigate(settings.workspace, settings.browser_headless, settings.data_dir / "browser-profiles"))
    if browser_sessions is not None:
        from .tools.browser_session import session_tools
        for tool in session_tools(browser_sessions):
            registry.register(tool)
    if include_delegation:
        registry.register(DelegateTasks(lambda: AgentTeam(lambda: build_agent(settings, False, browser_sessions=browser_sessions))))
    model = OpenAICompatibleModel(
        settings.model_base_url, settings.model_name, settings.model_api_key, settings.request_timeout, settings.model_max_attempts
    )
    selected_memory = memory or build_persistence(settings.persistence_backend, settings.data_dir, settings.postgres_dsn).memory
    context = ContextStore(settings.data_dir / "context.sqlite3")
    personal_model = PersonalModelStore(settings.data_dir / "personal-model.sqlite3")
    return Agent(model, registry, selected_memory, settings.max_steps, PolicyEngine.from_file(settings.policy_file), context, personal_model)
