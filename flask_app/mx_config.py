#!/usr/bin/env python3
"""
Copyright (c) 2023 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

__author__ = "Trevor Maco <tmaco@cisco.com>"
__copyright__ = "Copyright (c) 2023 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import json
import os

import meraki
from dotenv import load_dotenv

import config

# Load ENV Variable
load_dotenv()
MERAKI_API_KEY = os.getenv("MERAKI_API_KEY")

# Meraki Dashboard Instance
dashboard = meraki.DashboardAPI(api_key=MERAKI_API_KEY, suppress_logging=True)

# Get absolute path to resources folder
script_dir = os.path.dirname(os.path.abspath(__file__))
folder_path = os.path.join(script_dir, 'mx_configs')


def load_config_from_file(file_name):
    """
    Read a template config from a .json file in mx_configs
    :param file_name: Template filename
    :return: Config from template
    """
    # Determine file path
    path_to_file = os.path.join(folder_path, file_name)

    # Read in json file, convert to python dictionary structure
    with open(path_to_file, 'r') as fp:
        try:
            new_config = json.load(fp)
        except Exception as e:
            return str(e)

    return new_config


def combine_configs(baseline_config, exception_config):
    """
    Combine baseline template and exception template configs into a 'combined' config. If duplicate entries present,
    only take one.
    :param baseline_config: Baseline template config object
    :param exception_config: Exception template config object
    :return: Combined template config object
    """
    new_config = {}

    # Iterate through combined list of config keys
    for key in baseline_config.keys() | exception_config.keys():
        if key in baseline_config and key in exception_config:
            if isinstance(baseline_config[key], list) and isinstance(exception_config[key], list):
                # Merge the lists while avoiding duplicates
                new_config[key] = baseline_config[key] + [item for item in exception_config[key] if
                                                          item not in baseline_config[key]]
            elif isinstance(baseline_config[key], dict) and isinstance(exception_config[key], dict):
                # Combined configs recursively for nested dictionary structures
                new_config[key] = combine_configs(baseline_config[key], exception_config[key])
        elif key in baseline_config:
            # If config is only present in baseline, add to combined config
            new_config[key] = baseline_config[key]
        elif key in exception_config:
            # If config is only present in exception, add to combined config
            new_config[key] = exception_config[key]

    return new_config


class MerakiMXConfig:
    """
    Class representing MX Security Config settings (either downloaded from Meraki, or obtained from reading a json file)
    Supports uploading and downloading of settings.
    """

    def __init__(self, org_id, net_id, logger):
        self.org_id = org_id
        self.net_id = net_id
        # Common logger used with main flask app
        self.logger = logger
        self.net_name = None
        self.l3OutRules = {}
        self.l7Rules = {}
        self.contentRules = {}
        self.upload_errors = []

        # Get Network Name
        self.get_network_name()

    def get_network_name(self):
        """
        Get network name (useful for webpage table displays)
        """
        try:
            network = dashboard.networks.getNetwork(self.net_id)
            self.net_name = network['name']
        except Exception as e:
            self.upload_errors.append({'network': self.net_name, 'error': str(e)})

    def get_l3_out_rules(self):
        """
        Get L3 Outbound Rules
        """
        try:
            firewall_rules = dashboard.appliance.getNetworkApplianceFirewallL3FirewallRules(self.net_id)

            # Remove default any any any rule
            cleaned_rules = [rule for rule in firewall_rules["rules"] if rule['comment'] != "Default rule"]

            self.l3OutRules = {'rules': cleaned_rules}
            self.logger.info(f"Found the following L3 Outbound Rules: {self.l3OutRules}")
        except Exception as e:
            self.logger.error(f'Network ({self.net_name}) L3 Outbound Rules GET Failed: {str(e)}')

    def get_l7_rules(self):
        """
        Get L7 Rules
        """
        try:
            firewall_rules = dashboard.appliance.getNetworkApplianceFirewallL7FirewallRules(self.net_id)

            self.l7Rules = firewall_rules
            self.logger.info(f"Found the following L7 Rules: {firewall_rules}")
        except Exception as e:
            self.logger.error(f'Network ({self.net_name}) L7 Rules GET Failed: {str(e)}')

    def get_content_filtering_rules(self):
        """
        Get Content Filtering Rules (URL, Content)
        """
        try:
            firewall_rules = dashboard.appliance.getNetworkApplianceContentFiltering(self.net_id)

            self.contentRules = firewall_rules
            self.logger.info(f"Found the following Content Rules: {self.contentRules}")
        except Exception as e:
            self.logger.error(f'Network ({self.net_name}) Content Rules GET Failed: {str(e)}')

    def get_existing_security_config(self):
        """
        Wrapper method to support adding more security features later, calls individual security settings getters
        """
        # Get current existing security config(s)
        self.get_l3_out_rules()
        self.get_l7_rules()
        self.get_content_filtering_rules()

    def download(self):
        """
        Combine security config settings, and write settings to a .json file (using the network name as the file name)
        """
        # Consolidate config into a single dictionary (only settings which are tracked!)
        data = {}
        if config.tracked_settings['mx_l3_outbound_firewall'] and len(self.l3OutRules) > 0:
            data['mx_l3_outbound_firewall'] = self.l3OutRules

        if config.tracked_settings['mx_l7_firewall'] and len(self.l7Rules) > 0:
            data['mx_l7_firewall'] = self.l7Rules

        if config.tracked_settings['mx_content_rules'] and len(self.contentRules) > 0:
            data['mx_content_rules'] = self.contentRules

        self.logger.info(
            f"Downloading the following configs (based on config.py): {list(config.tracked_settings.keys())}")

        # Download security config to json file
        file_name = self.net_name.replace(" ", "_") + '.json'
        path_to_config_file = os.path.join(folder_path, file_name)

        self.logger.info(f"Saving to: {path_to_config_file}")

        # If file already exists, it will be overridden (handles updating a template via meraki)
        with open(path_to_config_file, 'w') as json_file:
            json.dump(data, json_file)

    def get_upload_errors(self):
        """
        Return list of upload errors (if any encountered when applying each security setting)
        """
        return self.upload_errors

    def upload(self, baseline_file_name, exception_file_name):
        """
        Upload security config settings to a new network, combine base and exception templates into a master template,
        then upload individual security settings
        :param baseline_file_name: Base template file name
        :param exception_file_name: Exception Template file name
        :return:
        """
        # Build configuration to apply to network
        # Case 1: Baseline is not None, but Exceptions is None
        if baseline_file_name != "None" and exception_file_name == "None":
            new_config = load_config_from_file(baseline_file_name)

            if isinstance(new_config, str):
                # If there's a problem reading the file, return
                self.logger.error(f"There was a problem reading json file {baseline_file_name}: {new_config}")
                self.upload_errors.append({'network': self.net_name,
                                           'error': f'There was a problem reading json file {baseline_file_name}: {new_config}'})
                return

        # Case 2: Baseline is None, but Exceptions is not None
        elif baseline_file_name == "None" and exception_file_name != "None":
            new_config = load_config_from_file(exception_file_name)

            if isinstance(new_config, str):
                # if there's a problem reading the file, return
                self.logger.error(f"There was a problem reading json file {exception_file_name}: {new_config}")
                self.upload_errors.append({'network': self.net_name,
                                           'error': f'There was a problem reading json file {exception_file_name}: {new_config}'})
                return

        # Case 3: both the baseline and exceptions are not none
        else:
            baseline_config = load_config_from_file(baseline_file_name)

            if isinstance(baseline_config, str):
                # if there's a problem reading the file, return
                self.logger.error(f"There was a problem reading json file {baseline_file_name}: {baseline_config}")
                self.upload_errors.append({'network': self.net_name,
                                           'error': f'There was a problem reading json file {baseline_file_name}: {baseline_config}'})
                return

            exception_config = load_config_from_file(exception_file_name)

            if isinstance(exception_config, str):
                # if there's a problem reading the file, return
                self.logger.error(f"There was a problem reading json file {exception_file_name}: {exception_config}")
                self.upload_errors.append({'network': self.net_name,
                                           'error': f'There was a problem reading json file {exception_file_name}: {exception_config}'})
                return

            # Combine both valid configs based on key
            new_config = combine_configs(baseline_config, exception_config)

        # Upload settings to target network
        upload_status_tracker = {}
        for config_item in new_config:
            # L3 Outbound Rules
            if config_item == "mx_l3_outbound_firewall" and config.tracked_settings['mx_l3_outbound_firewall']:
                try:
                    response = dashboard.appliance.updateNetworkApplianceFirewallL3FirewallRules(
                        self.net_id,
                        rules=new_config[config_item]["rules"]
                    )
                    upload_status_tracker[config_item] = "Success"
                except Exception as e:
                    self.logger.error(f'Network ({self.net_name}) L3 Outbound Rules Upload Failed: {str(e)}')
                    self.upload_errors.append({'network': self.net_name, 'error': str(e)})
                    upload_status_tracker[config_item] = "Failure (see Errors)"

            # L7 Rules
            if config_item == 'mx_l7_firewall' and config.tracked_settings['mx_l7_firewall']:
                try:
                    response = dashboard.appliance.updateNetworkApplianceFirewallL7FirewallRules(self.net_id,
                                                                                                 rules=new_config[
                                                                                                     config_item][
                                                                                                     'rules']
                                                                                                 )
                    upload_status_tracker[config_item] = "Success"
                except Exception as e:
                    self.logger.error(f'Network ({self.net_name}) L7 Rules Upload Failed: {str(e)}')
                    self.upload_errors.append({'network': self.net_name, 'error': str(e)})
                    upload_status_tracker[config_item] = "Failure (see Errors)"

            # Content Filtering Rules
            if config_item == 'mx_content_rules' and config.tracked_settings['mx_content_rules']:
                try:
                    response = dashboard.appliance.updateNetworkApplianceContentFiltering(
                        self.net_id,
                        allowedUrlPatterns=new_config[config_item]['allowedUrlPatterns'],
                        blockedUrlPatterns=new_config[config_item]['blockedUrlPatterns'],
                        blockedUrlCategories=[item['id'] for item in new_config[config_item]['blockedUrlCategories']]
                    )
                    upload_status_tracker[config_item] = "Success"
                except Exception as e:
                    self.logger.error(f'Network ({self.net_name}) Content Rules Upload Failed: {str(e)}')
                    self.upload_errors.append({'network': self.net_name, 'error': str(e)})
                    upload_status_tracker[config_item] = "Failure (see Errors)"

        self.logger.info(f"Upload Status ({self.net_name}) for each piece of the config: {upload_status_tracker}")
