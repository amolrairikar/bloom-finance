import sqlite3
import os
import datetime
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from google_auth_oauthlib.flow import Flow

from models import *
from database import get_db_connection
from utils import *
from logging_config import logger

router = APIRouter()

# Set scope for Gmail API access
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# Health check
@router.get('/health')
def health_check() -> Dict[str, str]:
    "Health check to check FastAPI status."
    return {'status': 'healthy'}

# Login/OAuth
@router.get('/oauth/login', response_class=RedirectResponse, response_model=None)
def login(request: Request):
    flow = Flow.from_client_secrets_file(
        os.environ['OAUTH_CREDENTIALS_PATH'],
        scopes=SCOPES,
        redirect_uri=request.url_for('callback')
    )
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    return RedirectResponse(url=authorization_url)

@router.get('/oauth/callback', name='callback', response_class=HTMLResponse, response_model=None)
def callback(request: Request, db: sqlite3.Connection = Depends(get_db_connection)):
    state = request.query_params.get('state')
    code = request.query_params.get('code')
    flow = Flow.from_client_secrets_file(
        client_secrets_file=os.environ['OAUTH_CREDENTIALS_PATH'],
        scopes=SCOPES,
        state=state,
        redirect_uri=request.url_for('callback')
    )
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    # Save tokens to the SQLite database
    save_tokens(db=db, table_name='user_data',
                access_token=credentials.token, refresh_token=credentials.refresh_token)
    
    return HTMLResponse(content="<h1>OAuth flow completed successfully!</h1>")

# Transactions
@router.post('/transactions/', response_model=Dict[str, str])
def refresh_transactions(
    db: sqlite3.Connection = Depends(get_db_connection)
) -> Dict[str, str]:
    """
    API endpoint to refresh transactions by fetching new transaction related emails
    and calling the create_transaction helper function.

    Parameters:
        - transaction (Transaction): Defined in models.py, representation of the schema
        for the transactions table in the application database.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    last_refresh_datetime = db.execute('SELECT last_transaction_refresh FROM user_data').fetchone()['last_transaction_refresh']
    transactions = email_parser_main(db=db, last_refresh_datetime=last_refresh_datetime)

    # Fetch rules from transaction_rules table
    rules = db.execute('SELECT merchant_original_name, merchant_renamed_name FROM transaction_rules').fetchall()
    for transaction in transactions:
        transaction['merchant'] = apply_rules_engine(transaction['merchant'], rules)
        transaction_data = Transaction(**transaction)
        create_transaction(db=db, transaction=transaction_data)
    
    # Update the last_refresh_time in the user_data table
    name = os.environ['NAME']
    current_datetime = get_current_datetime_string()
    db.execute(
        f'UPDATE user_data SET last_transaction_refresh = ? WHERE name=?;',
        (current_datetime, name)
    )
    return {'message': f'{str(len(transactions))} transactions imported successfully'}

@router.get('/transactions/', response_model=List[Transaction])
def get_transactions(
    merchant: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    subcategory: Optional[str] = None,
    account_name: Optional[str] = None,
    db: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Fetches all transactions from the transactions database. Can optionally specify query
    parameters to filter the list of transactions returned.

    Parameters:
        - merchant (Optional[str]): Optional merchant the transaction occurred with.
        - start_date (Optional[str]): Optional date before which transactions are not returned.
        - end_date (Optional[str]): Optional date after which transactions are not returned.
        - category (Optional[str]): Optional category the transaction falls under.
        - subcategory (Optional[str]): Optional subcategory the transaction falls under.
        - account_name (Optional[str]): Optional account_name the transaction occurred in.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    query = 'SELECT * FROM transactions WHERE 1=1'
    params = []

    if merchant:
        query = add_get_condition(query, ' AND merchant = ?', params, merchant)   
    if start_date:
        validate_date_format(date_string=start_date)
        query = add_get_condition(query, ' AND transaction_date >= ?', params, start_date)
    if end_date:
        validate_date_format(date_string=end_date)
        query = add_get_condition(query, ' AND transaction_date <= ?', params, end_date)
    if category:
        query = add_get_condition(query, ' AND category = ?', params, category)
    if subcategory:
        query = add_get_condition(query, ' AND subcategory = ?', params, subcategory)
    if account_name:
        query = add_get_condition(query, ' AND account_name = ?', params, account_name)

    return execute_get_query(
        query=query,
        db=db,
        model_type=Transaction,
        params=params
    )

@router.patch('/transactions/{transaction_id}', response_model=Transaction)
def update_transaction(
    transaction_id: str,
    transaction_update: TransactionUpdate,
    db: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Updates values for a transaction. Can specify as many fields to update as desired.

    Parameters:
        - transaction_id (str): Unique identifier for a transaction.
        - transaction_update (TransactionUpdate): Defined in models.py, representation
        of the schema to update transactions in the application database.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    # Check if the transaction exists
    cursor = db.execute('SELECT * FROM transactions WHERE transaction_id = ?', (transaction_id,))
    transaction_row = cursor.fetchone()
    if transaction_row is None:
        raise HTTPException(status_code=404, detail='Transaction not found')

    # Prepare the fields dictionary for the update
    fields = {
        'transaction_date': transaction_update.transaction_date,
        'merchant': transaction_update.merchant,
        'bucket': transaction_update.bucket,
        'amount': transaction_update.amount,
        'category': transaction_update.category,
        'subcategory': transaction_update.subcategory,
        'account_name': transaction_update.account_name,
        'is_recurring': transaction_update.is_recurring,
    }

    # Prepare the update query and parameters
    query, params = create_patch_query(
        table_name='transactions', primary_key='transaction_id', fields=fields
    )
    if not params:
        raise HTTPException(status_code=400, detail='No fields to update')
    params.append(transaction_id)

    # Execute the update
    db.execute(query, params)
    
    # Fetch the updated transaction for the response
    cursor = db.execute('SELECT * FROM transactions WHERE transaction_id = ?', (transaction_id,))
    updated_transaction_row = cursor.fetchone()
    
    if updated_transaction_row is None:
        raise HTTPException(status_code=404, detail='Transaction not found after update')

    return Transaction(**dict(updated_transaction_row))

@router.delete('/transactions/{transaction_id}', response_model=Dict[str, str])
def delete_transaction(
    transaction_id: str,
    db: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Deletes a transaction from the transactions table based on the given transaction ID.

    Parameters:
        - transaction_id (str): Unique identifier for a transaction to be deleted.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    # Check if the transaction exists
    cursor = db.execute('SELECT * FROM transactions WHERE transaction_id = ?', (transaction_id,))
    transaction_row = cursor.fetchone()
    
    if transaction_row is None:
        raise HTTPException(status_code=404, detail=f'Transaction {transaction_id} not found')

    # Delete the transaction
    db.execute('DELETE FROM transactions WHERE transaction_id = ?', (transaction_id,))

    return {'message': f'Transaction {transaction_id} deleted successfully'}

# User data
@router.get('/user_data/', response_model=List[UserData])
def get_user_info(db: sqlite3.Connection = Depends(get_db_connection)) -> List[UserData]:
    "Fetches all user profile data."
    query = 'SELECT * from user_data WHERE 1=1'
    return execute_get_query(
        query=query,
        db=db,
        model_type=UserData
    )

# Transaction rules
router.get('/transaction_rules/', response_model=List[TransactionRule])
def get_transaction_rules(db: sqlite3.Connection = Depends(get_db_connection)) -> List[TransactionRule]:
    "Fetches all transaction renaming rules the user has set."
    query = 'SELECT * from transaction_rules WHERE 1=1'
    return execute_get_query(
        query=query,
        db=db,
        model_type=TransactionRule
    )

router.post('/transaction_rules/')
def add_transaction_rule(rule: TransactionRule, db: sqlite3.Connection = Depends(get_db_connection)) -> Dict[str, str]:
    "Adds a new transaction rule created by the user."
    try:
        current_datetime = get_current_datetime_string()
        rule_id = generate_uuid()

        # Insert the new rule into the rules table
        db.execute(
            'INSERT INTO rules (rule_id, merchant_original_name, merchant_renamed_name, rule_created_date) VALUES (?, ?, ?, ?);',
            (rule_id, rule.merchant_original_name, rule.merchant_renamed_name, current_datetime)
        )
        
        # Trigger the backfill process for existing transactions
        backfill_count = backfill_transaction_rules(db=db)

        return {'message': 'Rule added successfully', 'backfilled_transactions': backfill_count}
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'Error adding rule: {e}')
    
router.patch('/transaction_rules/{transaction_rule_id}', response_model=TransactionRule)
def update_transaction_rule(
    transaction_rule_id: str,
    transaction_rule_update: TransactionRuleUpdate,
    db: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Updates values for a transaction. Can specify as many fields to update as desired.

    Parameters:
        - transaction_rule_id (str): Unique identifier for a transaction rule.
        - transaction_rule_update (TransactionRuleUpdate): Defined in models.py,
        representation of the schema to update rule in database.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    # Check if the transaction exists
    cursor = db.execute('SELECT * FROM transaction_rules WHERE rule_id = ?', (transaction_rule_id,))
    transaction_rule_row = cursor.fetchone()
    if transaction_rule_row is None:
        raise HTTPException(status_code=404, detail='Rule not found')

    # Prepare the fields dictionary for the update
    fields = {
        'merchant_original_name': transaction_rule_update.merchant_original_name,
        'merchant_renamed_name': transaction_rule_update.merchant_renamed_name,
        'rule_created_date': transaction_rule_update.rule_created_date
    }

    # Prepare the update query and parameters
    query, params = create_patch_query(
        table_name='transaction_rules', primary_key='transaction_rule_id', fields=fields
    )
    if not params:
        raise HTTPException(status_code=400, detail='No fields to update')
    params.append(transaction_rule_id)

    # Execute the update
    db.execute(query, params)

    # Trigger the backfill process for existing transactions
    backfill_count = backfill_transaction_rules(db=db)
    logger.info('Updated %i transactions after rule update', backfill_count)
    
    # Fetch the updated transaction for the response
    cursor = db.execute('SELECT * FROM transactions WHERE transaction_id = ?', (transaction_rule_id,))
    updated_rule_row = cursor.fetchone()
    
    if updated_rule_row is None:
        raise HTTPException(status_code=404, detail='Rule not found after update')

    return TransactionRule(**dict(updated_rule_row))

@router.delete('/transaction_rules/{transaction_rule_id}', response_model=Dict[str, str])
def delete_transaction_rule(
    transaction_rule_id: str,
    db: sqlite3.Connection = Depends(get_db_connection)
):
    """
    Deletes a transaction rule from the transaction_rules table based on the given rule ID.

    Parameters:
        - transaction_rule_id (str): Unique identifier for a transaction rule to be deleted.
        - db (sqlite3.Connection): Represents a SQLite database connection.
    """
    # Check if the transaction exists
    cursor = db.execute('SELECT * FROM transaction_rules WHERE transaction_rule_id = ?', (transaction_rule_id,))
    rule_row = cursor.fetchone()
    
    if rule_row is None:
        raise HTTPException(status_code=404, detail=f'Rule {transaction_rule_id} not found')

    # Delete the transaction
    db.execute('DELETE FROM transaction_rules WHERE transaction_rule_id = ?', (rule_row,))

    return {'message': f'Rule {transaction_rule_id} deleted successfully'}