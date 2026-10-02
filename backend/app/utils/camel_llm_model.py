"""CAMEL compatibility adapter, imported only by simulation consumers."""

from camel.models import OpenAICompatibleModel

from .budget import bind_budget_client, budgeted_chat_completion, budgeted_async_chat_completion, current_budget


class BoundedCompatibleModel(OpenAICompatibleModel):
    """Keep agent input-memory budget independent of output-token limits.

    CAMEL 0.2.78 otherwise uses ``max_tokens`` for both, or a 999,999,999
    fallback for arbitrary model names. Its token counter is an approximation
    for non-OpenAI models; the configured budget must leave output headroom.
    """

    def __init__(self, *, context_token_limit: int, **kwargs):
        self._context_token_limit = context_token_limit
        super().__init__(**kwargs)
        bind_budget_client(self._client)
        bind_budget_client(self._async_client)

    @property
    def token_limit(self) -> int:
        return self._context_token_limit

    def _request_config(self, messages, tools, response_format=None):
        config = dict(self.model_config_dict)
        if tools is not None:
            config['tools'] = tools
        if response_format is not None:
            config['response_format'] = response_format
            config.pop('stream', None)
        return dict(config, messages=messages, model=self.model_type)

    def _request_chat_completion(self, messages, tools=None):
        return budgeted_chat_completion(self._client, **self._request_config(messages, tools))

    async def _arequest_chat_completion(self, messages, tools=None):
        return await budgeted_async_chat_completion(self._async_client, **self._request_config(messages, tools))

    def _request_parse(self, messages, response_format, tools=None):
        return budgeted_chat_completion(self._client, sdk_operation='parse', **self._request_config(messages, tools, response_format))

    async def _arequest_parse(self, messages, response_format, tools=None):
        return await budgeted_async_chat_completion(self._async_client, sdk_operation='parse', **self._request_config(messages, tools, response_format))

    def _request_stream_parse(self, *args, **kwargs):
        if current_budget() or getattr(self._client, '_mirofish_budget', None):
            raise ValueError('Streaming model requests are not supported for budgeted runs')
        return super()._request_stream_parse(*args, **kwargs)

    async def _arequest_stream_parse(self, *args, **kwargs):
        if current_budget() or getattr(self._async_client, '_mirofish_budget', None):
            raise ValueError('Streaming model requests are not supported for budgeted runs')
        return await super()._arequest_stream_parse(*args, **kwargs)
