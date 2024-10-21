from pydantic import BaseModel
from typing import Optional

class Transaction(BaseModel):
    transaction_id: str
    transaction_date: str
    merchant: str
    bucket: str
    amount: str
    category: str
    subcategory: str
    account_name: str
    is_recurring: str

class TransactionUpdate(BaseModel):
    transaction_date: Optional[str] = None
    merchant: Optional[str] = None
    bucket: Optional[str] = None
    amount: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    account_name: Optional[str] = None
    is_recurring: Optional[str] = None

class UserData(BaseModel):
    name: str
    last_transaction_refresh: str
    access_token: str
    refresh_token: str

class TransactionRule(BaseModel):
    rule_id: str
    merchant_original_name: str
    merchant_renamed_name: str
    rule_created_date: str

class TransactionRuleUpdate(BaseModel):
    merchant_original_name: str
    merchant_renamed_name: str
    rule_created_date: str