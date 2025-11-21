import os
from dotenv import load_dotenv

# Load .env file and override existing environment variables
load_dotenv(override=True)

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable is required")
    
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    
    # Google OAuth
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    if not GOOGLE_CLIENT_ID:
        raise ValueError("GOOGLE_CLIENT_ID environment variable is required")
    
    
    # Server settings
    SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")  # Bind address for uvicorn
    SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))

    # Public URL for generating links (images, API docs, etc.)
    # In development: http://localhost:8000
    # In production: https://wedding.open-spaces.xyz
    PUBLIC_URL = os.getenv("PUBLIC_URL", f"http://localhost:{SERVER_PORT}")
    
    # File settings
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(5 * 1024 * 1024)))  # Default 5 MB

    # Database settings
    DB_URL = os.getenv("DB_URL", "sqlite:///./data/app.db")

config = Config()
