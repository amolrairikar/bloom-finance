import os
import uuid
import re
import datetime
import base64
import json
import logging
import sys
from typing import Dict, Union, Tuple
from google.cloud import firestore

# Create a logger
logger = logging.getLogger('database_writer')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

class Transaction:
    """A class to represent a transaction."""
    def __init__(self, transaction_id: str, transaction_date: str, merchant: str,
                 bucket: str, amount: str, category: str, subcategory: str,
                 account_name: str, is_recurring: str) -> None:
        """Initializes the Transaction instance."""
        self.transaction_id = transaction_id
        self.transaction_date = transaction_date
        self.merchant = merchant
        self.bucket = bucket
        self.amount = amount
        self.category = category
        self.subcategory = subcategory
        self.account_name = account_name
        self.is_recurring = is_recurring

    def to_dict(self) -> Dict[str, str]:
        """Convert the transaction details to a dictionary."""
        return {
            'transaction_id': self.transaction_id,
            'transaction_date': self.transaction_date,
            'merchant': self.merchant,
            'bucket': self.bucket,
            'amount': self.amount,
            'category': self.category,
            'subcategory': self.subcategory,
            'account_name': self.account_name,
            'is_recurring': self.is_recurring
        }
    
class TransactionParser:
    """"A class containing methods to parse transaction details from
    email content returned by the GmailService class."""
    def __init__(self) -> None:
        "Initializes the TransactionParser instance."
        self.emails = {
            'venmo': get_env_variable('VENMO_EMAIL'),
            'amex': get_env_variable('AMEX_EMAIL'),
            'chase': get_env_variable('CHASE_EMAIL'),
            'capital_one': get_env_variable('CAPITALONE_EMAIL'),
            'wells_fargo': get_env_variable('WELLSFARGO_EMAIL'),
        }

    def parse_transaction_details(self, subject: str, from_email: str,
                                   email_timestamp: str, email_content: str) -> Union[Transaction, None]:
        """Parses a transaction email for details about the transaction."""
        transaction_found = False
        transaction = None
        try:
            if from_email == self.emails['venmo']:
                transaction_found, transaction = self.parse_venmo_transaction(subject, email_timestamp)
            elif from_email == self.emails['amex']:
                transaction_found, transaction = self.parse_amex_transaction(subject, email_timestamp, email_content)
            elif from_email == self.emails['chase']:
                transaction_found, transaction = self.parse_chase_transaction(subject, email_timestamp, email_content)
            elif from_email == self.emails['capital_one']:
                transaction_found, transaction = self.parse_capital_one_transaction(subject, email_timestamp, email_content)
            elif from_email == self.emails['wells_fargo']:
                transaction_found, transaction = self.parse_wells_fargo_transaction(subject, email_timestamp, email_content)
            if transaction_found:
                logger.info('Successfully parsed transaction: %s', transaction.to_dict())
            else:
                logger.info('No valid transaction found for email.')
            return transaction
        except Exception as e:
            logger.error('Error occurred while parsing transaction: %s', e)
            return None
        
    def parse_venmo_transaction(self, subject: str, email_timestamp: str) -> Tuple[bool, Transaction]:
        """Parses a transaction email from Venmo."""
        if 'paid you' in subject or 'You paid' in subject:
            logger.info('Parsing Venmo transaction.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_bucket = 'Expense'
            transaction_amount = subject.split('$')[1]
            transaction_account = 'Venmo'
            transaction_recurring = 'False'
            if 'You paid' in subject:
                transaction_merchant = re.search(r'You paid (.+?) \$\d+\.\d{2}', subject).group(1)
            else:
                transaction_merchant = subject.split(' paid you')[0]
                transaction_amount = '-' + transaction_amount
            logger.info('Parsed Venmo transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, transaction_bucket,
                                      transaction_amount, '', '', transaction_account, transaction_recurring)
        logger.info('Non-transaction Venmo email detected.')
        return False, None

    def parse_amex_transaction(self, subject: str, email_timestamp: str, email_content: str) -> Tuple[bool, Transaction]:
        """Parses a transaction email from American Express."""
        if subject == 'Large Purchase Approved':
            logger.info('Parsing American Express transaction.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_lines = email_content.split('\n')
            transaction_merchant = transaction_lines[9]
            transaction_bucket = 'Expense'
            transaction_amount = re.search(r'\n\$([0-9]+\.[0-9]{2})\*', email_content).group(1)
            transaction_account = 'American Express ' + re.search(r'Account Ending: (\d{5})', email_content).group(1)
            transaction_recurring = 'False'
            logger.info('Parsed American Express transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, transaction_bucket,
                                      transaction_amount, '', '', transaction_account, transaction_recurring)
        logger.info('Non-transaction American Express email detected.')
        return False, None

    def parse_chase_transaction(self, subject: str, email_timestamp: str, email_content: str) -> Tuple[bool, Transaction]:
        """Parses a transaction email from Chase."""
        if 'You sent' in subject:
            logger.info('Parsing Chase transfer.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'Recipient\n(.*?)\nAmount', email_content).group(1)
            transaction_amount = re.search(r'Amount\n\$(\d+\.\d{2})', email_content).group(1)
            transaction_account = 'Chase ' + re.search(r'Account ending in\n\(\.\.\.(\d{4})\)\nSent on', email_content).group(1)
            transaction_recurring = 'False'
            logger.info('Parsed Chase transfer transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, '',
                                    transaction_amount, '', '', transaction_account, transaction_recurring)

        elif 'transaction with' in subject:
            logger.info('Parsing Chase credit card transaction.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'transaction with ([A-Za-z0-9\s\*\.\#\']+)', subject).group(1)
            transaction_amount = re.search(r'\$(\d+\.\d{2})', subject).group(1)
            transaction_account = 'Chase ' + re.search(r'\(\.\.\.(\d+)\)', email_content).group(1)
            transaction_recurring = 'False'
            logger.info('Parsed Chase credit card transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, '',
                                    transaction_amount, '', '', transaction_account, transaction_recurring)

        elif 'direct deposit' in subject:
            logger.info('Parsing Chase direct deposit transaction.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = os.environ['EMPLOYER']
            transaction_amount = re.search(r'\$([\d,]+\.\d{2})', subject).group(1).replace(',', '')
            transaction_account = 'Chase ' + re.search(r'\((\.\.\.\d{4})\)', subject).group(1)[-4:]
            transaction_recurring = 'False'
            logger.info('Parsed Chase direct deposit transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, 'Income',
                                    transaction_amount, 'Paychecks', '', transaction_account, transaction_recurring)

        logger.info('Non-transaction Chase email detected.')
        return False, None


    def parse_capital_one_transaction(self, subject: str, email_timestamp: str, email_content: str) -> Tuple[bool, Transaction]:
        """Parses a transaction email from Chase."""
        if subject == 'A new transaction was charged to your account':
            logger.info('Parsing Capital One transaction.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'at (.*?)\, a pending authorization or purchase', email_content).group(1).split(' at ')[-1]
            transaction_amount = re.search(r'amount of \$(\d+\.\d{2})', email_content).group(1)
            transaction_account = 'Capital One ' + re.search(r'ending in (\d{4})', email_content).group(1)
            transaction_recurring = 'False'
            logger.info('Parsed Capital One transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, '',
                                      transaction_amount, '', '', transaction_account, transaction_recurring)

        logger.info('Non-transaction Capital One email detected.')
        return False, None

    def parse_wells_fargo_transaction(self, subject: str, email_timestamp: str, email_content: str) -> Tuple[bool, Transaction]:
        """Parses a transaction email from Wells Fargo."""
        if 'You made a credit card purchase of' in subject:
            logger.info('Parsing Wells Fargo transaction.')
            transaction_id = self.generate_uuid()
            transaction_date = self.convert_unix_timestamp_to_date(email_timestamp)
            transaction_merchant = re.search(r'Merchant detail\s*(.*?)\s*View Accounts', email_content, re.DOTALL).group(1).strip()
            transaction_amount = re.search(r'Amount\s*\$([0-9,]+\.\d{2})\s*Merchant detail', email_content).group(1)
            transaction_account = 'Wells Fargo ' + re.search(r'Credit card\s*\.\.\.(\d+)\s*Amount', email_content).group(1)
            transaction_recurring = 'False'
            logger.info('Parsed Wells Fargo transaction.')
            return True, Transaction(transaction_id, transaction_date, transaction_merchant, '',
                                      transaction_amount, '', '', transaction_account, transaction_recurring)

        logger.info('Non-transaction Wells Fargo email detected.')
        return False, None
    
    def generate_uuid(self) -> str:
        """Generate a unique transaction ID."""
        return str(uuid.uuid4())
    
    def convert_unix_timestamp_to_date(self, unix_timestamp: str) -> str:
        """Convert a Unix timestamp to a readable date format."""
        return datetime.datetime.fromtimestamp(int(unix_timestamp) / 1000).strftime('%Y-%m-%d')
    
def get_env_variable(var_name: str) -> str:
    """Fetches an environment variable and raises an error if not found."""
    value = os.environ.get(var_name)
    if value is None:
        raise ValueError(f'Missing environment variable: {var_name}')
    return value

def write_transactions_to_database(transaction_data, project_id: str=None) -> None:
    """Writes transaction data to Cloud Firestore."""
    if project_id is None:
        db = firestore.Client()
    else:
        db = firestore.Client(project=project_id)
    transactions_ref = db.collection('transactions')
    transaction_id = transaction_data.pop('transaction_id', None)
    if transaction_id:
        transactions_ref.document(transaction_id).set(transaction_data)
    else:
        raise ValueError('Transaction cannot be added without a transaction_id.')

def process_pubsub_trigger(request) -> Tuple[str, int]:
    """Main Cloud Function handler, triggered by Pub/Sub."""
    # Decode the Pub/Sub message
    pubsub_message = request.get_json(silent=True)
    if not pubsub_message:
        return 'No Pub/Sub message received', 400
    try:
        message_data = base64.b64decode(pubsub_message['message']['data']).decode('utf-8')
        transaction_data = json.loads(message_data)
    except Exception as e:
        return f'Error decoding message data: {e}', 400

    # Write the transaction data to Firestore
    write_transactions_to_database(transaction_data)
    return 'Transaction data written to Firestore', 200