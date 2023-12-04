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

# Import Section
import datetime
import json
import logging
import os
import threading
from logging.handlers import TimedRotatingFileHandler

import meraki
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, url_for, redirect, g
from flask_caching import Cache

import config
import db
from mx_config import MerakiMXConfig

# Absolute Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, 'sqlite.db')
configs_path = os.path.join(script_dir, 'mx_configs')
logs_path = os.path.join(script_dir, 'logs')

# Global variables
app = Flask(__name__)
app.config['DATABASE'] = db_path

# Configuring Flask-Caching
cache = Cache(app, config={'CACHE_TYPE': 'simple'})

# Load in Environment Variables
load_dotenv()
MERAKI_API_KEY = os.getenv('MERAKI_API_KEY')

# Meraki Dashboard Instance
dashboard = meraki.DashboardAPI(api_key=MERAKI_API_KEY, suppress_logging=True)

# Global progress variable
progress = 0
upload_errors = {}

# Set up logging
logger = logging.getLogger('my_logger')
logger.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s %(levelname)s: %(funcName)s:%(lineno)d - %(message)s')

# log to stdout
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)

# log to files (last 7 days, rotated at midnight local time each day)
log_file = os.path.join(logs_path, 'portal_logs.log')
file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=7)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(stream_handler)

## One time actions ##

# Build drop down menus for organization and network selection, mapping or orgs/networks to id
organizations = dashboard.organizations.getOrganizations()
sorted_organizations = sorted(organizations, key=lambda x: x['name'])

# Connection to DB (one-time)
conn_one_time = db.create_connection(app.config['DATABASE'])

org_to_id = {}
network_to_id = {}
DROPDOWN_CONTENT = []
for organization in sorted_organizations:
    # Add Org to Base Templates DB Table (if not present already)
    db.add_template(conn_one_time, 'base', organization['id'], None)
    org_data = {'orgaid': organization['id'], 'organame': organization['name']}

    try:
        networks = dashboard.organizations.getOrganizationNetworks(organization['id'], total_pages='all')

        network_data = []
        network_ids = []
        for network in networks:
            # Filter for networks with appliance (MX) only
            if 'appliance' in network['productTypes']:
                # Add Network to Exception Template DB Table (if not present already)
                db.add_template(conn_one_time, 'exception', network['id'], None)

                network_data.append({'networkid': network['id'], 'networkname': network['name']})
                network_ids.append(network['id'])

                # Add new entries to data structures
                network_to_id[network['name']] = network['id']

        # Associate networks with their org
        org_data['networks'] = network_data

        # Add new entries to data structures
        DROPDOWN_CONTENT.append(org_data)
        org_to_id[organization['name']] = {'id': organization['id'], 'network_ids': network_ids}

    except Exception as e:
        logger.error(f"Error retrieving networks for organization ID {organization['id']}: {e}")

# Close DB connection (one-time)
db.close_connection(conn_one_time)


# Methods
def getSystemTimeAndLocation():
    """
    Return location and time of accessing device (used on all webpage footers)
    """
    # Request user ip
    userIPRequest = requests.get('https://get.geojs.io/v1/ip.json')
    userIP = userIPRequest.json()['ip']

    # Request geo information based on ip
    geoRequestURL = 'https://get.geojs.io/v1/ip/geo/' + userIP + '.json'
    geoRequest = requests.get(geoRequestURL)
    geoData = geoRequest.json()

    # Create info string
    location = geoData['country']
    timezone = geoData['timezone']
    current_time = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p")
    timeAndLocation = "System Information: {}, {} (Timezone: {})".format(location, current_time, timezone)

    return timeAndLocation


def get_conn():
    """
    Open a new database connection if there is none yet for the current application context.
    """
    if 'conn' not in g:
        g.conn = db.create_connection(app.config['DATABASE'])
    return g.conn


@app.teardown_appcontext
def close_conn(error):
    """
    Closes the database again at the end of the request (using teardown of app context)
    """
    conn = g.pop('conn', None)
    if conn is not None:
        db.close_connection(conn)


def thread_wrapper(current_config, progress_inc, baseline_filename, exception_filename):
    """
    Wrapper method to trigger baseline and exception template uploads to each network, each in their own thread for
    multiprocessing
    :param current_config: MX Config Object used for uploading and processing combined template config to each network
    :param progress_inc: Increment value for global progress value (used for progress bar displayed on webpage)
    :param baseline_filename: File name for baseline template
    :param exception_filename: File name for exception template
    """
    global progress, upload_errors

    # Trigger settings upload to network
    current_config.upload(baseline_filename, exception_filename)

    # Update Progress for display bar
    progress += progress_inc

    # Retrieve upload errors (if any), append to global errors for webpage display
    current_run_errors = current_config.get_upload_errors()
    for error in current_run_errors:
        if error['network'] in upload_errors:
            upload_errors[error['network']].append(error['error'])
        else:
            upload_errors[error['network']] = [error['error']]


@cache.memoize(timeout=120)  # Cache the result for 2 minutes
def get_mx_config_information(selected_organization, selected_network):
    """
    Get the current MX Security configs for the selected Network (wrapped in a separate method to support caching results)
    :param selected_organization: Org ID selected from drop down
    :param selected_network: Network ID selected from drop down
    :return: MX Config Object with current security configs
    """
    # Define class object representing current MX Security Settings
    current_config = MerakiMXConfig(selected_organization, selected_network, logger)

    # Retrieve all current security configs
    current_config.get_existing_security_config()

    return current_config


# Routes
@app.route('/progress')
def get_progress():
    """
    Get current process progress for progress bar display
    """
    global progress

    # Return the progress as a JSON response
    return jsonify({'progress': progress})


@app.route('/', methods=['GET'])
def index():
    """
    Landing page, display a table showing base templates and exception templates applied at each network
    """
    logger.info(f"Main Index {request.method} Request:")

    # Get DB connection
    conn = get_conn()

    # Get a list of organization names
    org_names = org_to_id.keys()

    # Get the table of base templates and exception templates
    base_templates = db.query_all_base_templates(conn)
    base_templates = {item[0]: item[1] for item in base_templates}

    exception_templates = db.query_all_exception_templates(conn)
    exception_templates = {item[0]: item[1] for item in exception_templates}

    # Build a display list for each orgs networks (show network name, base template, exception template)
    network_displays = []
    for org in DROPDOWN_CONTENT:
        org_networks = []
        for network in org['networks']:
            network_display = {'id': network['networkid'], 'org_name': org['organame'],
                               'net_name': network['networkname'],
                               'base_template': base_templates[org['orgaid']],
                               'exception_template': exception_templates[network['networkid']]}
            org_networks.append(network_display)

        network_displays.append(org_networks)

    logger.info(f"Current State of Template Assignments: {network_displays}")

    return render_template('index.html', hiddenLinks=False, org_names=org_names, networks=network_displays,
                           timeAndLocation=getSystemTimeAndLocation())


@app.route('/download_baseline', methods=['GET', 'POST'])
def download_baseline():
    """
    Download template (representing current security config) from an existing network. This template can be used as
    either a baseline or exception template for other networks)
    """
    logger.info(f'Download Baseline {request.method} Request:')

    # If success is present (during redirect after successfully updating SSID), extract URL param
    if request.args.get('success'):
        success = request.args.get('success')
    else:
        success = False

    # Extract which org and network (ids) were selected in the drop-down
    selected_organization = request.form.get('organizations_select')
    selected_network = request.form.get('networks_select')

    selected_elements = {
        'organization': selected_organization,
        'network_id': selected_network
    }

    # Handle the form submission, trigger a download of the current security configs
    current_config = None
    if request.method == 'POST':
        logger.info(f"POST data received from client: {request.form.to_dict()}")

        # Get the current MX security policies configured for the network (cached for 2 minutes to avoid redundant
        # class creation on download)
        current_config = get_mx_config_information(selected_organization, selected_network)

        # Check the value of the 'form_type' field
        form_type = request.form.get('form_type')

        # If this is the download form, download current config to a .json file (located in mx_configs folder)
        if form_type == 'download_form':
            logger.info("Download form request received:")
            current_config.download()

            return redirect(url_for('download_baseline', success=True))

    # Render page
    return render_template('download_baseline.html', hiddenLinks=False, dropdown_content=DROPDOWN_CONTENT,
                           selected_elements=selected_elements, current_config=current_config, success=success,
                           timeAndLocation=getSystemTimeAndLocation(), tracked_settings=config.tracked_settings)


@app.route('/assign_baseline', methods=['GET', 'POST'])
def assign_baseline():
    """
    Assign a baseline template to an organization (assigned at org level, maintained in sqlite db)
    """
    logger.info(f'Assign Baseline Template {request.method} Request:')

    # Get DB connection
    conn = get_conn()

    # If success is present (during redirect after successfully updating SSID), extract URL praram
    if request.args.get('success'):
        success = request.args.get('success')
    else:
        success = False

    # Get the table of base templates
    base_templates = db.query_all_base_templates(conn)
    base_templates = {item[0]: item[1] for item in base_templates}

    # Build a display list for each org (show org name, existing base template)
    org_display = []
    for org in org_to_id:
        org_display.append({'org_name': org, "existing_base_template": base_templates[org_to_id[org]['id']]})

    # Get a list of available baseline files for dropdown table fields
    config_files = [f for f in os.listdir(configs_path) if
                    os.path.isfile(os.path.join(configs_path, f)) and f != ".gitignore"]

    # Handle the form submission, assign baseline templates to one or more orgs, update DB
    if request.method == 'POST':
        logger.info(f"POST data received from client: {request.form.to_dict()}")
        logger.info(f"Current templates available in mx_configs folder: {config_files}")

        # Retrieve data from the request
        data = request.form.get('data')

        # Convert JSON string to Python list of dictionaries
        baselines = json.loads(data)

        logger.info(f"Baseline Template Table Before: {base_templates}")

        # Add or update db table with baseline templates
        for baseline in baselines:
            if baseline['templateValue'] == 'existing':
                # Skip entries with no changes to their assigned template
                pass
            elif baseline['templateValue'] == 'None':
                # If baseline value is 'none', remove baseline template
                db.remove_template(conn, 'base', org_to_id[baseline['orgName']]['id'])
            else:
                # Check if baseline template is assigned as exception template (if it is, remove it as exception
                # template)
                exceptions = db.query_all_exception_templates(conn)
                exceptions_filtered = [item for item in exceptions if baseline['templateValue'] in item]

                if len(exceptions_filtered) > 0:
                    org_networks = org_to_id[baseline['orgName']]['network_ids']

                    for exception in exceptions_filtered:
                        # If a network with the exception template is a part of the org we want to assign the same
                        # base template too, remove the exception template
                        if exception[0] in org_networks:
                            db.remove_template(conn, 'exception', exception[0])

                # Update template with new template
                db.update_template(conn, 'base', org_to_id[baseline['orgName']]['id'], baseline['templateValue'])

        # Get the table of base templates
        base_templates = db.query_all_base_templates(conn)
        base_templates = {item[0]: item[1] for item in base_templates}

        logger.info(f"Baseline Template Table After: {base_templates}")

        # Support AJAX redirect to display success message and update display tables on screen
        return jsonify({'redirect_url': url_for('assign_baseline', success=True)})

    return render_template('assign_baseline.html', hiddenLinks=False, orgs=org_display, config_files=config_files,
                           error=False, success=success, timeAndLocation=getSystemTimeAndLocation())


@app.route('/assign_exception', methods=['GET', 'POST'])
def assign_exception():
    """
    Assign exception templates to networks in organizations (maintained in sqlite db)
    """
    logger.info(f'Assign Exception {request.method} Request:')

    # Get DB connection
    conn = get_conn()

    # If success is present (during redirect after successfully updating SSID), extract URL param
    if request.args.get('success'):
        success = request.args.get('success')
    else:
        success = False

    # Get a list of organization names
    org_names = org_to_id.keys()

    # Get the table of base templates
    base_templates = db.query_all_base_templates(conn)
    base_templates = {item[0]: item[1] for item in base_templates}

    # Get the table of exception templates
    exception_templates = db.query_all_exception_templates(conn)
    exception_templates = {item[0]: item[1] for item in exception_templates}

    # Get a list of available files for exceptions for drop down table fields
    config_files = [f for f in os.listdir(configs_path) if
                    os.path.isfile(os.path.join(configs_path, f)) and f != ".gitignore"]

    # Build a display list for each orgs networks (show network name, base template, exception template)
    network_displays = []
    for org in DROPDOWN_CONTENT:
        org_networks = []
        for network in org['networks']:
            network_display = {'id': network['networkid'], 'org_name': org['organame'],
                               'net_name': network['networkname'],
                               'base_template': base_templates[org['orgaid']],
                               "existing_exception_template": exception_templates[network['networkid']]}
            org_networks.append(network_display)

        network_displays.append(org_networks)

    # Handle the form submission, upload templates to the networks!
    if request.method == 'POST':
        logger.info(f"POST data received from client: {request.form.to_dict()}")
        logger.info(f"Current templates available in mx_configs folder: {config_files}")

        # Retrieve data from the request, baseline and exception template selections from webpage
        data = request.form.get('data')

        # Convert JSON string to Python list of dictionaries
        template_selections = json.loads(data)

        logger.info(f"Exception Template Table Before: {exception_templates}")

        # Iterate through table selections for baseline and exception templates
        for template_selection in template_selections:
            if template_selection['exceptionTemplateValue'] == 'existing':
                # Skip entries with no changes to their assigned template
                pass
            elif template_selection['exceptionTemplateValue'] == 'None':
                # If baseline value is 'none', remove exception template
                db.remove_template(conn, 'exception', network_to_id[template_selection['netName']])
            else:
                # Update exception template with new template (if it isn't equal to baseline)
                db.update_template(conn, 'exception', network_to_id[template_selection['netName']],
                                   template_selection['exceptionTemplateValue'])

        # Get the table of exception templates
        exception_templates = db.query_all_exception_templates(conn)
        exception_templates = {item[0]: item[1] for item in exception_templates}

        logger.info(f"Exception Template Table After: {exception_templates}")

        # Support AJAX redirect to display success message and update display tables on screen
        return jsonify({'redirect_url': url_for('assign_exception', success=True)})

    return render_template('assign_exception.html', hiddenLinks=False, org_names=org_names, networks=network_displays,
                           config_files=config_files, success=success,
                           timeAndLocation=getSystemTimeAndLocation())


@app.route('/deploy_templates', methods=['GET', 'POST'])
def deploy_templates():
    """
    Deploy baseline and exception templates to each network based on checkbox selections
    """
    global progress, upload_errors
    logger.info(f'Deploy Templates {request.method} Request:')

    # Get DB connection
    conn = get_conn()

    # If success is present (during redirect after successfully updating SSID), extract URL param
    if request.args.get('success'):
        success = request.args.get('success')
    else:
        # Clear any previous upload errors, start fresh
        upload_errors.clear()

        success = False

    # Get the table of base templates
    base_templates = db.query_all_base_templates(conn)
    base_templates = {item[0]: item[1] for item in base_templates}

    # Get the table of exception templates
    exception_templates = db.query_all_exception_templates(conn)
    exception_templates = {item[0]: item[1] for item in exception_templates}

    # Get a list of available files for exceptions for drop down table fields
    config_files = [f for f in os.listdir(configs_path) if
                    os.path.isfile(os.path.join(configs_path, f)) and f != ".gitignore"]

    # Build a display list for each orgs networks (show network name, base template, exception template)
    network_displays = []
    for org in DROPDOWN_CONTENT:
        for network in org['networks']:
            network_display = {'id': network['networkid'], 'org_name': org['organame'],
                               'net_name': network['networkname'],
                               'base_template': base_templates[org['orgaid']],
                               "existing_exception_template": exception_templates[network['networkid']]}
            network_displays.append(network_display)

    # Handle the form submission, upload templates to the networks!
    if request.method == 'POST':
        progress = 0

        # Clear any previous upload errors, start fresh
        upload_errors.clear()

        logger.info(f"POST data received from client: {request.form.to_dict()}")
        logger.info(f"Current templates available in mx_configs folder: {config_files}")

        # Retrieve data from the request, baseline and exception template selections from webpage
        data = request.form.get('data')

        # Convert JSON string to Python list of dictionaries
        template_selections = json.loads(data)

        # Calculate progress increment (100 / length floored)
        progress_inc = 100 // len(template_selections)

        threads = []
        # Iterate through table selections for baseline and exception templates
        for template_selection in template_selections:
            # Grab baseline file name
            baseline_filename = template_selection['baseTemplateValue']
            exception_filename = template_selection['exceptionTemplateValue']

            # Sanity check assigned files are present and haven't been removed from mx_configs
            if baseline_filename != 'None' and baseline_filename not in config_files:
                logger.error(
                    f"Assigned base template {baseline_filename} not found... skipping sync(s). Please assign a valid file.")

                # Add missing file error to upload errors for display
                upload_errors[template_selection['orgName']] = [
                    f"Assigned base template {baseline_filename} not found... skipping sync(s). Please assign a valid file."]
                continue

            if exception_filename != 'None' and exception_filename not in config_files:
                logger.error(
                    f"Assigned exception template {exception_filename} not found... skipping sync(s). Please assign a valid file.")

                # Add missing file error to upload errors for display
                upload_errors[template_selection['netName']] = [
                    f"Assigned exception template {exception_filename} not found... skipping sync(s). Please assign a valid file."]
                continue

            # Sanity Check (None, None) -> there's no work to do
            if baseline_filename == "None" and exception_filename == "None":
                # Update Progress for display bar
                progress += progress_inc
                continue
            else:
                # Upload config to network, spawn a thread that calls the upload method for each network
                current_config = MerakiMXConfig(org_to_id[template_selection['orgName']]['id'],
                                                network_to_id[template_selection['netName']], logger)

                # Spawn a background thread
                thread = threading.Thread(target=thread_wrapper,
                                          args=(current_config, progress_inc, baseline_filename, exception_filename,))
                threads.append(thread)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to finish
        for t in threads:
            t.join()

        # If there's any remaining progress (clean division not possible, set to 100 for display)
        progress = 100

        # Support AJAX redirect to display success message and update display tables on screen
        return jsonify({'redirect_url': url_for('deploy_templates', success=True)})

    return render_template('deploy_templates.html', hiddenLinks=False, networks=network_displays,
                           config_files=config_files, success=success, upload_errors=upload_errors,
                           timeAndLocation=getSystemTimeAndLocation())


if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=False)
