import datetime

def convert_unix_timestamp_to_date(unix_timestamp: str) -> str:
    """
    Converts a unix timestamp to a date string in YYYY-MM-DD format.

    Parameters:
        - unix_timestamp (str): A Unix timestamp.    
    """
    return datetime.datetime.fromtimestamp(int(unix_timestamp) / 1000).strftime('%Y-%m-%d')