import os
import json
import base64
import re
import uuid
from bs4 import BeautifulSoup
import datetime
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import googleapiclient.discovery

from logging_config import logger
from utils import convert_unix_timestamp_to_date

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

def authenticate_gmail(token_path: str, credential_path: str) -> googleapiclient.discovery.Resource:
    """
    Authenticates the user and returns an instance of the Gmail API service.
    
    Parameters:
        - token_path (str): The full file path corresponding to where the JSON file containing
        the OAuth token and refresh token for authenticating to GMail is stored.
        - credential_path (str): The full file path corresponding to where the JSON file
        containing credentials to obtain an OAuth token is stored. 
    """
    creds = None

    # Load OAuth token from local file if it exists
    if os.path.exists(token_path):
        logger.info('OAuth token file found, reading credentials from file.')
        with open(token_path, 'r', encoding='UTF-8') as token_file:
            token_json = json.load(token_file)
            creds = Credentials.from_authorized_user_info(token_json, SCOPES)
    else:
        logger.info('OAuth token file not found.')

    # If credentials are invalid or expired, refresh or initiate new OAuth flow
    if creds is None or not creds.valid:
        logger.info('Credentials were either not found or expired.')
        if creds and creds.expired and creds.refresh_token:
            logger.info('Credentials expired, attempting to refresh token.')
            try:
                creds.refresh(Request())
                logger.info('Token refreshed successfully.')
            except RefreshError as e:
                logger.warning('Error refreshing token, initiating new OAuth flow: %s', e)
                creds = None
        else:
            logger.info('No valid OAuth token found, initiating new OAuth flow.')
            # Load OAuth credentials from local file
            if os.path.exists(credential_path):
                logger.info('OAuth credentials file found, running OAuth flow.')
                with open(credential_path, 'r', encoding='UTF-8') as creds_file:
                    oauth_credentials = json.load(creds_file)
                    flow = InstalledAppFlow.from_client_config(oauth_credentials, SCOPES)
                    creds = flow.run_local_server(port=8080)
            else:
                raise FileNotFoundError('OAuth credentials file not found.')

        # Save the token back to local storage after successful authentication
        if creds:
            with open(token_path, 'w', encoding='UTF-8') as token_file:
                token_file.write(creds.to_json())
            logger.info('Updated OAuth token stored locally.')

    # Build the Gmail service object
    service = googleapiclient.discovery.build('gmail', 'v1', credentials=creds)
    return service

def list_messages(service: googleapiclient.discovery.Resource, user_id: str, query: str) -> list:
    """
    Returns a lists the messages in the user's inbox matching the specified input query.

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

def get_message(service: googleapiclient.discovery.Resource, user_id: str, msg_id: str) -> dict:
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
                              email_content: str, last_update_time: str) -> dict:
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

    # Venmo
    if from_email == venmo_email:
        if 'paid you' in subject or 'You paid' in subject:
            logger.info('Parsing Venmo transaction.')
            transaction_found = True
            transaction_id = str(uuid.uuid4())
            transaction_date = convert_unix_timestamp_to_date(email_timestamp)
            transaction_bucket = 'Expense'
            transaction_amount = subject.split('$')[1]
            transaction_category = ''
            transaction_subcategory = ''
            transaction_account = 'Venmo'
            transaction_recurring = False
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
            transaction_id = str(uuid.uuid4())
            transaction_date = convert_unix_timestamp_to_date(email_timestamp)
            transaction_lines = email_content.split('\n')
            transaction_merchant = transaction_lines[9]
            transaction_bucket = 'Expense'
            transaction_amount = re.search(r'\n\$([0-9]+\.[0-9]{2})\*', email_content).group(1)
            transaction_category = ''
            transaction_subcategory = ''
            transaction_account = 'American Express ' +  re.search(r'Account Ending: (\d{5})', email_content).group(1)
            transaction_recurring = False
            logger.info('Successfully parsed American Express transaction.')
        else:
            logger.info('Non transaction American Express email detected.')
            logger.info('Email subject -- %s', subject)
    # Chase
    elif from_email == chase_email:
        if 'You sent' in subject:
            logger.info('Parsing Chase transfer.')
            transaction_found = True
            transaction_id = str(uuid.uuid4())
            transaction_date = convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'Recipient\n(.*?)\nAmount', email_content).group(1)
            transaction_bucket = ''
            transaction_amount = re.search(r'Amount\n\$(\d+\.\d{2})', email_content).group(1)
            transaction_category = ''
            transaction_subcategory = ''
            transaction_account = 'Chase ' + re.search(r'Account ending in\n\(\.\.\.(\d{4})\)\nSent on', email_content).group(1)
            transaction_recurring = False
            logger.info('Successfully parsed Chase transfer.')
        elif 'transaction with' in subject:
            logger.info('Parsing Chase credit card transaction.')
            transaction_found = True
            transaction_id = str(uuid.uuid4())
            transaction_date = convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'transaction with ([A-Za-z0-9\s\*\.\#\']+)', subject).group(1)
            transaction_bucket = ''
            transaction_amount = re.search(r'\$(\d+\.\d{2})', subject).group(1)
            transaction_category = ''
            transaction_subcategory = ''
            transaction_account = 'Chase ' + re.search(r'\(\.\.\.(\d+)\)', email_content).group(1)
            transaction_recurring = False
            logger.info('Successfully parsed Chase credit card transaction.')
        else:
            logger.info('Non transaction Chase email detected.')
            logger.info('Email subject -- %s', subject)
    # Capital One
    elif from_email == capitalone_email:
        if subject == 'A new transaction was charged to your account':
            logger.info('Parsing Capital One transaction.')
            transaction_found = True
            transaction_id = str(uuid.uuid4())
            transaction_date = convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'at (.*?)\, a pending authorization or purchase', email_content).group(1).split(' at ')[-1]
            transaction_bucket = ''
            transaction_amount = re.search(r'amount of \$(\d+\.\d{2})', email_content).group(1)
            transaction_category = ''
            transaction_subcategory = ''
            transaction_account = 'Capital One ' + re.search(r'ending in (\d{4})', email_content).group(1)
            transaction_recurring = False
        else:
            logger.info('Non transaction Capital One email detected.')
            logger.info('Email subject -- %s', subject)
    # Wells Fargo
    elif from_email == wells_fargo_email:
        if 'You made a credit card purchase of' in subject:
            logger.info('Parsing Wells Fargo transaction.')
            transaction_found = True
            transaction_id = str(uuid.uuid4())
            transaction_date = convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'Merchant detail\s*(.*?)\s*View Accounts', email_content, re.DOTALL).group(1).strip()
            transaction_bucket = ''
            transaction_amount = re.search(r'Amount\s*\$([0-9,]+\.\d{2})\s*Merchant detail', email_content).group(1)
            transaction_category = ''
            transaction_subcategory = ''
            transaction_account = 'Wells Fargo ' + re.search(r'Credit card\s*\.\.\.(\d+)\s*Amount', email_content).group(1)
            transaction_recurring = False
            logger.info('Successfully parsed Wells Fargo transaction.')
        else:
            logger.info('Non transaction Wells Fargo email detected.')
            logger.info('Email subject -- %s', subject)
    
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