import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Load environment variables from .env file
load_dotenv()

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/keep', 'https://www.googleapis.com/auth/keep.readonly']
SERVICE_ACCOUNT_FILE = 'credentials.json'

# The email address of the Google Workspace user to impersonate.
# Google Keep API via Service Account requires Domain-Wide Delegation.
def get_keep_service(user_email=None):
    """Shows basic usage of the Keep API using a Service Account."""
    try:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
             print(f"Service account file {SERVICE_ACCOUNT_FILE} not found.")
             return None

        # Load the service account credentials
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES
        )
        
        if not user_email:
            user_email = os.environ.get('KEEP_USER_EMAIL', '')
            
        if user_email:
            creds = creds.with_subject(user_email)
        else:
            print("WARNING: user_email is not set. Service Account may fail to access notes without impersonation.")

        service = build('keep', 'v1', credentials=creds)
        return service
    except Exception as err:
        print(f"Error initializing Keep API service: {err}")
        return None

if __name__ == "__main__":
    import sys
    
    user_email = ""
    # Check if user provided an email via argument for testing
    if len(sys.argv) > 1:
        user_email = sys.argv[1]
        
    service = get_keep_service(user_email)
    if service:
        print("Authentication successful! Testing API call...")
        try:
            # Attempt to list notes to verify scopes and delegation
            results = service.notes().list(pageSize=10).execute()
            notes = results.get('notes', [])
            if not notes:
                print('No notes found for this user.')
            else:
                print(f'Found {len(notes)} notes. First few:')
                for note in notes[:5]:
                    print(f" - {note.get('title', 'No Title')}")
        except Exception as e:
            print(f"Failed to list notes. Ensure Domain-Wide Delegation is configured for the Service Account in Google Workspace admin console.\nError: {e}")
    else:
        print("Authentication failed.")
