"""instinct_models - the shared model layer for Atlas, Meemee and Sugarcode.

Components: Inkling (local GGUF / self-hosted, or Hugging Face router), Ornith
(local GGUF through llama.cpp/Ollama's OpenAI-compatible API), Needle (on-device
tool-calling model, LoRA fine-tuned per product) and The AI Library
(read-only catalog connector). Free-first: nothing here calls a paid API.
Training pipelines are shared; datasets stay per product.
"""
from .config import ProductConfig, load_config
from .lexical import LexicalLocal, LexicalToolModel
from .providers import (
                        ChatResult,
                        HermesLocal,
                        InklingHFRouter,
                        InklingLocal,
                        JevEval,
                        JevStatusError,
                        NeedleLocal,
                        OpenClawOwner,
                        OrnithOpenAICompat,
                        Provider,
                        ProviderError,
                        ProviderUnavailable,
                        validate_questions,
)
from .router import Router, Task

__all__ = [
                        "ChatResult",
                        "HermesLocal",
                        "InklingHFRouter",
                        "InklingLocal",
                        "JevEval",
                        "JevStatusError",
                        "LexicalLocal",
                        "LexicalToolModel",
                        "NeedleLocal",
                        "OpenClawOwner",
                        "OrnithOpenAICompat",
                        "ProductConfig",
                        "Provider",
                        "ProviderError",
                        "ProviderUnavailable",
                        "Router",
                        "Task",
                        "load_config",
                        "validate_questions",
]
__version__ = "0.1.0"
