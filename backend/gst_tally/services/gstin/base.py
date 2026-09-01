from abc import ABC, abstractmethod


class GSTINProvider(ABC):
    @abstractmethod
    def get_gstin_details(self, gstin):
        raise NotImplementedError
