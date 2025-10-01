from abc import ABC, abstractmethod

from magscope.datatypes import MatrixBuffer
from magscope.processes import ManagerProcessBase, SingletonABCMeta


class HardwareManagerBase(ManagerProcessBase, ABC, metaclass=SingletonABCMeta):
    def __init__(self):
        super().__init__()
        self.buffer_shape = (1000, 2)
        self._buffer: MatrixBuffer | None = None
        self._is_connected: bool = False

    def setup(self):
        self._buffer = MatrixBuffer(
            create=False,
            locks=self.locks,
            name=self.name,
        )

    def do_main_loop(self):
        self.fetch()

    def quit(self):
        super().quit()
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