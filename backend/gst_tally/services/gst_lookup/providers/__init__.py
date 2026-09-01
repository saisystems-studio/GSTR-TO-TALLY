from .generic_rest import GenericRESTProvider
from .gstzen import GSTZenProvider
from .gstinapi import GSTINAPIProvider
from .jamku import JamkuProvider
from .vayana import VayanaProvider
from .sandbox import SandboxGSTProvider


PROVIDERS = {"generic_rest": GenericRESTProvider, "sandbox": SandboxGSTProvider, "gstzen": GSTZenProvider,
             "gstinapi": GSTINAPIProvider, "jamku": JamkuProvider, "vayana": VayanaProvider}


def provider_class(name):
    return PROVIDERS.get(str(name or "").strip().lower())
