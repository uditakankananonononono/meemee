from .agent import Agent
from .config import Settings
from .llm import OpenAICompatibleModel
from .memory import MemoryStore
from .tools import GitHubRepoSearch, ReadFile, ToolRegistry, WriteFile


def build_agent(settings: Settings | None = None) -> Agent:
    settings = settings or Settings()
    registry = ToolRegistry()
    registry.register(GitHubRepoSearch(settings.github_token))
    registry.register(ReadFile(settings.workspace))
    registry.register(WriteFile(settings.workspace))
    model = OpenAICompatibleModel(
        settings.model_base_url, settings.model_name, settings.model_api_key, settings.request_timeout
    )
    return Agent(model, registry, MemoryStore(settings.database_path), settings.max_steps)
