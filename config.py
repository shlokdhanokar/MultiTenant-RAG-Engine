import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# Global App Settings
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")
OAUTH_REDIRECT_URI = f"{APP_BASE_URL}/integrations/oauth/callback"

# Integration Credentials Mapping
# These will be fetched and merged into the service definition fetched from MongoDB.
INTEGRATION_CREDENTIALS = {
    "google_calendar": {
        "clientId": os.getenv("GOOGLE_CLIENT_ID", ""),
        "clientSecret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "redirectUri": OAUTH_REDIRECT_URI
    },
    "shopify": {
        "clientId": os.getenv("SHOPIFY_CLIENT_ID", ""),
        "clientSecret": os.getenv("SHOPIFY_CLIENT_SECRET", ""),
        "redirectUri": OAUTH_REDIRECT_URI
    },
    "slack": {
        "clientId": os.getenv("SLACK_CLIENT_ID", ""),
        "clientSecret": os.getenv("SLACK_CLIENT_SECRET", ""),
        "redirectUri": OAUTH_REDIRECT_URI
    },
    "calendly": {
        "clientId": os.getenv("CALENDLY_CLIENT_ID", ""),
        "clientSecret": os.getenv("CALENDLY_CLIENT_SECRET", ""),
        "redirectUri": OAUTH_REDIRECT_URI
    },
}
