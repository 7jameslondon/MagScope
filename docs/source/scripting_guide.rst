.. _scripting_guide:

Scripting
=========

This guide explains how to run and write scripts to automate tasks in MagScope.

Running scripts
---------------
MagScope comes with a lightweight scripting runtime that allows you to queue up GUI interactions and hardware commands for repeatable experiments.
A script is an instance of ``magscope.Script`` where each call records a step to be executed.

First, create a script following the details below.
An `example script is available <https://github.com/7jameslondon/MagScope/blob/master/example_script.py>`_.
Second, load the script by clicking "Load" and selecting the Python script.
Once loaded the Scripting panel should say "Loaded".
Third, click "Start".
You can pause your script while it is running.
Or once it is "Finished" you can run it again by clicking "Start".

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Scripting_Panel_v1.jpg
         :alt: Screenshot of the Scripting panel.

Writing your own script
""""""""""""""""""""""""

To start a script create a new Python file (example: ``a_script.py``).
Then import magscope and create script instance.
The instance can be called anything (it does not need to be called "my_script").
Example::
   import magscope
   from magscope.ipc_commands import StartSleepCommand

   my_script = magscope.Script()

To add a step to the script, instantiate one of the IPC command dataclasses and
pass it to the ``Script`` instance. For example we can call the
``StartSleepCommand`` to pause our script for 5 seconds like this::
   my_script(StartSleepCommand(5))