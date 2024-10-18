import os

from gmail_parser import authenticate_gmail
from gmail_parser import list_messages
from gmail_parser import get_message
from gmail_parser import parse_transaction_details
from gmail_parser import extract_html_content
from gmail_parser import extract_email

def email_parser_main(since_datetime: str):
    """
    Main handler for email parser.
    
    Parameters:
        - since_datetime (str): YYYY-MM-DD HH:MM:SS.SSS date string that is read from the application database
        indicating the last time the user fetched their transaction emails using the Gmail parser tool.
    """
    since_date = since_datetime.split(' ')[0]
    service = authenticate_gmail(
        token_path=os.environ['OAUTH_TOKEN_PATH'],
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
            last_update_time=since_datetime
        )
        if transaction_data != {}:
            transactions.append(transaction_data)
    return transactions

# Keeping for debugging purposes
#transactions = email_parser_main(since_datetime='2024-10-01 06:00:00.000')
#print(transactions)
#print(len(transactions))