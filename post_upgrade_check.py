from http import client
from importlib.metadata import files
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import paramiko
import socket
import logging
import re
import datetime
import sys
import signal
import smtplib

from requests import delete
from config import hostname, username, password

# Force UTF-8 output encoding
sys.stdout.reconfigure(encoding='utf-8')

# Logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Global flag for Ctrl+C handling
interrupted = False

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    global interrupted
    interrupted = True
    print("USER INTERRUPTED - Process stopped by user")
    sys.exit(0)

# Register signal handler for Ctrl+C
signal.signal(signal.SIGINT, signal_handler)


# ================= SSH CONNECTION =================
def connect_ssh():
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        client.connect(
            hostname=hostname,
            username=username,
            password=password,
            port=22
        )

        logger.info("SSH Connected")
        return client

    except Exception as e:
        logger.error(f"Connection failed: {e}")
        return None


def execute_command(client, command):
    try:
        _, stdout, _ = client.exec_command(command)
        return stdout.read().decode()
    except Exception as e:
        logger.error(f"Command failed: {e}")
        return None


def execute_sudo_command(client, command, sudo_password, timeout=5):
    """Execute command, try without sudo first, then with sudo if needed"""
    try:
        # First try WITHOUT sudo (most commands don't need it)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')
            
            # If no permission error, return the output
            if "Permission denied" not in error and "Permission denied" not in output:
                return output if output else error
        except:
            pass  # If that fails, try with sudo below
        
        # If we need sudo, use channel with PTY
        transport = client.get_transport()
        channel = transport.open_session()
        channel.settimeout(timeout)
        
        # Get PTY for proper terminal output
        channel.get_pty()
        
        # Execute sudo command with -S for stdin password
        channel.exec_command(f"sudo -S bash -c '{command}'")
        
        # Send password immediately
        channel.send(f"{sudo_password}\n")
        
        # Read all output
        output = b""
        try:
            while True:
                chunk = channel.recv(4096)
                if not chunk:
                    break
                output += chunk
        except socket.timeout:
            pass  # Timeout is OK, just get what we have
        except:
            pass
        
        channel.close()
        result = output.decode('utf-8', errors='ignore')
        
        # Check for password errors
        if "Sorry, try again" in result or "incorrect password" in result.lower():
            logger.warning(f"Sudo password rejected - this might indicate wrong password")
        
        return result
    except Exception as e:
        logger.error(f"Command execution failed: {e}")
        return None

# ================= CHECKS =================
def check_ts_about(client):
    logger.info("Current Version Check:")

    output = execute_command(client, "ts_about")

    if not output:
        print("ts_about: No output received")
        return False

    # Always print full ts_about output
    print(output)

    # Extract version
    match = re.search(r"Version:\s*([\d\.]+)", output)
    if not match:
        print("ts_about: Version not found")
        return False

    version = match.group(1)
    print(f"Detected Version : {version}")

    major = int(version.split(".")[0])

    # Version < 8: no docker check needed
    if major < 8:
        print("Docker Check     : Not required (version < 8)")
        return True

    # Version >= 8: check docker ps
    print("\nDocker Check     : Required (version >= 8)")
    docker_output = execute_command(client, "docker ps -a")

    if not docker_output:
        print("Docker           : Command failed or no output")
        return False

    lines = docker_output.splitlines()
    if not lines:
        print("Docker           : No containers found")
        return False

    header = lines[0]
    container_rows = [line for line in lines[1:] if line.strip()]

    if not container_rows:
        print("Docker           : No containers found")
        return False

    # Case-insensitive check: status column must contain "up"
    down_containers = [line for line in container_rows if "up" not in line.lower()]
    up_containers   = [line for line in container_rows if "up" in line.lower()]

    print(f"\nDocker Containers: Total={len(container_rows)}  UP={len(up_containers)}  DOWN={len(down_containers)}")
    print()
    print(header)
    print("-" * len(header))
    for row in container_rows:
        status_tag = "  [UP]  " if "up" in row.lower() else "  [DOWN]"
        print(f"{status_tag}  {row}")

    if down_containers:
        print(f"\nDocker           : {len(down_containers)} container(s) NOT running:")
        print(header)
        print("-" * len(header))
        for row in down_containers:
            print(row)
        return False

    print("\nDocker           : All containers are UP")
    return True


def check_disk_usage(client, threshold=60):
    """
    Checks disk usage and returns rows where usage > threshold %
    """
    output = execute_command(client, "df -kh")

    if not output:
        return []

    result = []

    lines = output.splitlines()
    header_line = lines[0] if lines else ""

    # Skip header
    for line in lines[1:]:
        parts = line.split()

        if len(parts) < 6:
            continue

        filesystem = parts[0]
        size = parts[1]
        used = parts[2]
        avail = parts[3]
        use_percent = parts[4]
        mount = parts[5]

        try:
            usage = int(use_percent.replace('%', ''))
        except:
            continue

        if usage > threshold:
            result.append({
                "filesystem": filesystem,
                "size": size,
                "used": used,
                "available": avail,
                "usage_percent": use_percent,
                "mounted_on": mount,
                "raw": line
            })

    print("\n===== DISK USAGE CHECK =====\n")

    if not result:
        print("Looks good for enough disk to upgrade\n")
    else:
        print(f"WARNING: The following filesystems exceed {threshold}% usage:\n")
        print(f"{header_line}")
        print("-" * len(header_line))
        for entry in result:
            print(entry["raw"])
        print(f"\nNeed to maintain less than {threshold}% disk storage\n")

    return result


def check_swap_memory(client):
    logger.info("Checking Swap Memory...")

    output = execute_command(client, "free -mh")

    if not output:
        print("\n===== SWAP MEMORY CHECK =====\n")
        print("Swap Check: No output from free command\n")
        return False, "No output from free command"

    swap_line = None

    # ✅ Find Swap line
    for line in output.splitlines():
        if line.lower().startswith("swap"):
            swap_line = line
            break

    print("\n===== SWAP MEMORY CHECK =====\n")

    if not swap_line:
        print("Swap Check: Not available on this system\n")
        return False, "Swap memory not found"

    parts = swap_line.split()

    # Expected: Swap: total used free
    if len(parts) < 4:
        print(f"Swap Check: Invalid swap data - {swap_line}\n")
        return False, f"Invalid swap data: {swap_line}"

    total = parts[1]
    used = parts[2]
    free = parts[3]

    # ✅ Convert free swap to GB
    def convert_to_gb(value):
        if value.lower().endswith('g'):
            return float(value[:-1])
        elif value.lower().endswith('m'):
            return float(value[:-1]) / 1024
        elif value.lower().endswith('k'):
            return float(value[:-1]) / (1024 * 1024)
        else:
            return float(value)

    free_gb = convert_to_gb(free)

    # ✅ Check condition (at least 2GB free)
    if free_gb >= 1:
        print(f"Swap OK: Total={total}, Used={used}, Free={free}\n")
        return True, f"Swap OK: Total={total}, Used={used}, Free={free}"
    else:
        print(f"Swap LOW: Total={total}, Used={used}, Free={free}")
        print(f"(Required: >= 2G free)")
        print("Need to maintain minimum 2GB free swap memory\n")
        return False, (
            f"Swap LOW: Total={total}, Used={used}, Free={free} "
            f"(Required: >= 2G free)"
        )


def check_license_validity(client=None, output=None):
    """Validate MX-ONE license from CLI output"""
    logger.info("Checking License Validity")

    is_valid = True

    # -------------------------------
    # 1. Hardware ID Validation
    # -------------------------------
    hw_status = re.search(r"Status on hardware id:\s*([^\s]+)", output)
    hw_licensed = re.search(r"Licensed to hardware id\s*([^\s]+)", output)

    if hw_status and hw_licensed:
        if hw_status.group(1).lower() == hw_licensed.group(1).lower():
            print("Hardware Binding : OK")
        else:
            print("Hardware Binding : FAILED")
            is_valid = False
    else:
        print("Hardware Binding : NOT FOUND")
        is_valid = False

    # -------------------------------
    # 2. License File Check
    # -------------------------------
    if "License file sequence number" in output:
        print("License File     : PRESENT")
    else:
        print("License File     : MISSING")
        is_valid = False

    # -------------------------------
    # 3. Trial License Check
    # -------------------------------
    trial_matches = re.findall(r"\s(\d+)\s+\d+\s+\d+", output)

    if any(int(t) > 0 for t in trial_matches):
        print("License Type     : TRIAL")
    else:
        print("License Type     : PERMANENT")

    # -------------------------------
    # 4. Allowed License Check
    # -------------------------------
    allowed_matches = re.findall(r"\s\d+\s+(\d+)\s+\d+", output)

    if any(int(a) > 0 for a in allowed_matches):
        print("License Capacity : AVAILABLE")
    else:
        print("License Capacity : NONE")
        is_valid = False

    # -------------------------------
    # 5. Expiry Check (Optional)
    # -------------------------------
    match = re.search(r'EXPIRES-(\d{4}-\d{2}-\d{2})', output)

    if match:
        expiry = match.group(1)
        expiry_date = datetime.datetime.strptime(expiry, "%Y-%m-%d")
        today = datetime.datetime.now()

        days_left = (expiry_date - today).days

        print(f"Expiry Date      : {expiry}")

        if days_left > 0:
            print(f"Days Remaining   : {days_left} days")
        else:
            print("License Expired ")
            is_valid = False
    else:
        print("Expiry           : NOT APPLICABLE (Permanent License)")

    # -------------------------------
    # Final Status
    # -------------------------------
    print("\nFinal License Status :", "VALID" if is_valid else "INVALID")

    return is_valid


def check_alarms(client):
    """Check and display alarm details with headers."""
    logger.info("Checking alarms...")

    output = execute_command(client, "alarm -p")

    if not output:
        logger.warning("Failed to retrieve alarm status")
        return True

    has_issues = False
    header_line = ""
    separator_line = ""

    print("\n===== ALARM STATUS =====\n")

    for line in output.splitlines():
        line = line.strip()

        # Capture header
        if line.startswith("S N"):
            header_line = line
            continue

        if line.startswith("===="):
            separator_line = line
            continue

        # Skip unwanted lines
        if not line or line.startswith("Global"):
            continue

        parts = line.split()

        # Check valid alarm row
        if len(parts) > 0 and parts[0].isdigit():
            severity = int(parts[0])

            if severity in (3, 4):  # all severities
                # Print header once before first row
                if not has_issues:
                    print(header_line)
                    print(separator_line)

                print(line)
                has_issues = True

    if not has_issues:
        print("No alarms found\n")

    return not has_issues


def check_comfunc(client):
    logger.info("Checking Common Functions status...")

    output = execute_command(client, "mdsh -c status -comfunc")

    if not output:
        print("Common Functions: No output received")
        return False

    header = []
    issues = []
    capture_header = True

    for line in output.splitlines():
        line = line.rstrip()

        if not line or line.strip() == "END":
            continue

        parts = line.split()

        # ✅ Capture header
        if capture_header:
            header.append(line)
            if line.startswith("----"):
                capture_header = False
            continue

        # ✅ Process rows
        if len(parts) >= 6:
            unit = parts[0]
            state = parts[4]

            if unit == "Unit":
                continue

            if state.lower() != "ok":
                issues.append(line)

    # Case 1: all rows are OK
    if len(issues) == 0:
        print("Common Functions: Everything is OK")
        return True

    # Case 2: issues found, print header + problematic rows
    result = "\n".join(header + issues)
    print("\nCommon Functions: Issues found")
    print(result)
    return False


# ================= EMAIL NOTIFICATIONS =================
from config import EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD, RECIPIENTS


def get_email_template(title, status_color, content_html):
    """Generate a styled HTML email template."""
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f4f4;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width: 650px; margin: 20px auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
        <!-- Header -->
        <tr>
            <td style="background-color: #1e3a5f; padding: 30px; text-align: center; border-radius: 8px 8px 0 0;">
                <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 600;">
                    MX-ONE Upgrade Monitor
                </h1>
                <p style="color: #b8d4e8; margin: 10px 0 0 0; font-size: 14px;">Automated Health Check Report</p>
            </td>
        </tr>

        <!-- Status Banner -->
        <tr>
            <td style="padding: 0;">
                <table width="100%" cellspacing="0" cellpadding="0">
                    <tr>
                        <td style="background-color: {status_color}; padding: 15px 30px; text-align: center;">
                            <span style="color: #ffffff; font-size: 18px; font-weight: 600;">
                                {title}
                            </span>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>

        <!-- Content -->
        <tr>
            <td style="padding: 30px;">
                {content_html}
            </td>
        </tr>

        <!-- Footer -->
        <tr>
            <td style="background-color: #f8f9fa; padding: 20px 30px; border-radius: 0 0 8px 8px; border-top: 1px solid #e9ecef;">
                <p style="margin: 0; color: #6c757d; font-size: 12px; text-align: center;">
                    This is an automated message from MX-ONE Upgrade Monitor<br>
                    <span style="color: #adb5bd;">&copy; Mitel Networks Corporation</span>
                </p>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def send_email(subject, body, is_html=False):
    """Send email to all configured recipients."""
    if not EMAIL_ENABLED:
        print("[EMAIL] Email notifications disabled")
        return

    # Support both list-style and comma-separated string recipients.
    if isinstance(RECIPIENTS, str):
        recipients = [r.strip() for r in RECIPIENTS.split(",") if r.strip()]
    else:
        recipients = [str(r).strip() for r in RECIPIENTS if str(r).strip()]

    if not recipients:
        print("[EMAIL] No valid recipients configured")
        return

    for recipient in recipients:
        try:
            msg = MIMEMultipart()
            msg["From"] = SENDER_EMAIL
            msg["To"] = recipient
            msg["Subject"] = subject
            content_type = "html" if is_html else "plain"
            msg.attach(MIMEText(body, content_type))

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipient, msg.as_string())
            server.quit()

            print(f"[EMAIL SENT] -> {recipient}")

        except Exception as e:
            print(f"[EMAIL ERROR] {e}")


def build_check_row(check_name, result):
    """Build a single HTML table row for a check result."""
    if isinstance(result, tuple):
        ok = bool(result[0])
        status = "PASS" if ok else "FAIL"
        color = "#28a745" if ok else "#dc3545"
        icon = "&#10004;" if ok else "&#10008;"
    elif isinstance(result, list):
        # disk_usage returns list of issues: empty list means PASS
        has_issues = len(result) > 0
        status = "WARNING" if has_issues else "PASS"
        color = "#ffc107" if has_issues else "#28a745"
        icon = "&#9888;" if has_issues else "&#10004;"
    elif result is None or result is False:
        status = "FAIL"
        color = "#dc3545"
        icon = "&#10008;"
    else:
        status = "PASS"
        color = "#28a745"
        icon = "&#10004;"

    return f"""
    <tr>
        <td style="padding: 10px 15px; border-bottom: 1px solid #e9ecef; font-size: 14px;">{check_name}</td>
        <td style="padding: 10px 15px; border-bottom: 1px solid #e9ecef; text-align: center;">
            <span style="color: {color}; font-weight: 600; font-size: 14px;">{icon} {status}</span>
        </td>
    </tr>"""


def build_summary_email(phase, results, server_hostname):
    """Build full HTML summary email for post-upgrade checks."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    check_labels = {
        "ts_about": "Version Check (ts_about)",
        "license_status": "License Validity",
        "disk_usage": "Disk Usage",
        "swap_memory": "Swap Memory",
        "alarm": "Alarm Status",
        "comfunc": "Common Functions",
    }

    rows_html = ""
    pass_count = 0
    fail_count = 0
    warn_count = 0

    for key, result in results.items():
        label = check_labels.get(key, key)
        rows_html += build_check_row(label, result)

        if isinstance(result, tuple):
            if result[0]:
                pass_count += 1
            else:
                fail_count += 1
        elif isinstance(result, list):
            if len(result) > 0:
                warn_count += 1
            else:
                pass_count += 1
        elif result is None or result is False:
            fail_count += 1
        else:
            pass_count += 1

    total = pass_count + fail_count + warn_count
    overall_status = "ALL CHECKS PASSED" if fail_count == 0 else f"{fail_count} CHECK(S) FAILED"
    status_color = "#28a745" if fail_count == 0 else "#dc3545"

    stats_html = f"""
    <table width="100%" cellspacing="0" cellpadding="0" style="margin-bottom: 20px;">
        <tr>
            <td style="text-align: center; padding: 10px;">
                <span style="font-size: 28px; font-weight: 700; color: #28a745;">{pass_count}</span><br>
                <span style="font-size: 12px; color: #6c757d;">PASSED</span>
            </td>
            <td style="text-align: center; padding: 10px;">
                <span style="font-size: 28px; font-weight: 700; color: #ffc107;">{warn_count}</span><br>
                <span style="font-size: 12px; color: #6c757d;">WARNINGS</span>
            </td>
            <td style="text-align: center; padding: 10px;">
                <span style="font-size: 28px; font-weight: 700; color: #dc3545;">{fail_count}</span><br>
                <span style="font-size: 12px; color: #6c757d;">FAILED</span>
            </td>
        </tr>
    </table>
    """

    content_html = f"""
    <p style="color: #495057; font-size: 14px; margin: 0 0 5px 0;">
        <strong>Server:</strong> {server_hostname}
    </p>
    <p style="color: #495057; font-size: 14px; margin: 0 0 5px 0;">
        <strong>Phase:</strong> {phase}
    </p>
    <p style="color: #495057; font-size: 14px; margin: 0 0 20px 0;">
        <strong>Timestamp:</strong> {now}
    </p>

    {stats_html}

    <table width="100%" cellspacing="0" cellpadding="0" style="border: 1px solid #dee2e6; border-radius: 6px; overflow: hidden;">
        <tr style="background-color: #1e3a5f;">
            <th style="padding: 12px 15px; text-align: left; color: #ffffff; font-size: 14px;">Check</th>
            <th style="padding: 12px 15px; text-align: center; color: #ffffff; font-size: 14px; width: 100px;">Status</th>
        </tr>
        {rows_html}
    </table>

    <p style="margin: 20px 0 0 0; color: #6c757d; font-size: 12px; text-align: center;">
        Total checks: {total} | Passed: {pass_count} | Warnings: {warn_count} | Failed: {fail_count}
    </p>
    """

    title = f"{phase} - {overall_status}"
    email_html = get_email_template(title, status_color, content_html)
    return email_html, fail_count


# ================= MAIN =================

def main():
    client = connect_ssh()

    if not client:
        send_email(
            f"[ALERT] Post-Upgrade Check - SSH Connection Failed ({hostname})",
            get_email_template(
                "SSH Connection Failed",
                "#dc3545",
                f'<p style="color:#495057;font-size:14px;">Could not connect to <strong>{hostname}</strong>. Post-upgrade checks were not executed.</p>'
            ),
            is_html=True
        )
        return

    try:
        print("\n===== POST UPGRADE CHECK =====\n")

        sudo_password = password

        results = {
            "ts_about": check_ts_about(client),
            "license_status": check_license_validity(output=execute_command(client, "license_status")),
            "disk_usage": check_disk_usage(client),
            "swap_memory": check_swap_memory(client),
            "alarm": check_alarms(client),
            "comfunc": check_comfunc(client),
        }

        # Send summary report email
        print("\n===== SENDING POST-UPGRADE REPORT EMAIL =====\n")
        email_html, fail_count = build_summary_email("Post-Upgrade Check", results, hostname)
        status_tag = "PASSED" if fail_count == 0 else "FAILED"
        send_email(
            f"[{status_tag}] Post-Upgrade Check Report - {hostname}",
            email_html,
            is_html=True
        )

    finally:
        client.close()


if __name__ == "__main__":
    main()