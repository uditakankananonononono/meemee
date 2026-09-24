"""instinct_models - the shared model layer for Atlas, Meemee and Sugarcode.

Components: Inkling (local GGUF / self-hosted, or Hugging Face router), Ornith
(local GGUF through llama.cpp/Ollama's OpenAI-compatible API), Needle (on-device
tool-calling model, LoRA fine-tuned per product) and The AI Library
(read-only catalog connector). Free-first: nothing here calls a paid API.
Training pipelines are shared; datasets stay per product.
"""
from .config import ProductConfig, load_config
from .providers import (ChatResult, InklingHFRouter, InklingLocal, NeedleLocal, OrnithOpenAICompat, Provider,
                        ProviderError, ProviderUnavailable)
from .router import Router, Task

__all__ = ["ProductConfig", "load_config", "Provider", "ProviderError", "ProviderUnavailable", "ChatResult",
           "InklingLocal", "InklingHFRouter", "OrnithOpenAICompat", "NeedleLocal", "Router", "Task"]
__version__ = "0.1.0"
