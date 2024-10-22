import datetime
import os
import sqlite3
import base64
import json
import re
import uuid
from bs4 import BeautifulSoup
from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional, Union, Tuple
from google.oauth2.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_oauthlib.flow import Flow
import googleapiclient.discovery

from models import Transaction
from logging_config import logger

# Set scope for Gmail API access
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def extract_html_content(html_body: str) -> str:
    """
    Parses HTML content from HTML string.
    
    Parameters:
        - html_body (str): A string containing raw HTML content.
    """
    soup = BeautifulSoup(html_body, 'html.parser')

    # Remove script, style, and meta tags
    for tag in soup(['script', 'style', 'meta']):
        tag.decompose()

    # Extract the text content, keeping only visible text
    text_content = soup.get_text(separator='\n', strip=True)
    return text_content

def extract_email(email_string: str) -> str:
    """
    Extracts the email address from a string in the format 'Venmo <venmo@venmo.com>'.

    Parameters:
        - email_string (str): The string containing the email + sender that needs to be parsed.
    """
    return email_string[email_string.find('<')+1:email_string.find('>')]

def is_email_inscope(email_timestamp: str, cutoff_datetime: str) -> bool:
    """
    Returns a boolean True/False indicating if the email has already been seen (False) or is new (True).
    
    Parameters:
        - email_timestamp (str): The Unix timestamp corresponding to when the email was received.
        - cutoff_datetime (str): A datetime string in YYYY-MM-DD HH:MM:SS.SSS format representing the
        datetime where an email arriving before that datetime should not be parsed.
    """
    cutoff_datetime = datetime.datetime.strptime(
        cutoff_datetime, '%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=datetime.timezone.utc)
    unix_datetime = datetime.datetime.fromtimestamp(int(email_timestamp)/1000, tz=datetime.timezone.utc)
    return unix_datetime > cutoff_datetime

def save_tokens(db: sqlite3.Connection, table_name: str, access_token: str, refresh_token: str,
                client_id: str, client_secret: str, token_uri: str):
    """
    Saves access and refresh tokens to the database.

    Parameters:
        - db (sqlite3.Connection): Represents a SQLite database connection.
        - table_name (str): Database table containing credentials data
        - access_token (str): Access token for Gmail API
        - refresh_token (str): Refresh token used to refresh the access token for Gmail API.
        - client_id (str): Client ID corresponding to access token credentials.
        - client_secret (str): Client secret corresponding to access token credentials.
        - token_uri (str): Token URI corresponding to access token credentials.
    """
    name = os.environ['NAME']
    db.execute(
        f'''
        UPDATE {table_name}
        SET access_token=?, refresh_token=?, client_id=?, client_secret=?, token_uri=?
        WHERE name=?;
        ''',
        (access_token, refresh_token, client_id, client_secret, token_uri, name)
    )

def load_credentials(db: sqlite3.Connection, table_name: str):
    """
    Loads access and refresh tokens from the database as a Google credentials object.

    Parameters:
        - db (sqlite3.Connection): Represents a SQLite database connection.
        - table_name (str): Database table containing credentials data
    """
    name = os.environ['NAME']
    cursor = db.cursor()

    cursor.execute(f'SELECT access_token, refresh_token, client_id, client_secret, token_uri FROM {table_name} WHERE name = ?', (name,))
    row = cursor.fetchone()

    if row:
        access_token = row['access_token']
        refresh_token = row['refresh_token']
        client_id = row['client_id']
        client_secret = row['client_secret']
        token_uri = row['token_uri']
        return Credentials(token=access_token, refresh_token=refresh_token,
                           client_id=client_id, client_secret=client_secret,
                           token_uri=token_uri, scopes=SCOPES)

    return None

def authenticate_gmail(db: sqlite3.Connection, table_name: str, credential_path: str) -> googleapiclient.discovery.Resource:
    """
    Authenticates the user and returns an instance of the Gmail API service.
    
    Parameters:
        - table_name (str): Database table containing credentials data.
        - credential_path (str): The full file path corresponding to where the JSON file
        containing credentials to obtain an OAuth token is stored. 
    """
    creds = None
    current_directory = os.getcwd()

    # Load OAuth token from database if it exists
    creds = load_credentials(db, table_name)

    # If credentials are invalid or expired, refresh or initiate new OAuth flow
    if creds is None or not creds.valid:
        logger.info('Credentials were either not found or expired.')
        if creds and creds.expired and creds.refresh_token:
            logger.info('Credentials expired, attempting to refresh token.')
            try:
                creds.refresh(GoogleAuthRequest())
                save_tokens(db=db, table_name='user_data',
                            access_token=creds.token, refresh_token=creds.refresh_token,
                            client_id=creds.client_id, client_secret=creds.client_secret,
                            token_uri=creds.token_uri)
                logger.info('Token refreshed successfully.')
            except RefreshError as e:
                logger.warning('Error refreshing token, initiating new OAuth flow: %s', e)
                creds = None
        else:
            logger.info('No valid OAuth token found, initiating new OAuth flow.')
            # Load OAuth credentials from local file
            if os.path.exists(os.path.join(current_directory, credential_path)):
                logger.info('OAuth credentials file found, running OAuth flow.')
                with open(credential_path, 'r', encoding='UTF-8') as creds_file:
                    oauth_credentials = json.load(creds_file)
                    flow = Flow.from_client_config(oauth_credentials, SCOPES)
                    flow.run_local_server(port=8080)
                    creds = flow.credentials
                    save_tokens(db, table_name, creds.token, creds.refresh_token)
                    logger.info('Saved OAuth credentials to database.')
            else:
                raise FileNotFoundError('OAuth credentials file not found.')

    # Build the Gmail service object
    service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)
    return service

def list_messages(service: googleapiclient.discovery.Resource, user_id: str, query: str) -> List[Dict[str, str]]:
    """
    Returns a list of the messages in the user's inbox matching the specified input query.

    Parameters:
        - service (googleapiclient.discovery.Resource): GMail service instance that allows
        our code to interact with emails based on the specified scopes listed in SCOPES.
        - user_id (str): The GMail address for the inbox containing emails we want to parse.
        - query (str): A query string that allows you to specify which email should be read.
        An example query is below:
            query = (
                f'(from:alice@example.com OR from:bob@example.com) '
                f'AND is:unread '
                f'AND after:{after_date}'
            )
    """
    try:
        response = service.users().messages().list(userId=user_id, q=query).execute()
        messages = response.get('messages', [])
        
        if not messages:
            logger.info('No messages found.')
            return messages
        else:
            logger.info('Found %i messages.', len(messages))
            return messages
    except Exception as error:
        logger.error('An error occurred while listing messages: %s', error)
        messages = []
        return messages

def get_message(service: googleapiclient.discovery.Resource, user_id: str, msg_id: str) -> Dict[str, str]:
    """
    Retrieves details about a specific message based on the message ID from the user's mailbox.

    Parameters:
        - service (googleapiclient.discovery.Resource): GMail service instance that allows
        our code to interact with emails based on the specified scopes listed in SCOPES.
        - user_id (str): The GMail address for the inbox containing emails we want to parse.
        - msg_id (str): The unique ID corresponding to a specific message. This ID can be
        found by calling the list_messages() method, and then passing in message['id'] for
        each message in the response of list_messages()
    """
    try:
        message_content = {}
        message = service.users().messages().get(userId=user_id, id=msg_id, format='full').execute()

        # Extract subject and sender
        headers = message['payload']['headers']
        for header in headers:
            if header['name'] == 'Subject':
                message_content['subject'] = header['value']
                logger.info('Successfully retrieved email subject.')
            if header['name'] == 'From':
                message_content['from'] = header['value']
                logger.info('Successfully retrieved email sender.')

        # Extract email timestamp
        internal_date = message.get('internalDate')
        message_content['internalDate'] = internal_date

        # Extract message content
        parts = message['payload'].get('parts', [])
        if not parts:
            logger.info('Single part message detected.')
            body_data = message['payload']['body']['data']
            logger.info('Successfully retrieved single part message.')
        else:
            logger.info('Multipart body detected.')
            for part in parts:
                if part['mimeType'] == 'text/html':
                    body_data = part['body']['data']
                    break
                # For Wells Fargo emails
                elif part['mimeType'] == 'multipart/related':
                    body_data = part['parts'][0]['body']['data']
                    break
            logger.info('Successfully retrieved multipart message.')

        message_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
        logger.info('Successfully decoded message.')
        message_content['body'] = message_body
        return message_content
    except Exception as error:
        logger.error('An error occurred while retrieving the message: %s', error)
        return message_content

def parse_transaction_details(subject: str, from_email: str, email_timestamp: str,
                              email_content: str, last_update_time: str) -> Union[Dict[str, str], Dict]:
    """
    Parses an email for details about the transaction such as merchant, date,
    amount, and account.
    
    Parameters:
        - subject (str): The subject line of the email.
        - from_email (str): The sender of the email.
        - email_timestamp (str): A Unix timestamp corresponding to when the email was sent.
        - email_content (str): A text string containing the HTML content from the email.
        - last_update_time (str): YYYY-MM-DD HH:MM:SS.SSS date string that was read from the
        application database indicating the last time the user fetched their transaction emails
        using the Gmail parser tool. Only emails with a timestamp after the last_update_time
        are parsed. This field is needed because GMail only allows passing in a date in the
        query used in list_messages()
    """

    # Fetch email addresses from environment variables
    venmo_email = os.getenv('VENMO_EMAIL')
    amex_email = os.getenv('AMEX_EMAIL')
    chase_email = os.getenv('CHASE_EMAIL')
    capitalone_email = os.getenv('CAPITALONE_EMAIL')
    wells_fargo_email = os.getenv('WELLSFARGO_EMAIL')

    # Check if email was received after last data refresh
    if is_email_inscope(email_timestamp=email_timestamp, cutoff_datetime=last_update_time):
        logger.info('Email was received after last data refresh.')
    else:
        logger.info('Email was part of last data refresh, skipping to next email.')
        transaction = {}
        return transaction
    
    transaction_found = False

    try:
        # Venmo
        if from_email == venmo_email:
            if 'paid you' in subject or 'You paid' in subject:
                logger.info('Parsing Venmo transaction.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_bucket = 'Expense'
                transaction_amount = subject.split('$')[1]
                transaction_category = ''
                transaction_subcategory = ''
                transaction_account = 'Venmo'
                transaction_recurring = 'False'
                if 'You paid' in subject:
                    transaction_merchant = re.search(r'You paid (.+?) \$\d+\.\d{2}', subject).group(1)
                else:
                    transaction_merchant = subject.split(' paid you')[0]
                    transaction_amount = '-' + transaction_amount
                logger.info('Successfully parsed Venmo transaction.')
            else:
                logger.info('Non transaction Venmo email detected.')
                logger.info('Email subject -- %s', subject)
        # American Express
        elif from_email == amex_email:
            if subject == 'Large Purchase Approved':
                logger.info('Parsing American Express transaction.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_lines = email_content.split('\n')
                transaction_merchant = transaction_lines[9]
                transaction_bucket = 'Expense'
                transaction_amount = re.search(r'\n\$([0-9]+\.[0-9]{2})\*', email_content).group(1)
                transaction_category = ''
                transaction_subcategory = ''
                transaction_account = 'American Express ' +  re.search(r'Account Ending: (\d{5})', email_content).group(1)
                transaction_recurring = 'False'
                logger.info('Successfully parsed American Express transaction.')
            else:
                logger.info('Non transaction American Express email detected.')
                logger.info('Email subject -- %s', subject)
        # Chase
        elif from_email == chase_email:
            if 'You sent' in subject:
                logger.info('Parsing Chase transfer.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_merchant = re.search(r'Recipient\n(.*?)\nAmount', email_content).group(1)
                transaction_bucket = ''
                transaction_amount = re.search(r'Amount\n\$(\d+\.\d{2})', email_content).group(1)
                transaction_category = ''
                transaction_subcategory = ''
                transaction_account = 'Chase ' + re.search(r'Account ending in\n\(\.\.\.(\d{4})\)\nSent on', email_content).group(1)
                transaction_recurring = 'False'
                logger.info('Successfully parsed Chase transfer.')
            elif 'transaction with' in subject:
                logger.info('Parsing Chase credit card transaction.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_merchant = re.search(r'transaction with ([A-Za-z0-9\s\*\.\#\']+)', subject).group(1)
                transaction_bucket = ''
                transaction_amount = re.search(r'\$(\d+\.\d{2})', subject).group(1)
                transaction_category = ''
                transaction_subcategory = ''
                transaction_account = 'Chase ' + re.search(r'\(\.\.\.(\d+)\)', email_content).group(1)
                transaction_recurring = 'False'
                logger.info('Successfully parsed Chase credit card transaction.')
            elif 'direct deposit' in subject:
                logger.info('Parsing Chase direct deposit transaction.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_merchant = os.environ['EMPLOYER']
                transaction_bucket = 'Income'
                transaction_amount = re.search(r'\$([\d,]+\.\d{2})', subject).group(1).replace(',', '')
                transaction_category = 'Paychecks'
                transaction_subcategory = ''
                transaction_account = 'Chase ' + re.search(r'\((\.\.\.\d{4})\)', subject).group(1)[-4:]
                transaction_recurring = 'False'
                logger.info('Successfully parsed Chase direct deposit transaction.')
            else:
                logger.info('Non transaction Chase email detected.')
                logger.info('Email subject -- %s', subject)
        # Capital One
        elif from_email == capitalone_email:
            if subject == 'A new transaction was charged to your account':
                logger.info('Parsing Capital One transaction.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_merchant = re.search(r'at (.*?)\, a pending authorization or purchase', email_content).group(1).split(' at ')[-1]
                transaction_bucket = ''
                transaction_amount = re.search(r'amount of \$(\d+\.\d{2})', email_content).group(1)
                transaction_category = ''
                transaction_subcategory = ''
                transaction_account = 'Capital One ' + re.search(r'ending in (\d{4})', email_content).group(1)
                transaction_recurring = 'False'
            else:
                logger.info('Non transaction Capital One email detected.')
                logger.info('Email subject -- %s', subject)
        # Wells Fargo
        elif from_email == wells_fargo_email:
            if 'You made a credit card purchase of' in subject:
                logger.info('Parsing Wells Fargo transaction.')
                transaction_found = True
                transaction_id = generate_uuid()
                transaction_date = convert_unix_timestamp_to_date(email_timestamp)
                transaction_merchant = re.search(r'Merchant detail\s*(.*?)\s*View Accounts', email_content, re.DOTALL).group(1).strip()
                transaction_bucket = ''
                transaction_amount = re.search(r'Amount\s*\$([0-9,]+\.\d{2})\s*Merchant detail', email_content).group(1)
                transaction_category = ''
                transaction_subcategory = ''
                transaction_account = 'Wells Fargo ' + re.search(r'Credit card\s*\.\.\.(\d+)\s*Amount', email_content).group(1)
                transaction_recurring = 'False'
                logger.info('Successfully parsed Wells Fargo transaction.')
            else:
                logger.info('Non transaction Wells Fargo email detected.')
                logger.info('Email subject -- %s', subject)
    except Exception as e:
        logger.error('Error occurred while parsing transaction: %s', e)
        logger.info('Raw email content: %s', email_content)
    
    if transaction_found:
        transaction = {
            'transaction_id': transaction_id,
            'transaction_date': transaction_date,
            'merchant': transaction_merchant,
            'bucket': transaction_bucket,
            'amount': transaction_amount,
            'category': transaction_category,
            'subcategory': transaction_subcategory,
            'account_name': transaction_account,
            'is_recurring': transaction_recurring
        }
    else:
        transaction = {}
    return transaction

def email_parser_main(db: sqlite3.Connection, last_refresh_datetime: str) -> List[Dict[str, str]]:
    """
    Main handler for email parser.
    
    Parameters:
        - db (sqlite3.Connection): Represents a SQLite database connection.
        - last_refresh_datetime (str): YYYY-MM-DD HH:MM:SS.SSS date string that is read from the application
        database indicating the last time the user fetched their transaction emails using the Gmail parser tool.
    """
    since_date = last_refresh_datetime.split(' ')[0]
    service = authenticate_gmail(
        db=db,
        table_name='user_data',
        credential_path=os.environ['OAUTH_CREDENTIALS_PATH']
    )
    query = (
        f'(from:{os.getenv("VENMO_EMAIL")} OR '
        f'from:{os.getenv("AMEX_EMAIL")} OR '
        f'from:{os.getenv("CHASE_EMAIL")} OR '
        f'from:{os.getenv("CAPITALONE_EMAIL")} OR '
        f'from:{os.getenv("WELLSFARGO_EMAIL")}) '
        f'AND after:{since_date}'
    )
    messages = list_messages(
        service=service,
        user_id=os.environ['EMAIL_ADDRESS'],
        query=query
    )
    transactions = []
    for message in messages:
        msg_id = message['id']
        message_content = get_message(
            service=service,
            user_id=os.environ['EMAIL_ADDRESS'],
            msg_id=msg_id
        )
        email = extract_email(message_content['from'])
        email_html = extract_html_content(message_content['body'])
        transaction_data = parse_transaction_details(
            subject=message_content['subject'],
            from_email=email,
            email_timestamp=message_content['internalDate'],
            email_content=email_html,
            last_update_time=last_refresh_datetime
        )
        if transaction_data != {}:
            transactions.append(transaction_data)
    return transactions

def convert_unix_timestamp_to_date(unix_timestamp: str) -> str:
    """
    Converts a unix timestamp to a date string in YYYY-MM-DD format.

    Parameters:
        - unix_timestamp (str): A Unix timestamp.    
    """
    return datetime.datetime.fromtimestamp(int(unix_timestamp) / 1000).strftime('%Y-%m-%d')

def create_transaction(db: sqlite3.Connection, transaction: Transaction) -> Optional[HTTPException]:
    """
    Writes a transaction to the transactions table in the SQLite database.
    
    Parameters:
        - db (sqlite3.Connection): Represents a SQLite database connection.
        - transaction (Transaction): Defined in models.py, representation of the schema
        for the transactions table in the application database.
    """
    try:
        with db:
            db.execute(
                '''
                INSERT INTO transactions (transaction_id, transaction_date, merchant, bucket,
                amount, category, subcategory, account_name, is_recurring)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                ''',
                (
                    transaction.transaction_id, transaction.transaction_date, transaction.merchant,
                    transaction.bucket, transaction.amount, transaction.category, transaction.subcategory,
                    transaction.account_name, transaction.is_recurring
                )
            )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f'An error occurred while creating transaction. Error code: {e}'
        )
    
def apply_rules_engine(merchant: str, rules: List[Dict[str, str]]) -> str:
    """
    Applies rules from the transaction_rules table to modify the merchant name.

    Parameters:
    - merchant (str): The original merchant name.
    - rules (List[Dict[str, str]]): List of rules from the rules table.
    """
    # Iterate through each rule
    for rule in rules:
        pattern = rule['pattern']
        replacement = rule['replacement']
        
        # If the pattern matches in the merchant string, replace it
        if pattern in merchant:
            merchant = merchant.replace(pattern, replacement)
    
    return merchant

def backfill_transaction_rules(db: sqlite3.Connection) -> int:
    """
    Backfills transaction merchant naming rules for existing transactions.
    
    Parameters:
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    rules = db.execute('SELECT pattern, replacement FROM rules').fetchall()
    transactions = db.execute('SELECT transaction_id, merchant FROM transactions').fetchall()

    updated_count = 0
    for transaction in transactions:
        transaction_id = transaction['transaction_id']
        merchant = transaction['merchant']

        # Apply rules engine to modify the merchant name
        new_merchant = apply_rules_engine(merchant, rules)
        if new_merchant != merchant:
            db.execute(
                '''
                    UPDATE transactions
                    SET merchant = ?
                    WHERE transaction_id = ?;
                ''',
                (new_merchant, transaction_id)
            )
            updated_count += 1

    return updated_count
    
def validate_date_format(date_string: str) -> Optional[HTTPException]:
    """
    Validates that a date string is in the format YYYY-MM-DD.
    
    Parameters:
        - date_string (str): Input date string that will be converted into YYYY-MM-DD format.
    """
    try:
        datetime.strptime(date_string, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail=f'Invalid date format: {date_string}. Expected format: YYYY-MM-DD.')
    
def add_get_condition(query: str, condition: str, params: List, new_param: str) -> str:
    """
    Utility function to add a condition to a get query and update parameters list for GET API requests.
    
    Parameters:
        - query (str): Base SQL query.
        - condition (str): Additional WHERE filter to narrow the results.
        - params (List): List of parameters to pass into the query. The query is structured as
        SELECT * FROM transactions WHERE 1=1 AND field_name = ? to avoid SQL injection. Each parameter
        in the params list is substituted for one of the question marks.
        - new_param (str): The new parameter to add to the params list for the given condition.
    """
    query += condition
    params.append(new_param)
    return query

def execute_get_query(query: str, db: sqlite3.Connection, model_type: BaseModel, params=[]):
    """
    Utility function to create a reusable execution framework for a GET API request.

    Parameters:
        - query (str): SQL query to execute.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    if params != []:
        cursor = db.execute(query, params)
    else:
        cursor = db.execute(query)
    rows = cursor.fetchall()
    data = [model_type(**dict(row)) for row in rows]
    return data

def create_patch_query(table_name: str, primary_key: str, fields: dict) -> Tuple[str, List]:
    """
    Utility function to prepare a patch query and update parameters list for PATCH API requests.
    
    Parameters:
        - table_name (str): The name of the table to update.
        - primary_key (str): The value for the primary key indicating the row to update.
        - fields (dict): A dictionary of fields to update with their new values.
    """
    query = f'UPDATE {table_name} SET'
    params = []
    
    # Iterate through fields and build the query and params list
    for field, value in fields.items():
        if value is not None:
            query += f' {field} = ?,'
            params.append(value)

    # Remove the trailing comma and add the WHERE clause placeholder
    query = query.rstrip(',') + f' WHERE {primary_key} = ?'
    return query, params

def execute_patch_query() -> None:
    # TODO: add reusable template to execute PATCH query
    pass

def execute_post_query() -> None:
    # TODO: add reusable template to execute POST query
    pass

def execute_delete_query() -> None:
    # TODO: add reusable template to execute DELETE query
    pass

def get_current_datetime_string() -> str:
    "Returns the current datetime as a string in '%Y-%m-%d %H:%M:%S.%f' format."
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')

def generate_uuid() -> str:
    "Returns a string UUID4."
    return str(uuid.uuid4())