from .generic_rest import GenericRESTProvider

PROVIDERS = {"generic_rest": GenericRESTProvider}

def provider_class(name):
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"Unsupported GST taxpayer provider: {name}") from exc
