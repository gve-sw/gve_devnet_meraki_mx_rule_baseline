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

import os
import sqlite3
from pprint import pprint
from sqlite3 import Error

# Absolute Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, 'sqlite.db')


def create_connection(db_file):
    """
    Connect to DB
    :param db_file: DB Object
    :return: DB connection object
    """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Error as e:
        print(e)

    return conn


def create_tables(conn):
    """
    Create initial tables (baseline template and exception template tables)
    :param conn: DB connection object
    """
    c = conn.cursor()

    c.execute("""
              CREATE TABLE IF NOT EXISTS base_templates
              ([org_id] TEXT PRIMARY KEY,
               [file_name] TEXT,
               UNIQUE (org_id))
              """)

    c.execute("""
              CREATE TABLE IF NOT EXISTS exception_templates
              ([net_id] TEXT PRIMARY KEY,
               [file_name] TEXT,
               UNIQUE (net_id))
              """)

    conn.commit()


def query_all_base_templates(conn):
    """
    Return table contents for Baseline Template table
    :param conn: DB connection object
    """
    c = conn.cursor()

    c.execute("""SELECT * FROM base_templates""")
    base_templates = c.fetchall()

    return base_templates


def query_all_exception_templates(conn):
    """
    Return table contents for Exception Template table
    :param conn: DB connection object
    """
    c = conn.cursor()

    c.execute("""SELECT * FROM exception_templates""")
    exception_templates = c.fetchall()

    return exception_templates


def query_template(conn, template_type, meraki_id):
    """
    Return a specific template from either the baseline templates or exception templates
    :param conn: DB connection object
    :param template_type: Determine the table to return a template from (baseline or exception)
    :param meraki_id: ID used to search for a template (org id for baseline table, network id for exception table)
    :return: Tuple containing template file name and id
    """
    c = conn.cursor()

    if template_type == "base":
        # Base template case
        table = "base_templates"

        select_statement = f"SELECT file_name FROM {table} WHERE org_id = ?"
        c.execute(select_statement, (meraki_id,))
    elif template_type == "exception":
        # Exception template case
        table = "exception_templates"

        select_statement = f"SELECT file_name FROM {table} WHERE net_id = ?"
        c.execute(select_statement, (meraki_id,))

    template_file_name = c.fetchall()

    if len(template_file_name) > 0:
        # If template filename found, return filename, otherwise if None, return "None"
        if template_file_name[0][0]:
            return template_file_name[0][0]
        else:
            return "None"
    else:
        return None


def add_template(conn, template_type, meraki_id, file_name):
    """
    Add baseline or exception template to respective table
    :param conn: DB connection object
    :param template_type: The type of template (controls table selection): base, exception
    :param meraki_id: ID used to search for a template (org id for baseline table, network id for exception table)
    :param file_name: New template file name
    """
    c = conn.cursor()

    if template_type == "base":
        # Base template case
        table = "base_templates"

        update_statement = f"INSERT OR IGNORE into {table} (org_id, file_name) VALUES (?,?)"
        c.execute(update_statement, (meraki_id, file_name))
    elif template_type == "exception":
        # Exception template case
        table = "exception_templates"

        update_statement = f"INSERT OR IGNORE into {table} (net_id, file_name) VALUES (?,?)"
        c.execute(update_statement, (meraki_id, file_name))

    conn.commit()


def update_template(conn, template_type, meraki_id, file_name):
    """
    Update baseline or exception template in respective table with a new file name
    :param conn: DB connection object
    :param template_type: The type of template (controls table selection): base, exception
    :param meraki_id: ID used to search for a template (org id for baseline table, network id for exception table)
    :param file_name: New template file name
    """
    c = conn.cursor()

    if template_type == "base":
        # Base template case
        table = "base_templates"

        update_statement = f"UPDATE {table} SET file_name = ? WHERE org_id = ?"
        c.execute(update_statement, (file_name, meraki_id))
    elif template_type == "exception":
        # Exception template case
        table = "exception_templates"
        update_statement = f"UPDATE {table} SET file_name = ? WHERE net_id = ?"
        c.execute(update_statement, (file_name, meraki_id))

    conn.commit()


def remove_template(conn, template_type, meraki_id):
    """
    "Soft Delete" template from baseline or exception table - Set to Null
    :param conn: DB connection object
    :param template_type: The type of template (controls table selection): base, exception
    :param meraki_id: ID used to search for a template (org id for baseline table, network id for exception table)
    """
    c = conn.cursor()

    if template_type == "base":
        # Base template case
        table = "base_templates"

        update_statement = f"UPDATE {table} SET file_name = NULL WHERE org_id = ?"
        c.execute(update_statement, (meraki_id,))
    elif template_type == "exception":
        # Exception template case
        table = "exception_templates"

        update_statement = f"UPDATE {table} SET file_name = NULL WHERE net_id = ?"
        c.execute(update_statement, (meraki_id,))

    conn.commit()


def delete_template(conn, template_type, meraki_id):
    """
    Hard Delete  template from baseline or exception table
    :param conn: DB connection object
    :param template_type: The type of template (controls table selection): base, exception
    :param meraki_id: ID used to search for a template (org id for baseline table, network id for exception table)
    """
    c = conn.cursor()

    if template_type == "base":
        # Base template case
        table = "base_templates"

        delete_statement = f"DELETE FROM {table} WHERE org_id = ?"
        c.execute(delete_statement, (meraki_id,))
    elif template_type == "exception":
        # Exception template case
        table = "exception_templates"

        delete_statement = f"DELETE FROM {table} WHERE net_id = ?"
        c.execute(delete_statement, (meraki_id,))

    conn.commit()


def close_connection(conn):
    """
    Close DB Connection
    :param conn: DB Connection
    """
    conn.close()


# If running this python file, create connection to database, create tables, and print out the results of queries of
# every table
if __name__ == "__main__":
    conn = create_connection(db_path)
    create_tables(conn)
    pprint(query_all_base_templates(conn))
    pprint(query_all_exception_templates(conn))
    close_connection(conn)
