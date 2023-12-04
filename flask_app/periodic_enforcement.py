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

import logging
import os
import threading
from datetime import datetime

import meraki
from dotenv import load_dotenv

import db
from mx_config import MerakiMXConfig

# Load in Environment Variables
load_dotenv()
MERAKI_API_KEY = os.getenv('MERAKI_API_KEY')

# Meraki Dashboard Instance
dashboard = meraki.DashboardAPI(api_key=MERAKI_API_KEY, suppress_logging=True)

# Set up logging (only to stdout -> cron.log)
logger = logging.getLogger('my_logger')
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s %(levelname)s: %(funcName)s:%(lineno)d - %(message)s')

# log to stdout
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

logger.addHandler(stream_handler)

# Get absolute path to resources folder
script_dir = os.path.dirname(os.path.abspath(__file__))


def thread_wrapper(current_config, baseline_filename, exception_filename):
    """
    Wrapper method to trigger baseline and exception template uploads to each network, each in their own thread for
    multiprocessing
    :param current_config: MX Config Object used for uploading and processing combined template config to each network
    :param baseline_filename: File name for baseline template
    :param exception_filename: File name for exception template
    """
    # Trigger settings upload to network
    current_config.upload(baseline_filename, exception_filename)


def main():
    """
    Run synchronization for each network with respect to assigned base template and exception template
    :return:
    """
    current_datetime = datetime.now()
    formatted_datetime = current_datetime.strftime("%Y-%m-%d %H:%M:%S")

    logger.info(f"Starting Periodic sync at: {formatted_datetime}")

    # Connection to DB (one-time)
    conn_one_time = db.create_connection(os.path.join(script_dir, 'sqlite.db'))

    # Get the table of base templates and exception templates
    base_templates = db.query_all_base_templates(conn_one_time)
    base_templates = {item[0]: item[1] for item in base_templates}

    exception_templates = db.query_all_exception_templates(conn_one_time)
    exception_templates = {item[0]: item[1] for item in exception_templates}

    # Get a list of available files for exceptions for drop down table fields
    configs_path = os.path.join(script_dir, 'mx_configs')
    config_files = [f for f in os.listdir(configs_path) if
                    os.path.isfile(os.path.join(configs_path, f)) and f != ".gitignore"]
    logger.info(f"Current templates available in mx_configs folder: {config_files}")

    logger.info(f"Baseline Template Table: {base_templates}")
    logger.info(f"Exception Template Table: {exception_templates}")

    threads = []
    # Iterate through orgs and networks grabbing respective templates, kick off upload workflow
    orgs = dashboard.organizations.getOrganizations()
    for org in orgs:
        # Grab baseline file name
        baseline_filename = base_templates[org['id']]
        if not baseline_filename:
            baseline_filename = "None"

        try:
            networks = dashboard.organizations.getOrganizationNetworks(org['id'], total_pages='all')
        except:
            continue

        for network in networks:
            if 'appliance' in network['productTypes']:
                # Grab Exception File Name
                exception_filename = exception_templates[network['id']]
                if not exception_filename:
                    exception_filename = "None"

                # Sanity check assigned files are present and haven't been removed from mx_configs
                if baseline_filename != 'None' and baseline_filename not in config_files:
                    logger.error(
                        f"Assigned base template {baseline_filename} not found... skipping sync(s). Please assign a valid file.")
                    continue

                if exception_filename != 'None' and exception_filename not in config_files:
                    logger.error(
                        f"Assigned exception template {exception_filename} not found... skipping sync(s). Please assign a valid file.")

                    continue

                # Sanity Check (None, None) -> there's no work to do
                if baseline_filename == "None" and exception_filename == "None":
                    continue
                else:
                    # Upload config to network - return action batch of configs to apply
                    current_config = MerakiMXConfig(org['id'], network['id'], logger)

                    # Spawn a background thread to perform settings upload to each network
                    thread = threading.Thread(target=thread_wrapper,
                                              args=(current_config, baseline_filename, exception_filename,))
                    threads.append(thread)

    # Start all threads.
    for t in threads:
        t.start()

    # Wait for all threads to finish.
    for t in threads:
        t.join()

    # Close DB connection (one-time)
    db.close_connection(conn_one_time)


if __name__ == "__main__":
    main()
