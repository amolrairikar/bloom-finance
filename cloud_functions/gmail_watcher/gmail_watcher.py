import os
import json
import base64
import datetime
import logging
import sys
from bs4 import BeautifulSoup
from typing import Optional, List, Dict
from google.cloud import secretmanager
from google.cloud import pubsub_v1
from google.cloud import firestore
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import googleapiclient.discovery

# Create a logger
logger = logging.getLogger('gmail_watcher')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

class GCPSecretManager:
    "A class for interacting with OAuth token secrets in GCP Secret Manager."
    def __init__(self, project_id: str) -> None:
        """Initializes the GCPSecretManager instance."""
        self.project_id = project_id
        self.client = secretmanager.SecretManagerServiceClient()

    def get_secret(self, secret_id: str) -> Optional[str]:
        """Gets a secret from GCP Secret Manager."""
        try:
            secret_name = f'projects/{self.project_id}/secrets/{secret_id}/versions/latest'
            logger.info(f'Retrieving secret at path: {secret_name}')
            response = self.client.access_secret_version(name=secret_name)
            return response.payload.data.decode('UTF-8')
        except Exception as e:
            logger.error('Exception: %s', str(e))
            return None

    def store_secret(self, secret_id: str, secret_value: str) -> Optional[str]:
        """Stores a secret to GCP Secret Manager and disables all past versions."""
        try:
            parent = f'projects/{self.project_id}/secrets/{secret_id}'
            response = self.client.add_secret_version(
                parent=parent,
                payload={'data': secret_value.encode('UTF-8')}
            )
            versions = self.client.list_secret_versions(parent=parent)
            # Disable all previous versions except for the latest one
            for version in versions:
                if version.name != response.name:
                    self.client.disable_secret_version(name=version.name)
            return response.name
        except Exception as e:
            logger.error('Exception: %s', str(e))
            return None


    def create_secret(self, secret_id: str) -> Optional[str]:
        """Creates a new secret in GCP Secret Manager."""
        try:
            parent = f'projects/{self.project_id}'
            secret = {
                'replication': {
                    'automatic': {}
                }
            }
            response = self.client.create_secret(
                parent=parent,
                secret_id=secret_id,
                secret=secret
            )
            return response.name
        except Exception as e:
            logger.error('Exception: %s', str(e))
            return None
        
    def generate_oauth_credentials(self, secret_id: str) -> Optional[Credentials]:
        """Creates a Credentials object from an OAuth token json retrieved
        from GCP Secret Manager and refreshes the token if it's expired."""
        try:
            logger.info(f'Retrieving secret {secret_id}')
            oauth_json_creds = self.get_secret(secret_id)
            logger.info(f'Successfully retrieved secret {secret_id}')
            oauth_creds = json.loads(oauth_json_creds)
            credentials = Credentials(
                token=oauth_creds['token'],
                refresh_token=oauth_creds['refresh_token'],
                client_id=oauth_creds['client_id'],
                client_secret=oauth_creds['client_secret'],
                token_uri=oauth_creds['token_uri'],
                scopes=oauth_creds.get('scopes', [])
            )

            if credentials.expired:
                logger.warning('OAuth token expired, refreshing...')
                credentials.refresh(Request())
                new_oauth_creds = {
                    'token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                    'client_id': oauth_creds['client_id'],
                    'client_secret': oauth_creds['client_secret'],
                    'token_uri': oauth_creds['token_uri'],
                    'scopes': oauth_creds.get('scopes', [])
                }
                self.update_secret(secret_id, json.dumps(new_oauth_creds))
                logger.info('OAuth token refreshed and updated in Secret Manager.')
                return credentials
            return credentials
        except Exception as e:
            logger.error('Exception: %s', str(e))
            return None
    
class GmailService:
    """A class for interacting with the Gmail API."""
    def __init__(self, user_id: str, credentials: Credentials) -> None:
        """Initializes the GmailService instance."""
        self.user_id = user_id
        self.credentials = credentials
        self.service = self.build_gmail_service(credentials=credentials)

    def build_gmail_service(self, credentials: Credentials) -> googleapiclient.discovery.Resource:
        """Builds a Gmail service that can be used according to the scopes in
        the credentials used to build the service."""
        return googleapiclient.discovery.build('gmail', 'v1', credentials=credentials)
    
    def list_messages(self, query: str) -> List[Dict[Optional[str], Optional[str]]]:
        """Returns a list of the messages in the user's inbox matching
        the specified input query."""
        try:
            logger.info('Beginning message retrieval')
            response = self.service.users().messages().list(userId=self.user_id, q=query).execute()
            messages = response.get('messages', [])

            if not messages:
                logger.info('No messages found.')
                return messages
            else:
                logger.info('Found %i messages.', len(messages))
                return messages
        except Exception as error:
            logger.error('An error occurred while retrieving messages: %s', error)
            return []

    def get_message(self, message_id: str) -> Optional[Dict[str, str]]:
        """Retrieves details about a specific message based on the
        message ID from the user's mailbox."""
        message_content = {}
        try:
            message = self.service.users().messages().get(userId=self.user_id, id=message_id, format='full').execute()

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
            message_content['message_timestamp'] = message.get('internalDate')

            # Extract message content
            parts = message['payload'].get('parts', [])
            if not parts:
                logger.info('Single part message detected.')
                body_data = message['payload']['body']['data']
            else:
                logger.info('Multipart body detected.')
                for part in parts:
                    if part['mimeType'] == 'text/html':
                        body_data = part['body']['data']
                        break
                    elif part['mimeType'] == 'multipart/related':
                        body_data = part['parts'][0]['body']['data']
                        break

            message_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
            logger.info('Successfully decoded message.')
            message_content['body'] = message_body
            return message_content
        except Exception as error:
            logger.error('An error occurred while retrieving the message: %s', error)
            return message_content
        
class PubSubService:
    """A class for interacting with Pub/Sub topics."""
    def __init__(self, project_id: str, topic_id: str) -> None:
        """Initializes the PubSubService instance."""
        self.project_id = project_id
        self.topic_id = topic_id
        self.publisher = pubsub_v1.PublisherClient()
        self.topic_path = self.publisher.topic_path(project_id, topic_id)

    def publish_message(self, data: dict) -> None:
        """Publishes a message to the Pub/Sub topic corresponding to the
        PubSubService instance."""
        message_json = json.dumps(data)
        message_bytes = message_json.encode('UTF-8')
        future = self.publisher.publish(self.topic_path, message_bytes)
        future.result()  # Optionally wait for it to complete
        logger.info('Published message with ID: %s', data['message_id'])

class FirestoreService:
    """A class for interacting with a Firestore database."""
    def __init__(self, collection_name: str, project_id: str=None) -> None:
        """Initializes the FirestoreService instance."""
        self.collection_name = collection_name
        if project_id:
            self.db = firestore.Client(project=project_id)
        else:
            self.db = firestore.Client()

    def is_message_processed(self, message_id: str) -> bool:
        """Checks if a message ID has already been processed."""
        doc_ref = self.db.collection(self.collection_name).document(message_id)
        return doc_ref.get().exists
    
    def mark_message_as_processed(self, message_id: str, message_timestamp: str) -> None:
        """Adds a message ID and its timestamp to Firestore to indicate
        it has been processed."""
        if message_id is not None:
            self.db.collection(self.collection_name).document(message_id).set(
                {
                    'processed': True,
                    'timestamp': message_timestamp
                }
            )
        else:
            raise ValueError('Message ID cannot be none.')

    def get_latest_processed_date(self) -> Optional[str]:
        """Fetches the most recent processed email timestamp from Firestore
        and returns it in YYYY-MM-DD format."""
        try:
            logger.info('Fetching date of last processed email')
            query = self.db.collection(self.collection_name).order_by(
                'timestamp', direction=firestore.Query.DESCENDING
            ).limit(1)
            docs = query.stream()
            for doc in docs:
                timestamp_str = doc.to_dict().get('timestamp')
                timestamp = int(timestamp_str)
                if timestamp:
                    latest_date = datetime.datetime.fromtimestamp(
                        timestamp=timestamp/1000,
                        tz=datetime.timezone.utc
                    )
                    latest_date_str = latest_date.strftime('%Y-%m-%d')
                    logger.info(f'Date of last processed email: {latest_date_str}')
                    return latest_date_str
        except Exception as e:
            logger.error(f'Exception occurred while fetching the latest processed date: {str(e)}')
            return None

def extract_html_content(html_body: str) -> str:
    """Extracts text content from an HTML content string."""
    soup = BeautifulSoup(html_body, 'html.parser')
    # Remove script, style, and meta tags
    for tag in soup(['script', 'style', 'meta']):
        tag.decompose()
    # Extract the text content, keeping only visible text
    return soup.get_text(separator='/n', strip=True)

def extract_email(email_string: str) -> str:
    """Extracts the email address from a string in the format 'Venmo <venmo@venmo.com>'."""
    return email_string[email_string.find('<')+1:email_string.find('>')]

def get_env_variable(var_name: str) -> str:
    """Fetches an environment variable and raises an error if not found."""
    logger.info(f'Fetching environment variable: {var_name}')
    value = os.environ.get(var_name)
    if value is None:
        raise ValueError(f'Missing environment variable: {var_name}')
    logger.info(f'Successfully fetched {var_name}')
    return value

def gmail_watcher_main(request) -> None:
    """Main event handler for the Gmail watcher Cloud Function."""
    project_id = get_env_variable(var_name='GCP_PROJECT_ID')
    oauth_token_id = get_env_variable(var_name='OAUTH_TOKEN_SECRET_ID')
    user_email = get_env_variable('EMAIL_ADDRESS')
    firestore_collection = get_env_variable(var_name='MESSAGE_PROCESSING_COLLECTION')
    pubsub_topic = get_env_variable(var_name='PUBSUB_TOPIC_ID')
    secret_manager = GCPSecretManager(project_id=project_id)
    credentials = secret_manager.generate_oauth_credentials(secret_id=oauth_token_id)
    gmail_service = GmailService(user_id=user_email, credentials=credentials)
    firestore_service = FirestoreService(
        collection_name=firestore_collection,
        project_id=project_id
    )
    since_date = firestore_service.get_latest_processed_date()
    query = (
        f'(from:{get_env_variable('VENMO_EMAIL')} OR '
        f'from:{get_env_variable('AMEX_EMAIL')} OR '
        f'from:{get_env_variable('CHASE_EMAIL')} OR '
        f'from:{get_env_variable('CAPITALONE_EMAIL')} OR '
        f'from:{get_env_variable('WELLSFARGO_EMAIL')}) '
        f'AND after:{since_date}'
    )
    messages = gmail_service.list_messages(query=query)
    pub_sub = PubSubService(
        project_id=project_id,
        topic_id=pubsub_topic
    )
    for message in messages:
        msg_id = message['id']
        # Check if the message has already been processed
        if not firestore_service.is_message_processed(msg_id):
            message_content = gmail_service.get_message(
                message_id=msg_id
            )
            email = extract_email(message_content['from'])
            email_html = extract_html_content(message_content['body'])
            message_data = {
                'message_id': msg_id,
                'message_sender': email,
                'message_subject': message_content['subject'],
                'message_timestamp': message_content['message_timestamp'],
                'message_body': email_html
            }
            pub_sub.publish_message(data=message_data)
            firestore_service.mark_message_as_processed(
                message_id=msg_id,
                message_timestamp=message_data['message_timestamp']
            )

gmail_watcher_main(request='')