"""CAMEL compatibility adapter, imported only by simulation consumers."""

from camel.models import OpenAICompatibleModel


class BoundedCompatibleModel(OpenAICompatibleModel):
    """Keep agent input-memory budget independent of output-token limits.

    CAMEL 0.2.78 otherwise uses ``max_tokens`` for both, or a 999,999,999
    fallback for arbitrary model names. Its token counter is an approximation
    for non-OpenAI models; the configured budget must leave output headroom.
    """

    def __init__(self, *, context_token_limit: int, **kwargs):
        self._context_token_limit = context_token_limit
        super().__init__(**kwargs)

    @property
    def token_limit(self) -> int:
        return self._context_token_limit
