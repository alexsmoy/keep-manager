#!/usr/bin/env python3
"""
Keep Manager - Automated Setup Checker and Launcher

This script validates your setup and starts the Keep Manager web application.
Run this instead of manually running uvicorn to ensure everything is configured correctly.
"""

import os
import sys
import subprocess
from pathlib import Path

# ANSI color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    """Print a section header"""
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{text}{Colors.END}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.END}\n")

def print_success(text):
    """Print success message"""
    print(f"{Colors.GREEN}✓{Colors.END} {text}")

def print_warning(text):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠{Colors.END} {text}")

def print_error(text):
    """Print error message"""
    print(f"{Colors.RED}✗{Colors.END} {text}")

def print_info(text):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ{Colors.END} {text}")

def check_python_version():
    """Check if Python version is 3.8+"""
    print_info("Checking Python version...")
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        print_success(f"Python {version.major}.{version.minor}.{version.micro} detected")
        return True
    else:
        print_error(f"Python 3.8+ required, but you have {version.major}.{version.minor}.{version.micro}")
        return False

def check_venv():
    """Check if virtual environment is activated (optional warning)"""
    print_info("Checking virtual environment...")
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print_success("Virtual environment is activated")
        return True
    else:
        print_warning("Virtual environment not detected (recommended but not required)")
        print_info("  Tip: Activate with 'source venv/bin/activate' or 'venv\\Scripts\\activate'")
        return True  # Not a blocker

def install_requirements():
    """Check for requirements.txt and offer to install dependencies"""
    print_info("Checking requirements.txt...")

    if not Path('requirements.txt').exists():
        print_warning("requirements.txt not found (optional)")
        return True

    print_success("requirements.txt found")

    # Quick check if dependencies might be missing
    try:
        import fastapi
        import uvicorn
        import dotenv
        from google.auth import credentials
        from googleapiclient import discovery
        print_info("Core dependencies appear to be installed")
        return True
    except ImportError:
        # Dependencies are missing, offer to install
        print_warning("Some dependencies appear to be missing")
        print_info("  Found requirements.txt - we can install them automatically")

        try:
            response = input(f"\n{Colors.YELLOW}Install dependencies from requirements.txt? [Y/n]: {Colors.END}").strip().lower()

            if response in ['', 'y', 'yes']:
                print_info("\nInstalling dependencies from requirements.txt...")
                print_info("This may take a minute...\n")

                try:
                    result = subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'],
                        check=True,
                        capture_output=False
                    )
                    print_success("\nDependencies installed successfully!")
                    return True
                except subprocess.CalledProcessError as e:
                    print_error(f"Failed to install dependencies: {e}")
                    print_info("Please run manually: pip install -r requirements.txt")
                    return False
            else:
                print_warning("Skipping dependency installation")
                print_info("You'll need to install them manually: pip install -r requirements.txt")
                return True  # Don't block, let dependency check fail with details

        except KeyboardInterrupt:
            print_info("\nSkipping dependency installation")
            return True

def check_dependencies():
    """Check if required dependencies are installed"""
    print_info("Verifying all dependencies are available...")
    required_modules = [
        ('fastapi', 'FastAPI'),
        ('uvicorn', 'Uvicorn'),
        ('dotenv', 'python-dotenv'),
        ('google.auth', 'google-auth'),
        ('googleapiclient', 'google-api-python-client'),
        ('pydantic', 'pydantic'),
    ]

    missing = []
    for module_name, package_name in required_modules:
        try:
            __import__(module_name)
            print_success(f"{package_name} is available")
        except ImportError:
            print_error(f"{package_name} not found")
            missing.append(package_name)

    if missing:
        print_error(f"\nMissing dependencies: {', '.join(missing)}")
        if Path('requirements.txt').exists():
            print_info("Install with: pip install -r requirements.txt")
        else:
            print_info(f"Install with: pip install {' '.join(missing)}")
        return False

    print_success("All dependencies verified!")
    return True

def check_credentials():
    """Check if credentials.json exists"""
    print_info("Checking Google Cloud credentials...")
    if Path('credentials.json').exists():
        print_success("credentials.json found")
        return True
    else:
        print_error("credentials.json not found in project root")
        print_info("  1. Create a Service Account in Google Cloud Console")
        print_info("  2. Download the JSON key file")
        print_info("  3. Rename it to 'credentials.json' and place in project root")
        print_info("  See: ai-docs/auth-setup.md for detailed instructions")
        return False

def check_env_file():
    """Check if .env file exists and has required variables"""
    print_info("Checking environment configuration...")

    if not Path('.env').exists():
        print_error(".env file not found")
        print_info("  1. Copy .env.template to .env")
        print_info("  2. Edit .env and set KEEP_USER_EMAIL to your Google Workspace email")
        return False

    print_success(".env file found")

    # Load and check for required variables
    from dotenv import load_dotenv
    load_dotenv()

    email = os.environ.get('KEEP_USER_EMAIL', '')
    if not email:
        print_error("KEEP_USER_EMAIL not set in .env file")
        print_info("  Edit .env and add: KEEP_USER_EMAIL=your-email@yourdomain.com")
        return False

    print_success(f"KEEP_USER_EMAIL configured: {email}")
    return True

def check_database():
    """Check if database exists, initialize/verify schema if needed"""
    print_info("Checking database...")

    db_exists = Path('keep_cache.db').exists()
    if db_exists:
        print_success("Database file found")
    else:
        print_warning("Database not found, creating...")

    try:
        from db import init_db
        init_db()
        if db_exists:
            print_success("Database schema verified/updated")
        else:
            print_success("Database initialized successfully")
        return True
    except Exception as e:
        print_error(f"Failed to initialize database: {e}")
        return False

def test_google_keep_api():
    """Test Google Keep API connection"""
    print_info("Testing Google Keep API connection...")

    try:
        from keep_client import get_keep_service
        service = get_keep_service()

        if not service:
            print_error("Failed to initialize Keep service")
            print_info("  Check credentials.json and KEEP_USER_EMAIL settings")
            return False

        # Try to make a simple API call
        try:
            results = service.notes().list(pageSize=1).execute()
            note_count = len(results.get('notes', []))
            print_success(f"Google Keep API connection successful")
            return True
        except Exception as e:
            print_error(f"Google Keep API call failed: {str(e)}")
            print_info("  Possible causes:")
            print_info("  - Domain-wide delegation not configured")
            print_info("  - Incorrect scopes in Google Workspace Admin Console")
            print_info("  - Service account lacks permissions")
            print_info("  See: ai-docs/auth-setup.md for configuration help")
            return False

    except Exception as e:
        print_error(f"Error testing API: {e}")
        return False

def offer_sync():
    """Offer to sync notes before starting"""
    print_info("Would you like to sync notes from Google Keep now?")
    print_info("  This will pull all your Keep notes to the local database.")
    print_info("  (You can skip this and sync manually later with 'python sync.py')")

    try:
        response = input(f"\n{Colors.YELLOW}Sync now? [y/N]: {Colors.END}").strip().lower()

        if response in ['y', 'yes']:
            print_info("\nSyncing notes from Google Keep...")
            try:
                from sync import sync_notes
                success = sync_notes()
                if success:
                    print_success("Sync completed successfully")
                    return True
                else:
                    print_warning("Sync completed with errors (check logs above)")
                    return True  # Don't block startup
            except Exception as e:
                print_error(f"Sync failed: {e}")
                return True  # Don't block startup
        else:
            print_info("Skipping sync")
            return True

    except KeyboardInterrupt:
        print_info("\nSkipping sync")
        return True

def start_server():
    """Start the FastAPI server"""
    print_header("Starting Keep Manager Web Server")
    print_info("Server will start on: http://localhost:8000")
    print_info("Press Ctrl+C to stop the server\n")

    try:
        # Start uvicorn
        subprocess.run([
            sys.executable, '-m', 'uvicorn',
            'main:app',
            '--reload',
            '--host', '0.0.0.0',
            '--port', '8000'
        ])
    except KeyboardInterrupt:
        print_info("\n\nServer stopped by user")
        sys.exit(0)
    except Exception as e:
        print_error(f"Failed to start server: {e}")
        sys.exit(1)

def main():
    """Main execution flow"""
    print_header("Keep Manager - Setup Validation & Launcher")

    # Run all checks
    checks = [
        ("Python Version", check_python_version),
        ("Virtual Environment", check_venv),
        ("Requirements Installation", install_requirements),
        ("Dependencies", check_dependencies),
        ("Credentials File", check_credentials),
        ("Environment Variables", check_env_file),
        ("Database", check_database),
        ("Google Keep API", test_google_keep_api),
    ]

    failed_checks = []

    for check_name, check_func in checks:
        if not check_func():
            failed_checks.append(check_name)

    # Summary
    if failed_checks:
        print_header("Setup Validation Failed")
        print_error(f"{len(failed_checks)} check(s) failed:")
        for check in failed_checks:
            print(f"  • {check}")
        print_info("\nPlease fix the issues above and run this script again.")
        print_info("For detailed setup instructions, see: README.md")
        sys.exit(1)
    else:
        print_header("All Checks Passed!")
        print_success("Your Keep Manager setup is properly configured")

        # Offer to sync
        offer_sync()

        # Start the server
        start_server()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_info("\n\nSetup interrupted by user")
        sys.exit(0)
