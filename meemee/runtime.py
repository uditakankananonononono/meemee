from contextlib import AsyncExitStack

from .agent import Agent
from .composition import run_composition
from .config import Settings
from .context import ContextStore
from .model_profiles import build_role_model
from .persistence import persistence_from_settings
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
    ListFiles,
    ReadFile,
    ShellCommand,
    ToolRegistry,
    WriteFile,
)


async def build_agent_async(settings: Settings | None = None, include_delegation: bool = True, memory=None, browser_sessions=None, persistence=None) -> Agent:
    settings = settings or Settings()
    owned_persistence = None
    registry = ToolRegistry()
    async with AsyncExitStack() as cleanup:
        def register(tool):
            close = getattr(tool, "aclose", None)
            if close is not None:
                cleanup.push_async_callback(close)
            registry.register(tool)
        register(GitHubRepoSearch(settings.github_token))
        register(GitHubPushBranch(settings.github_token))
        register(GitHubCreatePullRequest(settings.github_token))
        register(ReadFile(settings.workspace))
        register(ListFiles(settings.workspace))
        register(WriteFile(settings.workspace))
        register(ShellCommand(settings.workspace, set(settings.shell_allowlist.split(","))))
        register(GitInspect(settings.workspace))
        register(GitCommit(settings.workspace))
        register(BrowserNavigate(settings.workspace, settings.browser_headless, settings.data_dir / "browser-profiles"))
        if browser_sessions is not None:
            from .tools.browser_session import session_tools
            for tool in session_tools(browser_sessions):
                registry.register(tool)
        if include_delegation:
            register(DelegateTasks(lambda: AgentTeam(lambda: build_agent_async(settings, False, browser_sessions=browser_sessions, persistence=persistence))))
        model = build_role_model(settings, "agent")
        cleanup.push_async_callback(model.aclose)
        if persistence is None and (memory is None or settings.persistence_backend.strip().lower() == "postgresql"):
            persistence = persistence_from_settings(settings)
            owned_persistence = persistence
            cleanup.callback(persistence.close)
        selected_memory = memory or persistence.memory
        # PostgreSQL mode: the shared personal model and context index, not per-host files.
        context = persistence.context if persistence is not None else ContextStore(settings.data_dir / "context.sqlite3")
        personal_model = persistence.personal_model if persistence is not None else PersonalModelStore(settings.data_dir / "personal-model.sqlite3")
        agent = Agent(model, registry, selected_memory, settings.max_steps, PolicyEngine.from_file(settings.policy_file), context, personal_model, owns_model=True, owns_tools=True, owned_persistence=owned_persistence)
        cleanup.pop_all()
        return agent


def build_agent_sync(settings: Settings | None = None, include_delegation: bool = True, memory=None, browser_sessions=None, persistence=None) -> Agent:
    return run_composition(lambda: build_agent_async(settings, include_delegation, memory, browser_sessions, persistence))


# Legacy composition retained for API import compatibility until awaited bootstrap.
def build_agent(settings: Settings | None = None, include_delegation: bool = True, memory=None, browser_sessions=None, persistence=None) -> Agent:
    settings = settings or Settings()
    registry = ToolRegistry()
    registry.register(GitHubRepoSearch(settings.github_token))
    registry.register(GitHubPushBranch(settings.github_token))
    registry.register(GitHubCreatePullRequest(settings.github_token))
    registry.register(ReadFile(settings.workspace))
    registry.register(ListFiles(settings.workspace))
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
        registry.register(DelegateTasks(lambda: AgentTeam(lambda: build_agent(settings, False, browser_sessions=browser_sessions, persistence=persistence))))
    model = build_role_model(settings, "agent")
    if persistence is None and (memory is None or settings.persistence_backend.strip().lower() == "postgresql"):
        persistence = persistence_from_settings(settings)
    selected_memory = memory or persistence.memory
    # PostgreSQL mode: the shared personal model and context index, not per-host files.
    context = persistence.context if persistence is not None else ContextStore(settings.data_dir / "context.sqlite3")
    personal_model = persistence.personal_model if persistence is not None else PersonalModelStore(settings.data_dir / "personal-model.sqlite3")
    return Agent(model, registry, selected_memory, settings.max_steps, PolicyEngine.from_file(settings.policy_file), context, personal_model, owns_model=True, owns_tools=True)
