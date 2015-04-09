"""Contains the Config class with utility configuration methods"""

# config.py
# Mission Pinball Framework
# Written by Brian Madden & Gabe Knuth
# Released under the MIT License. (See license info at the end of this file.)

# Documentation and more info at http://missionpinball.com/mpf
import logging
import os
import sys

import yaml
from copy import deepcopy

from mpf.system.timing import Timing

log = logging.getLogger('ConfigProcessor')


class Config(object):

    @staticmethod
    def load_config_yaml(config=None, yaml_file=None,
                         new_config_dict=None):
        """Merges a new config dictionary into an existing one.

        This method does what we call a "deep merge" which means it merges
        together subdictionaries instead of overwriting them. See the
        documentation for `meth:dict_merge` for a description of how this
        works.

        If the config dictionary you're merging in also contains links to
        additional config files, it will also merge those in.

        At this point this method loads YAML files, but it would be simple to
        load them from JSON, XML, INI, or existing python dictionaires.

        Args:
            config: The optional current version of the config dictionary that
                you're building up. If you don't pass a dictionary, this method
                will create one.
            yaml_file: A YAML file containing the settings to deep merge into
                the config dictionary. This method will try to find a file
                with that name and open it to read in the settings. It will
                first try to open it as a file directly (including any path
                that's there). If that doesn't work, it will try to open the
                file using the last path that worked. (This path is stored in
                `config['Config_path']`.)
            new_config_dict: A dictionary of settings to merge into the config
                dictionary.

        Note that you only need to specify a yaml_file or new_config_dictionary,
        not both.

        Returns: Python dictionary which is your source with all the new config
            options merged in.

        """

        if not config:
            config = dict()

        new_updates = dict()

        # If we were passed a config dict, load from there
        if type(new_config_dict) == dict:
            new_updates = new_config_dict

        # If not, do we have a yaml_file?
        elif yaml_file:
            if os.path.isfile(yaml_file):
                config_location = yaml_file
                # Pull out the path in case we need it later
                config['Config_path'] = os.path.split(yaml_file)[0]
            elif os.path.isfile(os.path.join(config['Config_path'],
                                             yaml_file)):
                config_location = os.path.join(config['Config_path'],
                                               yaml_file)
            else:
                log.critical("Couldn't find file: %s.", yaml_file)
                sys.exit()

        if config_location:
            try:
                log.info("Loading configuration from file: %s", config_location)
                new_updates = yaml.load(open(config_location, 'r'))
            except yaml.YAMLError, exc:
                if hasattr(exc, 'problem_mark'):
                    mark = exc.problem_mark
                    log.critical("Error found in config file %s. Line %s, "
                                 "Position %s", config_location, mark.line+1,
                                 mark.column+1)
                    sys.exit()
            except:
                log.critical("Couldn't load from file: %s", yaml_file)
                sys.exit()

        config = Config.dict_merge(config, new_updates)

        # now check if there are any more updates to do.
        # iterate and remove them

        try:
            if 'Config' in config:
                if yaml_file in config['Config']:
                    config['Config'].remove(yaml_file)
                if config['Config']:
                    config = Config.load_config_yaml(config=config,
                                              yaml_file=config['Config'][0])
        except:
            log.critical("No configuration file found, or config file is "
                              "empty. But congrats! Your game works! :)")
            sys.exit()

        return config

    @staticmethod
    def process_config(config_spec, source, target=None):
        config_spec = yaml.load(config_spec)
        processed_config = source

        for k in config_spec.keys():
            if k in source:
                processed_config[k] = Config.validate_config_item(
                    config_spec[k], source[k])
            else:
                log.debug('Processing default settings for key "%s:"', k)
                processed_config[k] = Config.validate_config_item(
                    config_spec[k])

        if target:
            processed_config = Config.dict_merge(target, processed_config)

        return processed_config

    @staticmethod
    def validate_config_item(spec, item='item not in config!@#'):

        default = 'default required!@#'

        if '|' in spec: # list|none
            item_type, default = spec.split('|')
            # item_type = list, default = none
            if type(default) is str and default.lower() == 'none':
                default = None
        else:  #int
            item_type = spec  # item_type: int
            #default = no default!@#

        if item == 'item not in config!@#':
            if default == 'default required!@#':
                log.error('Required setting missing from config file. Run with '
                          'verbose logging and look for the last '
                          'ConfigProcessor entry above this line to see where '
                          'the problem is.')
                sys.exit()
            else:
                item = default

        if item_type == 'list':
            return Config.string_to_list(item)
        elif item_type == 'int':
            return int(item)
        elif item_type == 'float':
            return float(item)
        elif item_type == 'string':
            return str(item)
        elif item_type == 'boolean':
            return bool(item)
        elif item_type == 'ms':
            return Timing.string_to_ms(item)
        elif item_type == 'secs':
            return Timing.string_to_secs(item)
        elif item_type == 'list_of_lists':
            return Config.list_of_lists(item)

    @staticmethod
    def dict_merge(a, b, combine_lists=True):
        """Recursively merges dictionaries.

        Used to merge dictionaries of dictionaries, like when we're merging
        together the machine configuration files. This method is called
        recursively as it finds sub-dictionaries.

        For example, in the traditional python dictionary
        update() methods, if a dictionary key exists in the original and
        merging-in dictionary, the new value will overwrite the old value.

        Consider the following example:

        Original dictionary:
        `config['foo']['bar'] = 1`

        New dictionary we're merging in:
        `config['foo']['other_bar'] = 2`

        Default python dictionary update() method would have the updated
        dictionary as this:

        `{'foo': {'other_bar': 2}}`

        This happens because the original dictionary which had the single key
        `bar` was overwritten by a new dictionary which has a single key
        `other_bar`.)

        But really we want this:

        `{'foo': {'bar': 1, 'other_bar': 2}}`

        This code was based on this:
        https://www.xormedia.com/recursively-merge-dictionaries-in-python/

        Args:
            a (dict): The first dictionary
            b (dict): The second dictionary
            combine_lists (bool):
                Controls whether lists should be combined (extended) or
                overwritten. Default is `True` which combines them.

        Returns:
            The merged dictionaries.
        """
        #log.info("Dict Merge incoming A %s", a)
        #log.info("Dict Merge incoming B %s", b)
        if not isinstance(b, dict):
            return b
        result = deepcopy(a)
        for k, v in b.iteritems():
            if k in result and isinstance(result[k], dict):
                result[k] = Config.dict_merge(result[k], v)
            elif k in result and isinstance(result[k], list) and combine_lists:
                result[k].extend(v)
            else:
                result[k] = deepcopy(v)
        #log.info("Dict Merge result: %s", result)
        return result

    @staticmethod
    def string_to_list(string):
        """ Converts a comma-separated and/or space-separated string into a
        python list.

        Args:
            string: The string you'd like to convert.

        Returns:
            A python list object containing whatever was between commas and/or
            spaces in the string.
        """
        if type(string) is str:
            # Convert commas to spaces, then split the string into a list
            new_list = string.replace(',', ' ').split()
            # Look for string values of "None" and convert them to Nonetypes.
            for index, value in enumerate(new_list):
                if type(value) is str and value.lower() == 'none':
                    new_list[index] = None
            return new_list

        elif type(string) is list:
            return string  # If it's already a list, do nothing

        elif string is None:
            return []  # If it's None, make it into an empty list
        else:
            # if we're passed anything else, just make it into a list
            return [string]

    @staticmethod
    def list_of_lists(incoming_string):
        """ Converts an incoming string or list into a list of lists. """
        final_list = list()

        if type(incoming_string) is str:
            final_list = [Config.string_to_list(incoming_string)]

        else:
            for item in incoming_string:
                final_list.append(Config.string_to_list(item))

        return final_list

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
