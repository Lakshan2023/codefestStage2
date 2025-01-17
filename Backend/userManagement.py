from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from typing import Optional
import jwt
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from neo4j import GraphDatabase
import uvicorn
import os
import secrets

# Initialize FastAPI app
app = FastAPI()

# Neo4j setup
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USERNAME = "neo4j"
NEO4J_PASSWORD = "codefest@123"
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

# Security setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
SECRET_KEY = "codefest@123"
ALGORITHM = "HS256"

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = "boxr880@gmail.com"
SMTP_PASSWORD = "dlud oevn lalj zwlm"
ADMIN_EMAIL = "boxr880@gmail.com"


# Pydantic models
class UserCreate(BaseModel):
    email: EmailStr
    employee_id: str
    company_position: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ForgotPassword(BaseModel):
    email: EmailStr


class AdminApproval(BaseModel):
    approval_token: str
    approved: bool


# Neo4j query functions
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
           company_position=company_position, approval_token=approval_token)


def get_user(tx, email: str):
    query = """
    MATCH (u:User {email: $email})
    RETURN u
    """
    result = tx.run(query, email=email)
    record = result.single()
    return record['u'] if record else None


def get_user_by_token(tx, token: str):
    query = """
    MATCH (u:User {approval_token: $token})
    RETURN u
    """
    result = tx.run(query, token=token)
    record = result.single()
    return record['u'] if record else None


def activate_user(tx, email: str, password: str):
    query = """
    MATCH (u:User {email: $email})
    SET u.password = $password,
        u.status = 'active',
        u.is_active = true,
        u.approval_token = null
    """
    tx.run(query, email=email, password=password)


def update_password(tx, email: str, new_password: str):
    query = """
    MATCH (u:User {email: $email})
    SET u.password = $new_password
    """
    tx.run(query, email=email, new_password=new_password)


def set_password_reset_token(tx, email: str, reset_token: str):
    query = """
    MATCH (u:User {email: $email})
    SET u.reset_token = $reset_token
    """
    tx.run(query, email=email, reset_token=reset_token)


# Helper functions
def send_email(to_email: str, subject: str, body: str):
    msg = MIMEMultipart()
    msg['From'] = SMTP_USERNAME
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)

# API Endpoints
@app.post("/admin/create-account")
async def create_account(user: UserCreate):
    """Admin initiates account creation"""

    approval_token = secrets.token_urlsafe(32)

    try:
        with driver.session() as session:
            existing_user = session.execute_read(get_user, user.email)
            if existing_user:
                raise HTTPException(
                    status_code=400,
                    detail="User already exists"
                )

            # Create pending user
            session.execute_write(create_pending_user,
                                  user.email,
                                  user.employee_id,
                                  user.company_position,
                                  approval_token)

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error creating user: {str(e)}"
        )

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
            <a href="http://localhost:8000/admin/approve-account?token={approval_token}&action=approve" class="button">
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


@app.post("/admin/approve-account")
async def approve_account(approval: AdminApproval):
    """Admin approves or denies account creation"""

    with driver.session() as session:
        user = session.execute_read(get_user_by_token, approval.approval_token)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="Invalid approval token"
            )

        if approval.approved:
            # Generate temporary password
            temp_password = f"Sysco{user['employee_id']}!"

            # Activate user account
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
                    <strong>Temporary Password:</strong> {temp_password}
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
                "Welcome to Sysco Labs - Account Approved",
                user_email_body
            )

            return {"message": "Account approved and credentials sent"}
        else:
            # TODO: Implement account denial logic
            return {"message": "Account denied"}


@app.post("/forgot-password")
async def forgot_password(request: ForgotPassword):
    """User initiates forgot password process"""

    reset_token = secrets.token_urlsafe(32)

    with driver.session() as session:
        user = session.execute_read(get_user, request.email)
        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )

        # Store reset token
        session.execute_write(set_password_reset_token, request.email, reset_token)

        # Send approval request to admin
        admin_email_body = f"""
        <html>
            <body>
                <h2>Password Reset Request</h2>
                <p>Please review and approve the password reset request for:</p>
                <p>Email: {user['email']}</p>
                <p>Employee ID: {user['employee_id']}</p>
                <p>Company Position: {user['company_position']}</p>
                <p>To approve this reset, click the following link:</p>
                <p><a href="http://localhost:8000/admin/reset-password?token={reset_token}&action=approve">Approve Reset</a></p>
                <p>Or use the reset token: {reset_token}</p>
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


@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """User login endpoint"""

    with driver.session() as session:
        user = session.execute_read(get_user, form_data.username)

        if not user or user['status'] != 'active':
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials or inactive account"
            )

        if form_data.password != user['password']:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        return {
            "detail": "true"
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)