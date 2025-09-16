from abc import ABC, ABCMeta, abstractmethod

from magscope.datatypes import MatrixBuffer
from magscope.processes import ManagerProcessBase, SingletonMeta

class ABCSingletonMeta(ABCMeta, SingletonMeta):
    pass

class HardwareManagerBase(ManagerProcessBase, ABC, metaclass=ABCSingletonMeta):
    def __init__(self):
        super().__init__()
        self.buffer_shape = (1000, 2)
        self._buffer: MatrixBuffer | None = None
        self._is_connected: bool = False

    def run(self):
        super().run()

        self._buffer = MatrixBuffer(
            create=False,
            locks=self._locks,
            name=self.name,
        )

        while self._running:
            self._check_pipe()
            self.fetch()

        self.disconnect()

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def fetch(self):
        """
        Checks if the hardware has new data.

        If the hardware has new data, then it stores the
        data and timestamp in the matrix buffer (self._buffer).

        The timestamp should be the seconds since the unix epoch:
        (January 1, 1970, 00:00:00 UTC) """