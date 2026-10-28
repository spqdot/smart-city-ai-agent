class LLMConfigError(RuntimeError):
    pass


def create_llm(settings):
    """Build a chat model from settings. Keys come only from the environment."""
    provider = settings.provider

    if provider == "openai":
        if not settings.api_key and not settings.base_url:
            raise LLMConfigError(
                "OPENAI_API_KEY is not set. Add it to your environment or a "
                "local .env file (see .env.example), or set LLM_PROVIDER=ollama."
            )
        from langchain_openai import ChatOpenAI

        kwargs = {"model": settings.model, "temperature": 0}
        if settings.api_key:
            kwargs["api_key"] = settings.api_key
        else:
            kwargs["api_key"] = "not-needed"  # local OpenAI-compatible servers
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        return ChatOpenAI(**kwargs)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        kwargs = {"model": settings.model, "temperature": 0}
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        return ChatOllama(**kwargs)

    raise LLMConfigError(
        f"Unsupported LLM_PROVIDER '{provider}'. Use 'openai' or 'ollama'."
    )
