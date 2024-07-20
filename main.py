import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import smtplib
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

@app.post("/send-email")
async def send_email(email_request: EmailRequest):
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
                if smtp_config["use_tls"]:
                    server.starttls()
                if smtp_config["use_password"]:
                    server.login(smtp_config["sender_email"], smtp_config["sender_password"])
                server.sendmail(smtp_config["sender_email"], email_request.recipient_email, message.as_string())

        return {"message": "Email sent successfully"}
    
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=401, detail="Authentication failed. Check your username and password.")
    except smtplib.SMTPConnectError:
        raise HTTPException(status_code=500, detail="Failed to connect to the SMTP server.")
    except smtplib.SMTPRecipientsRefused:
        raise HTTPException(status_code=400, detail="Recipient address rejected by the server.")
    except smtplib.SMTPSenderRefused:
        raise HTTPException(status_code=400, detail="Sender address rejected by the server.")
    except smtplib.SMTPDataError:
        raise HTTPException(status_code=500, detail="The SMTP server refused to accept the message data.")
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=500, detail=f"An SMTP error occurred: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
