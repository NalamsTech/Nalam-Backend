import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """
    Configuration class for the Flask application.
    Loads settings from environment variables.
    """
    # General Config
    SECRET_KEY = os.environ.get('SECRET_KEY', 'a-default-secret-key')

    # Gemini API Key
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

    # Firebase Config
    FIREBASE_CRED_FILE = os.environ.get('FIREBASE_CRED_FILE', 'nalam-invoice-1-firebase-adminsdk-fbsvc-e687c97f65.json')

    # Nalam Foods URL
    NALAM_FOODS_URL = os.environ.get('NALAM_FOODS_URL', 'https://nalamfoodsusa.com')