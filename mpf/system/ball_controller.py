"""Manages and tracks all the balls in a pinball machine."""
# ball_controller.py
# Mission Pinball Framework
# Written by Brian Madden & Gabe Knuth
# Released under the MIT License. (See license info at the end of this file.)

# Documentation and more info at http://missionpinball.com/framework


# todo new method to set live count. Like "I want live count of 3"

# todo new method to remove_live? And then if that gets to zero then we have a
# ball over?


import logging
from mpf.system.tasks import DelayManager
from mpf.system.timing import Timing


class BallController(object):
    """Base class for the Ball Controller which is used to keep track of all
    the balls in a pinball machine.

    Parameters
    ----------

    machine : :class:`MachineController`
        A reference to the instance of the MachineController object.

    """
    def __init__(self, machine):
        self.machine = machine
        self.log = logging.getLogger("BallController")
        self.log.debug("Loading the BallController")
        self.delay = DelayManager()

        # Reset variables
        self.num_balls_contained = 0
        """Balls contained in ball devices."""
        self.num_balls_uncontained = 0
        """Balls loose on the playfield."""
        self.num_balls_known = self.num_balls_contained
        """How many balls the machine knows about. Could vary from the number
        of balls installed based on how many are *actually* in the machine, or
        to compensate for balls that are lost or stuck.
        """
        # todo should we save this in the machine data?

        self.flag_ball_search_in_progress = False
        """True if there's currently a ball search in progress."""

        self.flag_no_ball_search = False
        """Ball search is enabled and disabled automatically based on whether
        any balls are uncontained. Set this flag_no_ball_search to True if for
        some reason you don't want the ball search to be enabled. BTW I can't
        think of why you'd ever want this. The automatic stuff works great, and
        you need to keep it enabled even after tilts and stuff. Maybe for some
        kind of maintainance mode or something?
        """

        self.game = None
        """Holds the game object when a game is in progress."""

        self.num_balls_missing = 0
        """Balls lost and/or not installed."""

        # register for events
        self.machine.events.add_handler('machine_init_complete',
                                        self.ball_initial_count)
        self.machine.events.add_handler('request_to_start_game',
                                        self.request_to_start_game)
        self.machine.events.add_handler('ball_started', self.ball_started)
        self.machine.events.add_handler('ball_ending', self.ball_ending, 1000)
        self.machine.events.add_handler('sw_ballLive', self.ball_live_hit)
        self.machine.events.add_handler('tilt', self.tilt)
        self.machine.events.add_handler('game_start', self.game_start)
        self.machine.events.add_handler('game_end', self.game_end)
        self.machine.events.add_handler('sw_validPlayfield',
                                        self.validate_playfield)
        self.machine.events.add_handler('ball_add_live_success',
                                        self.ball_add_live_success)

        # for the ball drain events, we scan through all devices and register
        # the ones that have the 'drain' tag.

        for device in self.machine.balldevices.items_tagged('drain'):
            event_string = 'balldevice_' + device.name + '_ball_enter'
            self.machine.events.add_handler(event_string, self.ball_drained)

        # 'game'-specific variables
        self.num_balls_in_play = 0
        self.flag_valid_playfield = False
        self.num_hits_for_valid_playfied = self.machine.config['Game']\
            ['num_hits_for_valid_playfied']
        self.num_hits_towards_valid_playfield = 0
        self.flag_empty_balldevices_in_progress = True
        self.timer_ball_save = 0
        self.num_balls_contained_in_pf = 0  # balls in playfield devices
        self.num_balls_to_add_live = 0  # Balls to launch into play
        self.num_balls_to_add_stealth = 0  # Balls to stealth launch into play
        self.num_balls_to_auto_eject = 0  # Balls to autolaunch into play
        self.num_balls_to_save = 0

        # Ball Save
        self.flag_ball_save_active = False
        self.flag_ball_save_multiple_saves = False
        self.flag_ball_save_paused = False

    def request_to_start_game(self):
        """Method registered for the *request_to_start_game* event.

        Checks to make sure that the balls are in all the right places and
        returns. If too many balls are missing (based on the config files 'Min
        Balls' setting), it will return False to reject the game start request.
        """
        if self.num_balls_known < self.machine.config['Machine']['Min Balls']:
            self.log.info("BallController denies game start. Not enough balls")
            return False
        # todo something about all balls home? op setting?

    def ball_initial_count(self):
        """Sets up our counts when the machine is first powered on or after
        a reset.
        """
        self.num_balls_contained = self.count_contained_balls(process=False)
        self.num_balls_known = self.num_balls_contained
        self.num_balls_missing = self.machine.config['Machine']\
            ['Balls Installed'] - self.num_balls_known
        self.num_balls_uncontained = self.num_balls_missing
        if self.num_balls_missing:
            # Something ain't right
            # todo do something about this.
            # Watch for positive or negative
            # Probably a config setting will specify what to do
            # todo add config setting for min balls required to play
            # If not, put a message on the display that the game is OOO
            self.ball_search_begin()

    def ball_contained_count_change(self, change=0):
        """Used when you want to change the count of balls that are contained
        (held) versus uncontained (loose).

        Pass a positive value (+) to increase the contained count and
        decrease the uncontained count (i.e. when a ball enters a device or is
        captured.)

        Pass a negavitve value (-) to decrease the contained count and
        increase the uncontained count (i.e. when a ball is released from a
        device and is now loose on the playfield)

        If this count changes results in at least one ball being uncontained,
        the ball controller will start the ball search timer. If this count
        change results in no balls being uncontained, it will stop the ball
        search timer. If a ball search is in progress, it will sttop it.

        Parameters
        ----------

        change : int
            The change in contained balls.

        """
        self.log.debug("Ball contained count change. New values:")
        self.num_balls_contained += change
        self.num_balls_uncontained -= change
        self.log.debug("num_balls_contained: %s", self.num_balls_contained)
        self.log.debug("num_balls_uncontained: %s",
                         self.num_balls_uncontained)

        if self.num_balls_uncontained > 0:
            # ball search should be enabled since we have live balls
            self.ball_search_schedule()
        elif self.num_balls_uncontained == 0:
            # ball search should be off since all balls are known
            self.ball_search_disable()
        elif self.num_balls_uncontained < 0:  # this is weird
            self.log.debug("Balls uncontained (live) went negative. Doing a "
                             "full recount")
            self.ball_update_all_counts()

        # todo check for ball search in progress and notify it?

    def ball_update_all_counts(self):
        """Does a full count of all balls, including counting all balls in
        all devices and adding in any uncontained balls.
        """
        self.log.debug("Entering ball_controller.ball_update_all_counts()")

        total_count = 0

        self.num_balls_contained = self.count_contained_balls()
        # todo add something about tempcontainer??
        self.log.debug("We counted %s ball(s) contained",
                         self.num_balls_contained)
        self.log.debug("We have %s ball(s) uncontained",
                         self.num_balls_uncontained)
        self.log.debug("Ball known count: %s",
                         self.num_balls_known)

        if self.num_balls_uncontained < 0:
            self.log.warning("Ball uncontained is less than zero. "
                                "Resetting to zero.")
            self.num_balls_uncontained = 0

        total_count = self.num_balls_contained + self.num_balls_uncontained
        self.log.debug("We counted %s ball(s) grand total", total_count)

        if total_count > self.num_balls_known:
            self.ball_found(total_count-self.num_balls_known)

        elif total_count < self.num_balls_known:
            self.log.debug("We just counted %s ball(s), but there "
                             "should be %s", total_count, self.num_balls_known)
            # dang, we lost one (or more?)
            if self.num_balls_uncontained == 0:
                # if we should have all the balls
                if not self.flag_ball_search_in_progress:
                    # and there's not a search in progress
                    self.log.debug("Since we think all the balls are "
                                     "contained, let's start a ball search")
                    self.ball_search_begin()

        elif total_count == self.num_balls_known:
            # cool, we know where all the balls are
            # cancel ball search
            if self.flag_ball_search_in_progress:
                self.ball_search_end()

        if total_count > self.machine.config['Machine']['Balls Installed']:
            self.log.warning("WARNING! Too many balls installed. We "
                                  "counted %s, but there should only be %s "
                                  "installed", total_count,
                                  self.machine.config['Machine']\
                                                  ['Balls Installed'])
            # todo Do something about this

    def count_contained_balls(self, process=True):
        """Loops through all the ball devices and gets a count of how many
        balls they have.

        Parameters
        ----------

        process : bool
            If True, then if there's a contained count change, this method
            will call :meth:`ball_contained_count_change`.

        """
        new_count = 0
        prev_count = self.num_balls_contained
        for device in self.machine.balldevices:
            new_count += device.count_balls(process_balls=False)
        self.num_balls_contained = new_count
        self.log.debug("Counting contained balls. Found: %s", new_count)

        if new_count != prev_count and process:
            self.ball_contained_count_change()

        return new_count

    def ball_found(self, num=1):
        """Used when a previously missing ball is found. Updates the balls
        known and balls missing variables.

        Parameters
        ----------

        num : int
            Specifies how many balls have been found. Default is 1.

        """
        self.log.debug("HEY!! We just found %s lost ball(s).", num)
        self.num_balls_known += num
        self.num_balls_missing -= num
        self.log.debug("New ball counts. Known: %s, Missing: %s",
                       self.num_balls_known, self.num_balls_missing)
        self.ball_update_all_counts()  # is this necessary? todo

    def are_all_balls_home(self):
        """Checks to see if all the balls are 'home,' which means they're all
        contained in ball devices that are have the 'home' tag.

        Typically this is the trough and the plunger lane, but you can make
        them whatever you want.)

        """

        self.log.debug("Checking to see if all the balls are home")
        count = 0
        for device in self.machine.balldevices.items_tagged('home'):
                count += device.num_balls_contained
        if count == self.machine.ball_controller.num_balls_known:
            self.log.debug("Yes, all balls are home")
            return True
        else:
            self.log.debug("No, all balls are not home")
            return False

    def send_all_balls_home(self):
        """Ejects all balls from all ball devices that do not have the 'home'
        tag.

        Typically this would be used after a game ends, or when the machine is
        reset or first starts up.

        """
        self.log.debug("Received request to send all balls home")
        if not self.are_all_balls_home():
            self.log.debug("Determined that all balls are not home")
            for device in self.machine.balldevices:
                if 'home' not in device.tags:
                    device.eject_all()
                    # todo Add delay to wait to look for the balls
                    # todo need some flag for ball devices that they should be
                    # empty. Like what if a ball falls into a ball device
                    # while this is in progress?

    def empty_playfield_devices(self):
        """Ejects all balls from all devices tagged with 'playfield'."""
        # todo should probably change this to accept a tag name as a parameter
        # so it can be used with anything.
        self.log.debug("Emptying Playfield Ball Devices")
        self.flag_empty_devices_in_progress = True

        for device in self.machine.devices.items_tagged('playfield'):
            device.eject_all()

    def game_start(self, game):
        """Used to inform the ball controller that a game is starting.

        Parameters
        ----------

        game : :meth:`mpf.game.game.Game` instance

        """
        # todo I like the idea of receiving the game object on start. In this
        # case that's so our game mode can keep track of the current ball.
        # But is that really necessary? Why not just have ball_controller keep
        # track of it? We'll see...
        self.log.debug("ball_controller game_start")
        self.game = game
        self.num_balls_in_play = 0
        # todo make sure all balls are home?
        # todo do we need to update ball counts here? meh?

    def game_end(self):
        """Tells the ball controller that the game is ending."""
        self.game = None

    def ball_started(self):
        """Tells the ball controller that a ball has started."""
        self.log.debug("Entering ball_started()")
        self.num_balls_in_play = 0
        self.flag_valid_playfield = False
        self.ball_add_live(num=1, stealth=False, auto=False)

    def ball_ending(self, queue):
        """Tells the ball controller that the game received a request to end
        a ball.

        Typically this happens if the last ball drains.

        This method is tied to a queue event which means we can cancel the
        ball ending request if we want to. In this case we'll check the count
        of the number of balls we still have to add live. If it's more than
        zero then we're canceling the ball ending request by returning False.
        """
        # fyi this is priority 1000 so it runs first since we might end up
        # killing it if we have a ball launch in progress
        self.log.debug("Entering ball_ending()")
        if self.num_balls_to_add_live:
            self.log.debug("We have at least one ball to add live. Canceling "
                           " the ball_ending event.")
            queue.kill()
            return False

    def ball_add_live(self, num=0, stealth=False, auto=False, device=None):
        # todo verify whether num=0 should be 1?
        """Adds one or more balls into play. This is how start a ball or how
        you add additional balls in a multiball scenario.

        Parameters
        ----------

        num : int
            Number of balls to be launched. If ball launches are still
            pending from a previous request, this number will be added to the
            previously requested number.

        stealth : bool
            Set to True if the balls being launched should NOT
            be added to the number of balls in play.  For instance, if
            a ball is being locked on the playfield, and a new ball is
            being launched to keep only 1 active ball in play,
            stealth=True should be used.

        auto : bool
            Whether the balls should be added automatically. False means the
            player has to hit the plunger switch or manually plunge. True means
            the plunger will automatically fire.

            Note if the ball device you're adding from is not configured with a
            player switch and you request to add live with auto=False, it will
            eject the ball(s) anyway since there's no way for the player to do
            it.

        device : ball device object
            Specifies which ball device you want to add the ball from.
            Default is whichever device is tagged with 'add_live.' (If you have
            multiple devices taggeed with add_live it will be random. If that's
            not what you want then pass the device you want to launch from.)

        """

        # set which ball device we're working with
        if not device:
            device = self.machine.plunger  # todo change this to add_live tag

        if stealth:
            device.num_balls_to_eject_stealth += num

        self.log.debug("Received request to add %s live ball(s) from "
                       "ball device: %s", num, device.name)
        self.log.debug("Stealth launch: %s, Auto launch: %s", stealth, auto)

        # if the ball device we're adding the live ball from has a player
        # controller eject switch and this add live request is NOT set to auto,
        # then we can just make sure we have a ball ready to eject and then
        # do nothing (since we're waiting for the player to eject it)
        if device.config['player_controlled_eject'] and not auto:
            if not device.ok_to_eject:
                device.stage_ball()
            return

        if auto:
            device.num_balls_to_auto_eject += num
            device.num_balls_to_eject += num
            device.eject(num)

    def ball_add_live_success(self):
        """A ball was just added live to the playfield.

        Note there's no option to specify how many balls were added live since
        fundamentally you can only add one ball at a time. If you add a bunch
        of balls really fast (like from opening a gate on a lock device, then
        just call this method multiple times.)

        """

        self.num_balls_in_play += 1
        self.log.debug("Live ball added. Balls in play now: %s",
                       self.num_balls_in_play)

    def ball_live_hit(self):
        """A ball just hit a playfield switch.

        This means we have a ball loose on the playfield. (It doesn't
        necessarily mean that ball is "live," as this could happen as a ball
        rolls towards the drain after a tilt.)

        This method is mainly used to continuously push out the start time of
        the ball search. If this method is called when a ball search is in
        progress, it will end the it. (Since this method means we found the
        stuck ball.)

        Note you shouldn't have to call this method manually. The switch
        controller will do it automatically each time a switch tagged with
        'ball_live' has been activated.

        """
        if self.num_balls_uncontained:
            self.ball_search_schedule()
        if self.flag_ball_search_in_progress:
            self.log.debug("Just got a live playfield hit during ball search, "
                           "so we're ending ball search.")
            self.ball_search_end()

        # if pending ball launch todo?
        # if valid playfield todo?
        # call live ball added (or ball launch success) todo?
        # todo add in game, and ball server request?

        if not self.num_balls_in_play and not self.flag_valid_playfield:
            # a game is going on but our playfield is not valid
            self.num_hits_towards_valid_playfield += 1
            if self.num_hits_towards_valid_playfield >= \
                    self.num_hits_for_valid_playfied:
                self.validate_playfield()

    def validate_playfield(self):
        """Validates the playfield, but only if it was not valid before."""
        if not self.num_balls_in_play and not self.flag_valid_playfield:
            self.flag_valid_playfield = True
            self.num_hits_towards_valid_playfield = 0
            self.machine.events.post('playfield_valid')
            # todo add start ball save (based on the event, not here)

    def ball_drained(self, new_balls=1):
        """We've confirmed that a ball has entered a ball device tagged with
        *drain*.

        When this method is called, it posts a relay event called *ball_drain*
        along with the number of balls that just entered the device. This gives
        other modules a chance to intervene before the ball controller posts
        the *ball_remove_live* event.

        This is typically used with ball save. If ball save is active and a
        ball drains, when we post the ball_drain event the ball save module
        will pick it up and change the balls parameter that's being passed
        around to zero. That will cause our callback
        :meth:`process_ball_drained` to not post the *ball_remove_live event*.

        """
        self.log.debug("%s ball(s) just entered a drain device", new_balls)
        self.log.debug("num_balls_in_play: %s", self.num_balls_in_play)

        self.machine.events.post('ball_drain', ev_type='relay',
                                 callback=self.process_ball_drained,
                                 balls=new_balls)

    def process_ball_drained(self, balls=0):
        """Callback from the ball_drained event. If it receives a
        parameter of *balls* which is greater than 1, it will post a
        *ball_remove_live* event for each ball.

        """
        if balls:
            self.log.debug("Processing %s newly-drained ball(s)", balls)

            for i in range(balls):
                self.machine.events.post('ball_remove_live')

    def tilt(self):
        """ Registers for the 'tilt' event so the ball controller can do what
        it needs to do.

        Mainly this just tracks how many balls were live before
        the tilt and waits for them all to drain so the game can proceed. It
        also makes sure any balls that enter ball devices are ejected as
        they're rolling towards the drain.

        """
        pass

    '''
     ____        _ _    _____                     _
    |  _ \      | | |  / ____|                   | |
    | |_) | __ _| | | | (___   ___  __ _ _ __ ___| |__
    |  _ < / _` | | |  \___ \ / _ \/ _` | '__/ __| '_ \
    | |_) | (_| | | |  ____) |  __/ (_| | | | (__| | | |
    |____/ \__,_|_|_| |_____/ \___|\__,_|_|  \___|_| |_|

    The following code interfaces with the ball search module (which actually
    performs the ball search). Here's the interface if you want to write your
    own:

    MPF will post events when it wants certain things to happen, like
    "ball_search_begin_1"
    "ball_search_begin_2"
    "ball_search_end"

    You can use the following methods from our machine controller. (These
    examples assume it's in your ball search module as self.machine.)

    self.machine.get_balldevice_status()

    Returns a dictionary of ball devices, with the device object as the key
    and the number of balls it contains at the value.

    If you want to access a balldevice, they're accessible via:
    self.machine.balldevices[<device>].x

    Valid methods incude eject()
    With force=True to force it to fire the eject coil even if it thinks
    there's no ball.

    # todo that should trigger an eject in progress which the game can use to
    figure out where the ball came from.

    This ball search module is not reponsible for "finding" a ball. It just
    manages all the actions that take place during a search. The MPF Ball
    Controller will "find" any balls that are knocked loose and will then
    cancel the search.

    If you create your own, you should receive the instance of the machine_
    controller as an init paramter.

    You can fire coils via self.machine.coils[<coilname>].pulse()
    '''

    # todo need to think about soft delay switches (flippers)

    def ball_search_schedule(self, secs=None, force=False):
        """Schedules a ball search to start. By default it will schedule it
        based on the time configured in the machine configuration files.

        If a ball search is already scheduled, this method will reset that
        schedule to the new time passed.

        Parameters
        ----------

        secs : into
            Schedules the ball search that many secs from now.

        force : bool
            Set True to force a ball search. Otherwise it will only schedule it
            if self.flag_no_ball_search is False

        """
        if not self.flag_no_ball_search or force is True:
            if secs is not None:
                ball_search_start_ticks = Timing.secs(secs)
            else:
                ball_search_start_ticks = Timing.secs(
                    self.machine.config['BallSearch']
                    ['Secs until ball search start'])
            self.log.debug("Scheduling a ball search for %s ticks from now",
                           ball_search_start_ticks)
            self.delay.reset("ball_search_start",
                             delay=ball_search_start_ticks,
                             callback=self.ball_search_begin)

    def ball_search_disable(self):
        """Disables ball search.

        Note this is used to prevent a future ball
        search from happening (like when all balls become contained.) This
        method is not used to cancel an existing ball search. (Use
        ball_search_end for that.)

        """
        self.log.debug("Disabling Ball Search")
        self.delay.remove('ball_search_start')

    def ball_search_begin(self, force=False):
        """Begin the ball search process"""
        if not self.flag_no_ball_search:
            self.log.debug("Received request to start ball search")
            # ball search should only start if we have uncontained balls
            if self.num_balls_uncontained or force:
                # todo add audit
                self.flag_ball_search_in_progress = True
                self.machine.events.post("ball_search_begin_phase1")
                # todo set delay to start phase 2

            else:
                self.log.debug("We got request to start ball search, but we "
                               "have no balls uncontained. WTF??")

    def get_balldevice_status(self, includetrough=False):
        """ Returns a dictionary of ball devices, along with the number of
        balls the machine *thinks* are in each device.

        Format is a dictionary, with the device object as the key, and the
        number of balls contained as the value.

        # todo need to think about priorities. Assume it should start with ones
        it thinks are empty? And then maybe do them in the order the game
        wants?

        """
        # todo
        pass

    def ball_search_failed(self):
        """Ball Search did not find the ball."""
        self.log.debug("Ball Search failed to find a ball. Disabling.")
        self.ball_search_end()
        self.ball_lost()

    def ball_search_end(self):
        """End the ball search, either because we found the ball or
        are giving up."""
        self.log.debug("Ball search ending")
        self.flag_ball_search_in_progress = False
        self.machine.events.post("ball_search_end")
        # todo cancel the delay for phase 2 if we had one

    def ball_lost(self):
        """Mark a ball as lost"""
        self.num_balls_known = self.num_balls_contained
        self.num_balls_missing = self.machine.config['Machine']\
            ['Balls Installed'] - self.num_balls_contained
        self.num_balls_uncontained = 0
        self.log.debug("Ball(s) Marked Lost. Known: %s, Missing: %s",
                       self.num_balls_known, self.num_balls_missing)

        # todo audit balls lost

        # If we lost balls that were in play, stealth launch the number in play
        if self.num_balls_in_play:
            self.ball_add_live(self.num_balls_in_play, stealth=True, auto=True)

# The MIT License (MIT)

# Copyright (c) 2013-2014 Brian Madden and Gabe Knuth

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