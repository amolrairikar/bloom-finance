import sqlite3
import os
import datetime

from logging_config import logger

DATABASE_URL = 'database.db'

def init_db():
    """
    Initialization script to create all required application tables if SQLite database
    file does not exist.
    """
    if not os.path.exists(DATABASE_URL):
        logger.info('%s not found, creating a new database.', DATABASE_URL)
        conn = sqlite3.connect(DATABASE_URL)
        cursor = conn.cursor()

        # Create tables
        cursor.executescript(
            '''
            CREATE TABLE IF NOT EXISTS user_data (
                name TEXT PRIMARY KEY,
                last_transaction_refresh TEXT,
                access_token TEXT,
                refresh_token TEXT,
                client_id TEXT,
                client_secret TEXT,
                token_uri TEXT
            );

            CREATE TABLE IF NOT EXISTS categories (
                category_name TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS subcategories (
                subcategory_name TEXT PRIMARY KEY,
                category_name REFERENCES categories (category_name)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                transaction_id TEXT PRIMARY KEY,
                transaction_date TEXT,
                merchant TEXT,
                bucket TEXT,
                amount TEXT,
                category REFERENCES categories (category_name),
                subcategory REFERENCES subcategory_name (subcategories),
                account_name TEXT,
                is_recurring TEXT
            );

            CREATE TABLE IF NOT EXISTS transaction_rules (
                rule_id TEXT PRIMARY KEY,
                merchant_original_name TEXT,
                merchant_renamed_name TEXT,
                rule_created_date TEXT
            );

            '''
        )

        # Insert values into user_data table
        current_datetime = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
        cursor.execute(
            '''
            INSERT INTO user_data (name, last_transaction_refresh)
            VALUES (?, ?);
            '''
            ,
            #(os.environ['NAME'], current_datetime)
            (os.environ['NAME'], '2024-10-01 01:00:00.000')
        )

        conn.commit()
        conn.close()
    else:
        logger.info('%s already exists', DATABASE_URL)

def get_db_connection():
    "Establish SQLite connection."
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()
