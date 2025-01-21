from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from neo4j import GraphDatabase
import os
from cryptography.fernet import Fernet
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import string
import secrets
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from fastapi import Query

router = APIRouter()

# Load environment variables
load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI") # "bolt://localhost:7687"
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") # "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD") # "password"
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD)) # Create a new driver instance

# Security setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # Create an OAuth2 password bearer instance
SECRET_KEY = "codefest@123" # Secret key for encoding/decoding JWT
ALGORITHM = "HS256" # Algorithm for encoding/decoding JWT

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com") # SMTP server
SMTP_PORT = int(os.getenv("SMTP_PORT", 587)) # SMTP port
SMTP_USERNAME = os.getenv("SMTP_USERNAME") # SMTP username
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") # SMTP password
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL") # Admin email

# Neo4j queries
def generate_secure_password():
    """Generate a secure random password with:
    - At least 12 characters
    - Mix of uppercase, lowercase, numbers, and special characters
    - Truly random using secrets module
    """
    # Define character sets
    lowercase = string.ascii_lowercase # Lowercase letters
    uppercase = string.ascii_uppercase # Uppercase letters
    digits = string.digits # Numbers
    special = "!@#$%^&*" # Special characters

    # Ensure at least one character from each set
    password = [
        secrets.choice(lowercase), # Lowercase
        secrets.choice(uppercase), # Uppercase
        secrets.choice(digits), # Numbers
        secrets.choice(special), # Special characters
    ]

    # Add remaining random characters
    all_characters = lowercase + uppercase + digits + special # Combine all characters
    password.extend(secrets.choice(all_characters) for _ in range(8)) # Add 8 more random characters

    # Shuffle the password
    shuffled = list(password) # Convert to list
    secrets.SystemRandom().shuffle(shuffled) # Shuffle the list

    return ''.join(shuffled) # Convert back to string


# Add encryption setup
def generate_key(): #
    salt = b'syscolabs'
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(b"codefest@123"))
    return key

# Add encryption setup
cipher_suite = Fernet(generate_key())

# Add encryption functions
def encrypt_password(password: str) -> str:
    encrypted_pass = cipher_suite.encrypt(password.encode())
    return base64.urlsafe_b64encode(encrypted_pass).decode()

# Add decryption function
def decrypt_password(encrypted_password: str) -> str:
    try:
        decoded = base64.urlsafe_b64decode(encrypted_password.encode())
        decrypted_pass = cipher_suite.decrypt(decoded)
        return decrypted_pass.decode()
    except Exception:
        return None


# Pydantic models remain the same
class UserCreate(BaseModel):
    email: EmailStr
    employee_id: str
    company_position: str

# Pydantic models remain the same
class UserLogin(BaseModel):
    email: EmailStr
    password: str

# Pydantic models remain the same
class ForgotPassword(BaseModel):
    email: EmailStr

# Pydantic models remain the same
class AdminApproval(BaseModel):
    approval_token: str
    approved: bool


# Only modify the password-related Neo4j functions
def activate_user(tx, email: str, password: str):
    encrypted_password = encrypt_password(password) # Encrypt password
    query = """
    MATCH (u:User {email: $email})
    SET u.password = $password,
        u.status = 'active',
        u.is_active = true,
        u.approval_token = null
    """
    tx.run(query, email=email, password=encrypted_password) # Update password and activate account


# Only modify the login endpoint
@router.post("/login") # Update the endpoint
async def login(form_data: OAuth2PasswordRequestForm = Depends()): # Update the function signature
    with driver.session() as session: # Update the session block
        user = session.execute_read(get_user, form_data.username) # Update the user retrieval

        if not user or user['status'] != 'active': # Update the status check
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, # Update the status code
                detail="Invalid credentials or inactive account"
            )

        stored_password = decrypt_password(user['password']) # Decrypt stored password
        if not stored_password or stored_password != form_data.password: # Decrypt and compare passwords
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, # Update the status code
                detail="Invalid credentials"
            )

        return {
            "detail": "true"
        }


# All other functions and endpoints remain exactly the same
def create_pending_user(tx, email: str, employee_id: str, company_position: str, approval_token: str):
    query = """
    CREATE (u:User {
        email: $email,
        employee_id: $employee_id,
        company_position: $company_position,
        approval_token: $approval_token,
        status: 'pending',
        is_active: false
    })
    """
    tx.run(query, email=email, employee_id=employee_id,
           company_position=company_position, approval_token=approval_token) # Create pending user

# Add this Neo4j function
def get_user(tx, email: str):
    query = """
    MATCH (u:User {email: $email})
    RETURN u
    """
    result = tx.run(query, email=email) # Find user by email
    record = result.single() # Get single record
    return record['u'] if record else None # Return user if found


def get_user_by_token(tx, token: str):
    query = """
    MATCH (u:User {approval_token: $token})
    RETURN u
    """
    result = tx.run(query, token=token) # Find user by token
    record = result.single() # Get single record
    return record['u'] if record else None # Return user if found

# Add this Neo4j function
def set_password_reset_token(tx, email: str, reset_token: str):
    query = """
    MATCH (u:User {email: $email})
    SET u.reset_token = $reset_token
    """
    tx.run(query, email=email, reset_token=reset_token) # Set password reset token


# Helper functions remain the same
def send_email(to_email: str, subject: str, body: str):
    msg = MIMEMultipart() # Create a MIME message
    msg['From'] = SMTP_USERNAME # Set the sender
    msg['To'] = to_email # Set the recipient
    msg['Subject'] = subject # Set the subject
    msg.attach(MIMEText(body, 'html')) # Attach the HTML body

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server: # Connect to SMTP server
        server.starttls() # Start TLS
        server.login(SMTP_USERNAME, SMTP_PASSWORD) # Login to SMTP server
        server.send_message(msg) # Send the email


# API Endpoints
@router.post("/admin/create-account")
async def create_account(user: UserCreate):
    """Admin initiates account creation"""

    approval_token = secrets.token_urlsafe(32) # Generate approval token

    try:
        # Check if user already exists
        with driver.session() as session:
            existing_user = session.execute_read(get_user, user.email) # Check if user exists
            if existing_user: # If user exists, raise an error
                raise HTTPException(
                    status_code=400,
                    detail="User already exists"
                ) # Return error response

            # Create pending user
            session.execute_write(create_pending_user,
                                  user.email,
                                  user.employee_id,
                                  user.company_position,
                                  approval_token) # Create pending user

    except Exception as e: # Handle any exceptions
        raise HTTPException(
            status_code=400,
            detail=f"Error creating user: {str(e)}"
        ) # Return error response

    # Send approval request to admin
    admin_email_body = f"""
    <!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 0;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 600px;
            margin: 20px auto;
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            background-color: #0068B3;
            color: white;
            padding: 20px;
            margin: -30px -30px 20px -30px;
            border-radius: 8px 8px 0 0;
            text-align: center;
        }}
        h1 {{
            margin: 0;
            font-size: 28px;
            font-weight: bold;
        }}
        h2 {{
            color: #0068B3;
            font-size: 22px;
            margin-top: 20px;
        }}
        .content {{
            padding: 20px 0;
        }}
        .info-item {{
            margin: 10px 0;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 4px;
        }}
        .button {{
            display: inline-block;
            background-color: #0068B3;
            color: white;
            padding: 12px 24px;
            border-radius: 4px;
            margin: 20px 0;
            transition: background-color 0.3s ease;
            color:white
        }}
        .button:hover {{
            background-color: #005291;
        }}
        .token {{
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            font-family: monospace;
            margin: 10px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Sysco Pulse</h1>
        </div>
        <div class="content">
            <h2>New Account Creation Request</h2>
            <p>Please review and approve the following account:</p>

            <div class="info-item">
                <strong>Email:</strong> {user.email}
            </div>
            <div class="info-item">
                <strong>Employee ID:</strong> {user.employee_id}
            </div>
            <div class="info-item">
                <strong>Company Position:</strong> {user.company_position}
            </div>

            <p>To approve this account, click the button below:</p>
            <a href="https://codefest-backend.azurewebsites.net/user/admin/approve-account?token={approval_token}&approved=true" class="button">
                Approve Account
            </a>

            <p>Or use the approval token:</p>
            <div class="token">
                {approval_token}
            </div>
        </div>
    </div>
</body>
</html>
    """

    send_email(
        ADMIN_EMAIL,
        f"Account Creation Approval Required - {user.email}",
        admin_email_body
    )

    return {"message": "Account creation request sent to admin for approval"}

@router.post("/admin/approve-account")
@router.get("/admin/approve-account")
async def approve_account(
        token: str = Query(None, alias="token"), #  Update the token parameter
        approved: bool = Query(None, alias="approved"), # Update the approved parameter
        approval: AdminApproval | None = None # Update the approval parameter
):
    """Admin approves or denies account creation"""

    # Use either query parameters or body data
    final_token = token if token else (approval.approval_token if approval else None) # Update the token
    final_approved = approved if approved is not None else (approval.approved if approval else None) # Update the approved

    # Validate required data
    if not final_token or final_approved is None: # Check for required data
        raise HTTPException(
            status_code=400,
            detail="Missing required parameters. Provide either query parameters or request body."
        ) # Return error response

    with driver.session() as session: # Create a new session
        user = session.execute_read(get_user_by_token, final_token)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="Invalid approval token"
            ) # Return error response

        if final_approved: # Check if account is approved
            # Generate unique secure password for each user
            temp_password = generate_secure_password()

            # Activate user account with encrypted password
            session.execute_write(activate_user, user['email'], temp_password)

            # Send credentials to user
            user_email_body = f"""
           <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            line-height: 1.6;
                            margin: 0;
                            padding: 0;
                            background-color: #f5f5f5;
                        }}
                        .container {{
                            max-width: 600px;
                            margin: 20px auto;
                            background-color: white;
                            padding: 30px;
                            border-radius: 8px;
                            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                        }}
                        .header {{
                            background-color: #0068B3;
                            color: white;
                            padding: 20px;
                            margin: -30px -30px 20px -30px;
                            border-radius: 8px 8px 0 0;
                            text-align: center;
                        }}
                        h1 {{
                            margin: 0;
                            font-size: 28px;
                            font-weight: bold;
                        }}
                        h2 {{
                            color: #0068B3;
                            font-size: 22px;
                            margin-top: 20px;
                        }}
                        .content {{
                            padding: 20px 0;
                        }}
                        .info-item {{
                            margin: 10px 0;
                            padding: 10px;
                            background-color: #f8f9fa;
                            border-radius: 4px;
                        }}
                        .credentials-box {{
                            background-color: #f8f9fa;
                            border-left: 4px solid #0068B3;
                            padding: 15px;
                            margin: 20px 0;
                            border-radius: 0 4px 4px 0;
                        }}
                        .important-note {{
                            color: #721c24;
                            background-color: #f8d7da;
                            padding: 10px;
                            border-radius: 4px;
                            margin: 20px 0;
                        }}
                        .login-link {{
                            display: inline-block;
                            background-color: #0068B3;
                            color: white;
                            padding: 12px 24px;
                            text-decoration: none;
                            border-radius: 4px;
                            margin: 20px 0;
                            transition: background-color 0.3s ease;
                        }}
                        .login-link:hover{{
                            background-color: #005291;
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>Sysco Pulse</h1>
                        </div>
                        <div class="content">
                            <h2>Welcome to Sysco Pulse</h2>
                            <p>Your account has been approved and is ready to use. Please find your login credentials below:</p>
                
                            <div class="credentials-box">
                                <div class="info-item">
                                    <strong>Email:</strong> {user['email']}
                                </div>
                                <div class="info-item">
                                    <strong>Password:</strong> {temp_password}
                                </div>
                            </div>
                            <p>If you have any questions or need assistance, please contact your system administrator.</p>
                        </div>
                    </div>
                </body>
                </html>
            """

            send_email(
                user['email'],
                "Welcome to SyscoPulse - Account Approved",
                user_email_body
            )

            return {"message": "Account approved and credentials sent"}
        else:
            # Handle account denial
            return {"message": "Account denied"}


@router.post("/forgot-password")
async def forgot_password(request: ForgotPassword): # Update the function signature
    """User initiates forgot password process"""

    reset_token = secrets.token_urlsafe(32) # Generate reset token

    with driver.session() as session:
        user = session.execute_read(get_user, request.email) # Find user by email
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            ) # Return error response

        # Store reset token
        session.execute_write(set_password_reset_token, request.email, reset_token) # Set password reset token

        # Send approval request to admin
        admin_email_body = f"""
        <!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: Arial, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 0;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 600px;
            margin: 20px auto;
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            background-color: #0068B3;
            color: white;
            padding: 20px;
            margin: -30px -30px 20px -30px;
            border-radius: 8px 8px 0 0;
            text-align: center;
        }}
        h1 {{
            margin: 0;
            font-size: 28px;
            font-weight: bold;
        }}
        h2 {{
            color: #0068B3;
            font-size: 22px;
            margin-top: 20px;
        }}
        .content {{
            padding: 20px 0;
        }}
        .info-item {{
            margin: 10px 0;
            padding: 10px;
            background-color: #f8f9fa;
            border-radius: 4px;
        }}
        .button {{
            display: inline-block;
            background-color: #0068B3;
            color: white;
            padding: 12px 24px;
            border-radius: 4px;
            margin: 20px 0;
            transition: background-color 0.3s ease;
            text-decoration: none;
        }}
        .button:hover {{
            background-color: #005291;
        }}
        .token {{
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: 4px;
            font-family: monospace;
            margin: 10px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Sysco Pulse</h1>
        </div>
        <div class="content">
            <h2>Password Reset Request</h2>
            <p>Please review and approve the password reset request for:</p>

            <div class="info-item">
                <strong>Email:</strong> {user['email']}
            </div>
            <div class="info-item">
                <strong>Employee ID:</strong> {user['employee_id']}
            </div>
            <div class="info-item">
                <strong>Company Position:</strong> {user['company_position']}
            </div>

            <p>To approve this reset, click the button below:</p>
            <a href="https://codefest-backend.azurewebsites.net/user/admin/reset-password?token={reset_token}&action=approve" class="button">
                Approve Reset
            </a>

            <p>Or use the reset token:</p>
            <div class="token">
                {reset_token}
            </div>
        </div>
    </div>
</body>
</html>
        """

        send_email(
            ADMIN_EMAIL,
            f"Password Reset Approval Required - {user['email']}",
            admin_email_body
        )

        # Send confirmation to user
        user_email_body = """
        <html>
            <body>
                <h2>Password Reset Request Received</h2>
                <p>We have received your password reset request. An administrator will review and process your request shortly.</p>
            </body>
        </html>
        """

        send_email(
            user['email'],
            "Password Reset Request Received",
            user_email_body
        )

        return {"message": "Password reset request sent to admin"}


# Add this Neo4j function
def reset_user_password(tx, reset_token: str, new_password: str): # Update the function signature
    encrypted_password = encrypt_password(new_password) # Encrypt new password
    query = """
    MATCH (u:User {reset_token: $reset_token})
    SET u.password = $password,
        u.reset_token = null
    RETURN u
    """
    result = tx.run(query, reset_token=reset_token, password=encrypted_password) # Update password and clear token
    record = result.single() # Get single record
    return record['u'] if record else None # Return user if found


# Add this endpoint
@router.post("/admin/reset-password")
@router.get("/admin/reset-password")
async def reset_password(
        token: str = Query(None, alias="token"), # Update the token parameter
        action: str = Query(None, alias="action") # Update the action parameter
):
    """Admin approves and processes password reset"""
    if not token or not action:
        raise HTTPException(
            status_code=400,
            detail="Missing required parameters" # Return error response
        )

    if action.lower() != "approve":
        raise HTTPException(
            status_code=400,
            detail="Invalid action"
        ) # Return error response

    # Generate new secure password
    new_password = generate_secure_password() # Generate new password

    with driver.session() as session:
        user = session.execute_write(reset_user_password, token, new_password) # Reset user password

        if not user:
            raise HTTPException(
                status_code=404,
                detail="Invalid reset token"
            ) # Return error response

        # Send new credentials to user
        user_email_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    margin: 0;
                    padding: 0;
                    background-color: #f5f5f5;
                }}
                .container {{
                    max-width: 600px;
                    margin: 20px auto;
                    background-color: white;
                    padding: 30px;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
                }}
                .header {{
                    background-color: #0068B3;
                    color: white;
                    padding: 20px;
                    margin: -30px -30px 20px -30px;
                    border-radius: 8px 8px 0 0;
                    text-align: center;
                }}
                .credentials-box {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #0068B3;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 0 4px 4px 0;
                }}
                .info-item {{
                    margin: 10px 0;
                    padding: 10px;
                    background-color: #f8f9fa;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Sysco Pulse</h1>
                </div>
                <div class="content">
                    <h2>Password Reset Complete</h2>
                    <p>Your password has been reset. Please find your new login credentials below:</p>

                    <div class="credentials-box">
                        <div class="info-item">
                            <strong>Email:</strong> {user['email']}
                        </div>
                        <div class="info-item">
                            <strong>New Password:</strong> {new_password}
                        </div>
                    </div>

                    <p>Please change your password after logging in.</p>
                    <p>If you did not request this password reset, please contact your administrator immediately.</p>
                </div>
            </div>
        </body>
        </html>
        """

        send_email(
            user['email'],
            "Password Reset Complete - Sysco Pulse",
            user_email_body
        )

        return {"message": "Password reset successful and new credentials sent"}

# Add this endpoint
@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()): # Update the function signature
    """User login endpoint"""

    with driver.session() as session:
        user = session.execute_read(get_user, form_data.username) # Find user by email

        if not user or user['status'] != 'active': # Check user status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials or inactive account"
            )

        if form_data.password != user['password']: # Compare passwords
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        return {
            "detail": "true"
        }