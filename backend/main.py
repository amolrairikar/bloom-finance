from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
#from routes import router

# Initialize API
app = FastAPI()
#app.include_router(router)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# TODO: Clean up API code now that we have transitioned to an event-driven model