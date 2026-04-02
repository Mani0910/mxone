# hostname = "10.211.37.47"
# username = "mxone_admin"
# password = "mxoneadmin"
# sudo_password = "mxoneroot"


# EMAIL_ENABLED=True
# SMTP_SERVER="smtp.mitel.com"
# SMTP_PORT=587
# SENDER_EMAIL="mekala.manikanta@mitel.com"
# SENDER_PASSWORD="Hitmanabd45"
# RECIPIENTS="mekala.manikanta@mitel.com"


import os
from dotenv import load_dotenv

load_dotenv(override=True)

hostname = os.getenv("HOSTNAME")
username = os.getenv("USERNAME")
password = os.getenv("PASSWORD")
sudo_password = os.getenv("SUDO_PASSWORD")

EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "False") == "True"
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECIPIENTS = os.getenv("RECIPIENTS").split(",") if os.getenv("RECIPIENTS") else []