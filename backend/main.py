from fastapi import FastAPI

from database import init_db
from routes import router

# Initialize API and SQLite DB
app = FastAPI()
init_db()
app.include_router(router)