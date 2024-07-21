import json
import smtplib
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Form, UploadFile, File, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, field_validator
from typing import List, Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.audio import MIMEAudio
from email import encoders
from email.utils import formatdate
import mimetypes
import os
from minio import Minio
from minio.error import S3Error
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

def load_smtp_config():
    with open('smtp_config.json', 'r') as file:
        return json.load(file)

smtp_config = load_smtp_config()
API_KEY = smtp_config.get('api_key', 'your_api_key')

app = FastAPI(
    title=smtp_config.get('api_name'),
    description=smtp_config.get('api_description'),
    version="1.0.0",
    docs_url=None,  # Disable the default docs
    redoc_url=None,  # Disable the default redoc
    openapi_url="/openapi.json"
)

@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint():
    return app.openapi()

@app.get("/docs", include_in_schema=False)
async def get_documentation():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_ui_parameters={"displayRequestDuration": True},
        swagger_favicon_url=None
    )

@app.get("/redoc", include_in_schema=False)
async def redoc_documentation():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_favicon_url=None
    )

# API Key Dependency
api_key_header = APIKeyHeader(name="X-API-Key")

async def get_api_key(api_key_header: str = Depends(api_key_header)):
    if api_key_header != API_KEY:
        raise HTTPException(status_code=403, detail="Could not validate credentials")

class EmailRequest(BaseModel):
    recipient_email: str
    subject: str
    body: str
    body_type: Optional[str] = "plain"  # Added to specify "plain" or "html"
    debug: Optional[bool] = False

    @field_validator('recipient_email')
    def validate_email(cls, v):
        if len(v) > 64:
            raise ValueError('Email address must be less than 64 characters')
        return v

    @field_validator('subject')
    def validate_subject(cls, v):
        if len(v) > 255:
            raise ValueError('Subject must be less than 255 characters')
        return v

    @field_validator('body')
    def validate_body(cls, v):
        if len(v) > 2000:
            raise ValueError('Body content must be less than 2000 characters')
        return v

    @field_validator('body_type')
    def validate_body_type(cls, v):
        if v not in ["plain", "html"]:
            raise ValueError('Body type must be either "plain" or "html"')
        return v

# Initialize MinIO client
minio_client = Minio(
    smtp_config.get('minio_server', "localhost:9000"),
    access_key=smtp_config.get('minio_access_key', "minioadmin"),
    secret_key=smtp_config.get('minio_secret_key', "minioadmin"),
    secure=smtp_config.get('minio_secure', False),
)

def save_email_result(email_id: str, status: str, detail: str, client_ip: str, headers: dict, message_length: int):
    # Remove sensitive headers
    headers.pop("x-api-key", None)

    date_str = datetime.now().strftime("%Y-%m-%d")
    status_dir = "success" if status == "success" else "failure"
    dir_path = os.path.join("data", date_str, status_dir)
    os.makedirs(dir_path, exist_ok=True)

    result = {
        "email_id": email_id,
        "status": status,
        "detail": detail,
        "timestamp": datetime.now().isoformat(),
        "client_ip": client_ip,
        "headers": headers,
        "message_length": message_length
    }

    with open(os.path.join(dir_path, f"{email_id}.json"), "w") as f:
        json.dump(result, f, indent=4)

def save_debug_email(email_id: str, message: MIMEMultipart):
    date_str = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join("data", date_str, "debug")
    os.makedirs(dir_path, exist_ok=True)

    with open(os.path.join(dir_path, f"{email_id}_email.txt"), "w") as f:
        f.write(message.as_string())

def upload_to_minio(file: UploadFile):
    bucket_name = "emails"
    object_name = f"{uuid.uuid4()}_{file.filename}"

    # Check the file size by reading it in chunks
    max_size = 2 * 1024 * 1024  # 2MB
    current_size = 0

    for chunk in file.file:
        current_size += len(chunk)
        if current_size > max_size:
            raise HTTPException(status_code=400, detail="Attachments must be smaller than 2MB.")

    file.file.seek(0)  # Reset file pointer after reading

    minio_client.put_object(
        bucket_name,
        object_name,
        file.file,
        length=-1,
        part_size=10*1024*1024
    )

    return object_name

def add_attachment(object_name: str):
    bucket_name = "emails"
    response = minio_client.get_object(bucket_name, object_name)
    file_content = response.read()
    filename = object_name.split("_", 1)[1]
    ctype, encoding = mimetypes.guess_type(filename)
    if ctype is None or encoding is not None:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split('/', 1)

    if maintype == "text":
        attachment = MIMEText(file_content.decode('utf-8'), _subtype=subtype)
    elif maintype == "image":
        attachment = MIMEImage(file_content, _subtype=subtype)
    elif maintype == "audio":
        attachment = MIMEAudio(file_content, _subtype=subtype)
    else:
        attachment = MIMEBase(maintype, subtype)
        attachment.set_payload(file_content)
        encoders.encode_base64(attachment)

    attachment.add_header('Content-Disposition', 'attachment', filename=filename)
    return attachment

def send_email_task(email_request: EmailRequest, email_id: str, client_ip: str, headers: dict, attachment_names: List[str]):
    try:
        message = MIMEMultipart()
        message["From"] = smtp_config["sender_email"]
        message["To"] = email_request.recipient_email
        message["Subject"] = email_request.subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = f"<{email_id}@{smtp_config['sender_domain']}>"

        message.attach(MIMEText(email_request.body, email_request.body_type))

        if attachment_names:
            for object_name in attachment_names:
                if object_name:  # Ensure object_name is not None
                    attachment_part = add_attachment(object_name)
                    message.attach(attachment_part)

        message_length = len(message.as_string())

        if smtp_config["use_ssl"]:
            with smtplib.SMTP_SSL(smtp_config["smtp_server"], smtp_config["smtp_port"]) as server:
                if smtp_config["use_password"]:
                    server.login(smtp_config["sender_email"], smtp_config["sender_password"])
                server.sendmail(smtp_config["sender_email"], email_request.recipient_email, message.as_string())
        else:
            with smtplib.SMTP(smtp_config["smtp_server"], smtp_config["smtp_port"]) as server:
                if smtp_config.get("use_tls"):
                    server.starttls()
                if smtp_config["use_password"]:
                    server.login(smtp_config["sender_email"], smtp_config["sender_password"])
                server.sendmail(smtp_config["sender_email"], email_request.recipient_email, message.as_string())

        save_email_result(email_id, "success", "Email sent successfully", client_ip, headers, message_length)

        if email_request.debug:
            save_debug_email(email_id, message)
    
    except smtplib.SMTPAuthenticationError:
        save_email_result(email_id, "failure", "Authentication failed. Check your username and password.", client_ip, headers, 0)
    except smtplib.SMTPConnectError:
        save_email_result(email_id, "failure", "Failed to connect to the SMTP server.", client_ip, headers, 0)
    except smtplib.SMTPRecipientsRefused:
        save_email_result(email_id, "failure", "Recipient address rejected by the server.", client_ip, headers, 0)
    except smtplib.SMTPSenderRefused:
        save_email_result(email_id, "failure", "Sender address rejected by the server.", client_ip, headers, 0)
    except smtplib.SMTPDataError:
        save_email_result(email_id, "failure", "The SMTP server refused to accept the message data.", client_ip, headers, 0)
    except smtplib.SMTPException as e:
        save_email_result(email_id, "failure", f"An SMTP error occurred: {e}", client_ip, headers, 0)
    except Exception as e:
        save_email_result(email_id, "failure", f"An unexpected error occurred: {e}", client_ip, headers, 0)

@app.post("/mail/send-with-attachments")
async def send_email_with_attachments(
    background_tasks: BackgroundTasks,
    request: Request,
    recipient_email: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    body_type: str = Form("plain"),  # Accepting body type for HTML or plain text
    debug: bool = Form(False),
    attachments: List[UploadFile] = File(None),
    api_key: str = Depends(get_api_key)
):
    email_id = str(uuid.uuid4())
    client_ip = request.headers.get("x-real-ip") or request.client.host
    headers = dict(request.headers)
    email_request = EmailRequest(
        recipient_email=recipient_email,
        subject=subject,
        body=body,
        body_type=body_type,
        debug=debug
    )
    # Validate attachments count and size
    if attachments and len(attachments) > 2:
        raise HTTPException(status_code=400, detail="You can only upload up to 2 attachments.")
    
    attachment_names = []
    if attachments:
        for attachment in attachments:
            # Validate attachment size by reading in chunks
            max_size = 2 * 1024 * 1024  # 2MB
            current_size = 0

            for chunk in attachment.file:
                current_size += len(chunk)
                if current_size > max_size:
                    raise HTTPException(status_code=400, detail="Attachments must be smaller than 2MB.")

            attachment.file.seek(0)  # Reset file pointer after reading

            object_name = upload_to_minio(attachment)
            attachment_names.append(object_name)

    background_tasks.add_task(send_email_task, email_request, email_id, client_ip, headers, attachment_names)
    return {"message": "Email is being sent in the background", "email_id": email_id}

@app.post("/mail/send")
async def send_email_json(
    background_tasks: BackgroundTasks,
    request: Request,
    email_request: EmailRequest,
    api_key: str = Depends(get_api_key)
):
    email_id = str(uuid.uuid4())
    client_ip = request.headers.get("x-real-ip") or request.client.host
    headers = dict(request.headers)

    # No attachments handling in this endpoint

    background_tasks.add_task(send_email_task, email_request, email_id, client_ip, headers, [])
    return {"message": "Email is being sent in the background", "email_id": email_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
