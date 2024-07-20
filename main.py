import json
import smtplib
import uuid
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = FastAPI()

class EmailRequest(BaseModel):
    recipient_email: str
    subject: str
    body: str

def load_smtp_config():
    with open('smtp_config.json', 'r') as file:
        return json.load(file)

smtp_config = load_smtp_config()

def send_email_task(email_request: EmailRequest, email_id: str):
    try:
        # Create a multipart message and set headers
        message = MIMEMultipart()
        message["From"] = smtp_config["sender_email"]
        message["To"] = email_request.recipient_email
        message["Subject"] = email_request.subject

        message.attach(MIMEText(email_request.body, "plain"))

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
        print(f"Email {email_id} sent successfully")
    
    except smtplib.SMTPAuthenticationError:
        print(f"Email {email_id} failed: Authentication failed. Check your username and password.")
    except smtplib.SMTPConnectError:
        print(f"Email {email_id} failed: Failed to connect to the SMTP server.")
    except smtplib.SMTPRecipientsRefused:
        print(f"Email {email_id} failed: Recipient address rejected by the server.")
    except smtplib.SMTPSenderRefused:
        print(f"Email {email_id} failed: Sender address rejected by the server.")
    except smtplib.SMTPDataError:
        print(f"Email {email_id} failed: The SMTP server refused to accept the message data.")
    except smtplib.SMTPException as e:
        print(f"Email {email_id} failed: An SMTP error occurred: {e}")
    except Exception as e:
        print(f"Email {email_id} failed: An unexpected error occurred: {e}")

@app.post("/send-email")
async def send_email(email_request: EmailRequest, background_tasks: BackgroundTasks):
    email_id = str(uuid.uuid4())
    background_tasks.add_task(send_email_task, email_request, email_id)
    return {"message": "Email is being sent in the background", "email_id": email_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
