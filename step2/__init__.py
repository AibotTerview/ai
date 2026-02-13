def get_llm_service():
    from step2.services import get_llm_service as _get
    return _get()


__all__ = ["get_llm_service"]
