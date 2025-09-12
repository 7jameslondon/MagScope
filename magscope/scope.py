from ctypes import c_uint8
from multiprocessing import Event, freeze_support, Pipe, Lock, Value
import numpy as np
import os
from typing import TYPE_CHECKING
from warnings import warn
import yaml

from magscope import CameraManager, ManagerProcess, Message, VideoBuffer, MatrixBuffer, VideoProcessorManager
from magscope.beads import BeadManager
from magscope.gui import WindowManager

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType

class MagScope:
    def __init__(self):
        self._running: bool = False
        self._default_settings_path = os.path.join(os.path.dirname(__file__), 'default_settings.yaml')
        self._settings_path = 'settings.yaml'
        self._settings = self._get_default_settings()
        self.bead_manager = BeadManager()
        self.camera_manager = CameraManager()
        self.video_processor_manager = VideoProcessorManager()
        self.window_manager = WindowManager()
        self.pipes: dict[str, Connection] = {}
        self.locks: dict[str, LockType] = {}
        self.lock_names: list[str] = ['VideoBuffer', 'TracksBuffer']
        process_instances: list[ManagerProcess] = [
            self.bead_manager,
            self.camera_manager,
            self.video_processor_manager,
            self.window_manager]
        self.processes: dict[str, ManagerProcess] = self._setup_processes(process_instances)
        self._quitting: Event = Event()
        self.quitting_events: dict[str, EventType] = {}
        self.tracks_buffer: MatrixBuffer | None = None
        self.video_buffer: VideoBuffer | None = None

    def start(self):
        if self._running:
            warn('MagScope is already running')
        self._running = True

        # First, attempt to load the settings file
        self._load_settings()

        # Second, set up multiprocessing resources
        freeze_support()  # To prevent recursion in windows executable
        self._setup_shared_resources()

        # Third, start the managers
        for proc in self.processes.values():
            proc.start() # calls 'run()'

        print('MagScope main loop starting ...')
        while self._running:
            self._check_pipes()
        print('MagScope main loop ended.')

        # Forth, join the parelle processes
        for name, proc in self.processes.items():
            proc.join()
            print(name, 'ended.')

    def _check_pipes(self):
        for pipe in self.pipes.values():
            # Check if this pipe has a message
            if not pipe.poll():
                continue

            # Get the message
            message = pipe.recv()

            if type(message) is not Message:
                warn(f'Message is not a Message object: {message}')
                continue

            # Process the message
            if message.to == ManagerProcess.__name__: # the message is to all processes
                if message.func == 'quit':
                    print('MagScope quitting ...')
                    self._quitting.set()
                    self._running = False
                for name, pipe2 in self.pipes.items():
                    if self.processes[name].is_alive() and not self.quitting_events[name].is_set():
                        pipe2.send(message)
                        if message.func == 'quit':
                            while not self.quitting_events[name].is_set():
                                if pipe2.poll():
                                    pipe2.recv()
                if message.func == 'quit':
                    break
            elif message.to in self.pipes.keys(): # the message is to one process
                if self.processes[message.to].is_alive() and not self.quitting_events[message.to].is_set():
                    self.pipes[message.to].send(message)
            else:
                warn(f'Unknown pipe {message.to} with {message}')

    @staticmethod
    def _setup_processes(proc_list: list[ManagerProcess]):
        proc_dict = {}
        for proc in proc_list:
            proc_dict[proc.name] = proc
        return proc_dict

    def _setup_shared_resources(self):
        # Create and share locks, pipes, flags, ect
        video_process_flag = Value(c_uint8, 0)
        for proc in self.processes.values():
            proc._camera_type = type(self.camera_manager.camera)
            proc._video_process_flag = video_process_flag
        self._setup_quitting_events()
        self._setup_pipes()
        self._setup_locks()

        # Create the shared buffers
        self.video_buffer = VideoBuffer(
            create=True,
            locks=self.locks,
            n_stacks=self._settings['video buffer n stacks'],
            n_images=self._settings['video buffer n images'],
            width=self.camera_manager.camera.width,
            height=self.camera_manager.camera.height,
            bits=np.iinfo(self.camera_manager.camera.dtype).bits)
        self.tracks_buffer = MatrixBuffer(
            create=True,
            locks=self.locks,
            name='TracksBuffer',
            shape=(self._settings['tracks max datapoints'], 7))


    def _setup_quitting_events(self):
        for name, proc in self.processes.items():
            proc._magscope_quitting = self._quitting
            self.quitting_events[name] = proc._quitting

    def _setup_locks(self):
        for name in self.lock_names:
            self.locks[name] = Lock()
        for proc in self.processes.values():
            proc._locks = self.locks

    def _setup_pipes(self):
        for name, proc in self.processes.items():
            pipe = Pipe()
            self.pipes[name] = pipe[0]
            proc._pipe = pipe[1]

    def _get_default_settings(self):
        with open(self._default_settings_path, 'r') as f:
            settings = yaml.safe_load(f)
        return settings

    def _load_settings(self):
        if not self._settings_path.endswith('.yaml'):
            warn("Settings path must be a .yaml file")
        elif not os.path.exists(self._settings_path):
            warn(f"Settings file {self._settings_path} did not exist. Creating it now.")
            with open(self._settings_path, 'w') as f:
                yaml.dump(self._settings, f)
        else:
            try:
                with open(self._settings_path, 'r') as f:
                    settings = yaml.safe_load(f)
                self._settings.update(settings)
            except yaml.YAMLError as e:
                warn(f"Error loading settings file {self._settings_path}: {e}")

        for proc in self.processes.values():
            proc.set_settings(self._settings)

    @property
    def settings_path(self):
        return self._settings_path

    @settings_path.setter
    def settings_path(self, value):
        if self._running:
            warn('MagScope is already running')
        self._settings_path = value

    @property
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = value
        if self._running:
            for pipe in self.pipes.values():
                pipe.send(Message(ManagerProcess, ManagerProcess.set_settings, value))
