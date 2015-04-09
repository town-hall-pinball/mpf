""" MPF plugin for a shot controller which converts series of switch events
to 'shots' in the game."""
# shot_controller.py
# Mission Pinball Framework
# Written by Brian Madden & Gabe Knuth
# Released under the MIT License. (See license info at the end of this file.)

# Documentation and more info at http://missionpinball.com/mpf
import logging

from mpf.system.timing import Timing
from mpf.system.tasks import DelayManager
from mpf.system.config import Config

# todo reset shots on ball start?


def preload_check(machine):

    #if 'Shots' in machine.config and machine.config['Shots']:
    #    return True
    #else:
    #    return False

    return True


class ShotController(object):

    def __init__(self, machine):
        """Base class for the shot controller.

        The shot controller sets up and keeps track of all the shots in the
        machine.

        TODO: There's a lot to do still with this, but the basics are working.
        The todo list includes:

        * Lots more shot options need to be added, including kill switches,
        time extensions, etc.
        * Need to implement the functionality to work around broken switches
        * Need to add the ability for a shot to "advertise" itself which would
        be light, display, or sound effects that are kicked off when a shot
        wants to let the player know it's available.
        * Time before a shot can be reactivated again
        * Enable /disable shots without having to delete and recreate them

        Args:
            machine : The main MachineController object

        """
        self.machine = machine
        self.machine.shots = self  # is there a better way to do this? todo
        self.log = logging.getLogger("Shots")
        self.log.debug("Loading the ShotController")
        self.shots = []

        if 'Shots' in self.machine.config:
            self.process_config(self.machine.config['Shots'])

        # Tell the mode controller that it should look for shot items in
        # modes.
        self.machine.modes.register_start_method(self.process_config, 'Shots')

    def process_config(self, config, mode=None, priority=0):
        # config is localized to "Shots"

        self.log.debug("Configuring  Shots")

        shot_list = list()

        for shot in config:

            if ('Type' in config[shot] and config[shot]['Type'] == 'Sequence'):
                shot_list.append(SequenceShot(self.machine, shot, config[shot],
                                              priority))
            else:
                shot_list.append(StandardShot(self.machine, shot, config[shot],
                                              priority))

        self.shots += shot_list

        return shot_list

    def remove_shots(self, shot_list):
        for shot in shot_list:
            shot._remove_blocker()
            self.shots.remove(shot)


class Shot(object):

    def __init__(self, machine, name, config, priority=0):
        """Parent class that all shots are based on.

        Args:
            machine: The MachineController object
            name: String name of this shot.
            config: Dictionary that holds the configuration for this shot.
        """
        self.log = logging.getLogger('Shot.' + name)
        self.log.debug("Creating shot")

        self.machine = machine
        self.name = name
        self.config = config
        self.priority = priority

        self.enabled = True
        self.advertising = False
        self.tags = list()
        self.ms_to_complete = 0
        self.block_event = None

    def _block_handler(self, **kwargs):
        self.log.debug("Blocking Shot")
        return False

    def shot_made(self):
        self.machine.events.post_boolean('shot_' + self.name)
        if self.machine.auditor.enabled:
            self.machine.auditor.audit('Shots', self.name)

    def enable(self):
        self.log.debug("Enabling")

        if 'block' in self.config and self.config['block']:
            self.block_event = self.machine.events.add_handler(
                'shot_' + self.name,
                self._block_handler,
                priority=self.priority-1)

        self.enabled = True

    def disable(self):
        self.log.debug("Disabling")

        if self.block_event:
            self.machine.events.remove_handler_by_key(self.block_event)
            self.block_event = None

        self.enabled = False


class StandardShot(Shot):

    def __init__(self, machine, name, config, priority):
        """StandardShot base class which maps a single switch to a shot.

        Subclass of `Shot`

        Args:
            machine: The MachineController object
            name: String name of this shot.
            config: Dictionary that holds the configuration for this shot.

        """
        super(StandardShot, self).__init__(machine, name, config, priority)

        self.enable()

    def enable(self):
        """Enables the shot."""
        super(StandardShot, self).enable()

        for switch in Config.string_to_list(self.config['Switch']):
            self.machine.switch_controller.add_switch_handler(
                switch, self._switch_handler, return_info=True)

    def disable(self):
        """Disables the shot."""
        super(StandardShot, self).disable()

        for switch in Config.string_to_list(self.config['Switch']):
            self.machine.switch_controller.remove_switch_handler(
                switch, self._switch_handler)

    def _switch_handler(self, switch_name, state, ms):
        self.shot_made()


class SequenceShot(Shot):

    def __init__(self, machine, name, config, priority):
        """SequenceShot is where you need certain switches to be hit in the
        right order, possibly within a time limit.

        Subclass of `Shot`

        Args:
            machine: The MachineController object
            name: String name of this shot.
            config: Dictionary that holds the configuration for this shot.

        """
        super(SequenceShot, self).__init__(machine, name, config, priority)

        self.delay = DelayManager()

        self.progress_index = 0
        """Tracks how far along through this sequence the current shot is."""

        # convert our switches config to a list
        if 'Switches' in self.config:
            self.config['Switches'] = \
                Config.string_to_list(self.config['Switches'])

        # convert our timout to ms
        if 'Time' in self.config:
            self.config['Time'] = Timing.string_to_ms(self.config['Time'])
        else:
            self.config['Time'] = 0

        self.active_delay = False

        self.enable()

    def enable(self):
        """Enables the shot. If it's not enabled, the switch handlers aren't
        active and the shot event will not be posted."""

        super(SequenceShot, self).enable()

        # create the switch handlers
        for switch in self.config['Switches']:
            self.machine.switch_controller.add_switch_handler(
                switch, self._switch_handler, return_info=True)
        self.progress_index = 0

    def disable(self):
        """Disables the shot. If it's disabled, the switch handlers aren't
        active and the shot event will not be posted."""

        super(SequenceShot, self).disable()

        for switch in self.config['Switches']:
            self.machine.switch_controller.remove_switch_handler(
                switch, self.switch_handler)
        self.progress_index = 0

    def _switch_handler(self, switch_name, state, ms):
        # does this current switch meet the next switch in the progress index?
        if switch_name == self.config['Switches'][self.progress_index]:

            # are we at the end?
            if self.progress_index == len(self.config['Switches']) - 1:
                self.confirm_shot()
            else:
                # does this shot specific a time limit?
                if self.config['Time']:
                    # do we need to set a delay?
                    if not self.active_delay:
                        self.delay.reset(name='shot_timer',
                                         ms=self.config['Time'],
                                         callback=self.reset)
                        self.active_delay = True

                # advance the progress index
                self.progress_index += 1

    def confirm_shot(self):
        """Called when the shot is complete to confirm and reset it."""
        # kill the delay
        self.delay.remove('shot_timer')
        # reset our shot
        self.reset()

        self.shot_made()

    def reset(self):
        """Resets the progress without disabling the shot."""
        self.log.debug("Resetting this shot")
        self.progress_index = 0
        self.active_delay = False


# The MIT License (MIT)

# Copyright (c) 2013-2015 Brian Madden and Gabe Knuth

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
