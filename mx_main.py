import upgrade_mxone
import pre_upgrade_check
import post_upgrade_check
import time
import paramiko
import sys
import re
import logging
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config import EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD, RECIPIENTS

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def _normalize_recipients(recipients):
        if isinstance(recipients, str):
                return [r.strip() for r in recipients.split(',') if r.strip()]
        return [str(r).strip() for r in recipients if str(r).strip()]


def send_email(subject, body, is_html=False):
        if not EMAIL_ENABLED:
                logging.info("Email notifications are disabled")
                return

        recipients = _normalize_recipients(RECIPIENTS)
        if not recipients:
                logging.warning("No valid email recipients configured")
                return

        for recipient in recipients:
                try:
                        msg = MIMEMultipart()
                        msg["From"] = SENDER_EMAIL
                        msg["To"] = recipient
                        msg["Subject"] = subject
                        msg.attach(MIMEText(body, "html" if is_html else "plain"))

                        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
                        server.starttls()
                        server.login(SENDER_EMAIL, SENDER_PASSWORD)
                        server.sendmail(SENDER_EMAIL, recipient, msg.as_string())
                        server.quit()
                        logging.info(f"Summary email sent to {recipient}")
                except Exception as e:
                        logging.error(f"Failed to send summary email to {recipient}: {e}")


def build_upgrade_summary_html(hostname, target_version, current_version, start_time, end_time, steps, run_checks=False):
        duration = str(end_time - start_time).split('.')[0]
        failures = [s for s in steps if s["status"] == "FAILED"]
        overall = "SUCCESS" if not failures else "FAILED"
        overall_color = "#28a745" if overall == "SUCCESS" else "#dc3545"

        rows = []
        for step in steps:
                color = "#28a745" if step["status"] == "SUCCESS" else "#dc3545"
                icon = "&#10004;" if step["status"] == "SUCCESS" else "&#10008;"
                rows.append(
                        f"""
                        <tr>
                                <td style=\"padding:10px;border-bottom:1px solid #e9ecef;\">{step['name']}</td>
                                <td style=\"padding:10px;border-bottom:1px solid #e9ecef;text-align:center;color:{color};font-weight:600;\">{icon} {step['status']}</td>
                                <td style=\"padding:10px;border-bottom:1px solid #e9ecef;\">{step['details']}</td>
                        </tr>
                        """
                )

        # Build the "What User Needs To Know" section based on whether checks were run
        checks_info = ""
        if run_checks:
                checks_info = "<li>Pre-upgrade checks and post-upgrade checks were executed from the existing script flow.</li>"
        else:
                checks_info = "<li>Pre-upgrade checks and post-upgrade checks were SKIPPED. To include them, add '--with-checks' flag.</li>"

        return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Segoe UI,Arial,sans-serif;background:#f4f6f8;padding:20px;">
    <table style="max-width:760px;margin:auto;background:#fff;border-radius:8px;border-collapse:collapse;box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden;">
        <tr><td style="background:#1e3a5f;color:#fff;padding:20px;text-align:center;font-size:24px;font-weight:700;">MX-ONE Upgrade Final Summary</td></tr>
        <tr><td style="background:{overall_color};color:#fff;padding:12px;text-align:center;font-weight:700;">OVERALL STATUS: {overall}</td></tr>
        <tr><td style="padding:20px;">
            <p><b>Server:</b> {hostname}</p>
            <p><b>Target Version:</b> {target_version}</p>
            <p><b>Current Version Before Upgrade:</b> {current_version}</p>
            <p><b>Start Time:</b> {start_time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p><b>End Time:</b> {end_time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p><b>Total Duration:</b> {duration}</p>
            <h3 style="margin-top:24px;">What Was Done</h3>
            <table style="width:100%;border-collapse:collapse;border:1px solid #dee2e6;">
                <tr style="background:#1e3a5f;color:#fff;">
                    <th style="padding:10px;text-align:left;">Step</th>
                    <th style="padding:10px;text-align:center;">Status</th>
                    <th style="padding:10px;text-align:left;">Details</th>
                </tr>
                {''.join(rows)}
            </table>
            <h3 style="margin-top:24px;">What User Needs To Know</h3>
            <ul>
                {checks_info}
                <li>If any step is marked FAILED, review server logs and rerun that stage.</li>
                <li>Validate service health using ts_about, alarm -p, and mdsh -c status -comfunc.</li>
            </ul>
        </td></tr>
    </table>
</body>
</html>
"""

class mx_upgrade:
    def __init__(self, hostname, username, password, sudo_password, port=22):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.sudo_password = sudo_password
        self.port = port

    def check_mxone_version(self):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(self.hostname, username=self.username, password=self.password,
                    timeout=1800)
        stdin, stdout, stderr = ssh.exec_command('ts_about')
        text = stdout.read().decode("utf-8")
        output = stdout.readlines()
        logging.info("Checking current version")
        match = re.search(r"Version:\s+([\d\.]+)", text)
        if match:
            version = match.group(1)  # Extract the first captured group
            logging.info(f"Current mxone Version: {version}")
            return version
        else:
            logging.error("Error in extracting the current version")
            return output

    def get_older_versions(self):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(self.hostname, username=self.username, password=self.password, timeout=1800)
        shell = ssh.invoke_shell()
        shell.send('su -l root\n')
        time.sleep(1)
        shell.send(self.sudo_password + '\n')
        time.sleep(2)
        shell.send('/opt/mxone_install/bin/package_handling --list\n')
        time.sleep(2)
        data = ""
        if shell.recv_ready():
            data = shell.recv(4096)
            time.sleep(0.5)
        ssh.close()
        console_data = data.decode('utf-8', errors='ignore')

        pattern = r"Older version\(s\):([\s\S]*?)\nNewer version\(s\):"
        match = re.search(pattern, console_data)
        if not match:
            logging.info("No older versions found.")
            return []
        section = match.group(1)
        versions = []
        versions = re.findall(r"\(([\d\.]+)\)", section)
        logging.info(f"Older MXONE package versions: {versions}")
        return versions

    def delete_folders(self, folder_list):
        if len(folder_list) == 0:
            logging.info("No older version to delete.")
            return
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(self.hostname, username=self.username, password=self.password, timeout=1800)
        shell = ssh.invoke_shell()
        shell.send('su -l root\n')
        time.sleep(1)
        shell.send(self.sudo_password + '\n')
        time.sleep(2)
        base_dirs = [
            '/opt/mxone_install/',
            '/opt/mxone_snm_install/',
            '/opt/mxone_pm_install/'
        ]
        for folder in folder_list:
            for base_dir in base_dirs:
                full_path = f"{base_dir}{folder}"
                cmd = f"rm -rf {full_path}\n"
                shell.send(cmd)
                logging.info(f"Sent delete command: {cmd.strip()}")
                time.sleep(1)
        shell.send('exit\n')
        ssh.close()
        logging.info("Folder deletion completed.")

    def build_path(self, build_version):
        parts = build_version.split('.')
        build_file = f"MX-ONE_{parts[0]}.{parts[1]}.sp{parts[2]}.hf{parts[3]}.rc{parts[4]}.bin"
        build_url = 'http://10.105.64.17/GIT/Release/refs/tags/mx' + build_version + '/install/' + build_file
        logging.info(f'Build URL: {build_url}')
        return build_url, build_file

if __name__ == "__main__":
    if len(sys.argv) < 6:
        logging.error("Usage: python script.py <hostname> <username> <password> <sudo_password> <version> [--with-checks] [<standalone_pm> <st_hostname> <st_username> <st_password> <st_sudo_password>]")
        sys.exit(1)
    
    hostname = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3]
    sudo_password = sys.argv[4]
    version = sys.argv[5]
    
    # Check if pre/post upgrade checks should be run
    run_checks = '--with-checks' in sys.argv
    
    # Find where standalone PM args start (accounting for optional --with-checks flag)
    standalone_pm_index = 6
    if run_checks:
        standalone_pm_index = 7

    start_time = datetime.datetime.now()
    steps = []

    def add_step(name, status, details):
        steps.append({"name": name, "status": status, "details": details})

    current_version = "UNKNOWN"

    try:
        logging.info(f'Expected version to upgrade: {version}')
        logging.info(f'Run pre/post checks: {run_checks}')

        if run_checks:
            logging.info("Starting pre-upgrade checks")
            pre_upgrade_check.main()
            add_step("Pre-Upgrade Checks", "SUCCESS", "Pre-upgrade checks executed")

        mx = mx_upgrade(hostname, username, password, sudo_password)
        current_version = mx.check_mxone_version()
        add_step("Read Current Version", "SUCCESS", f"Detected {current_version}")

        older_versions = mx.get_older_versions()
        logging.info("delete older builds starts")
        mx.delete_folders(older_versions)
        logging.info("delete older builds ends")
        add_step("Cleanup Older Versions", "SUCCESS", f"Older versions found: {len(older_versions)}")

        if current_version == version:
            add_step("Upgrade Execution", "SUCCESS", "No upgrade needed; already on target version")
            logging.info("Current version matches the expected version. No upgrade needed.")
        else:
            file_url, file_name = mx.build_path(version)
            destination = '/local/home/mxone_admin/' + file_name
            logging.info(f'Complete file path: {destination}')
            add_step("Build Path Preparation", "SUCCESS", f"Build URL prepared: {file_url}")

            up = upgrade_mxone.SystemUpgradeManager(hostname, username, password, sudo_password)
            distribute_cmd = 'sh ' + destination + ' --package_distribute'
            prepare_cmd = 'sh /opt/mxone_install/' + version + '/target/install_scripts/sn_install_main.sh "upgrade_prepare" "" "" "silent"'
            upgrade_cmd = 'sh /opt/mxone_install/' + version + '/target/install_scripts/sn_install_main.sh "upgrade" "" "" "silent"'
            upgrade_pm_cmd = 'sh /opt/mxone_install/' + version + '/addon_sw/mxone_pm_install-' + version + '.bin'
            upgrade_pm_location = '/opt/mxone_install/' + version + '/addon_sw/mxone_pm_install-' + version + '.bin'

            logging.info(f'Distribute command: {distribute_cmd}')
            logging.info(f'Prepare command: {prepare_cmd}')
            logging.info(f'Upgrade command: {upgrade_cmd}')
            logging.info(f'Upgrade PM command: {upgrade_pm_cmd}')

            up.distribute_builds(distribute_cmd)
            add_step("Package Distribute", "SUCCESS", "Distribution completed")

            up.prepare_builds(prepare_cmd)
            add_step("Upgrade Prepare", "SUCCESS", "Prepare completed")

            up.perform_upgrade(upgrade_cmd)
            add_step("Service Node Upgrade", "SUCCESS", "SN upgrade completed")

            if len(sys.argv) > standalone_pm_index and sys.argv[standalone_pm_index].lower() == 'true':
                st_hostname = sys.argv[standalone_pm_index + 1]
                st_username = sys.argv[standalone_pm_index + 2]
                st_password = sys.argv[standalone_pm_index + 3]
                st_sudo_password = sys.argv[standalone_pm_index + 4]
                logging.info("Upgrading standalone PM")
                destination_path = '/local/home/mxone_admin/'
                status = up.perform_standalone_pm_upgrade(
                    upgrade_pm_location,
                    st_hostname,
                    st_username,
                    st_password,
                    destination_path,
                    st_sudo_password,
                    version,
                    port=22
                )
                add_step("Standalone PM Upgrade", "SUCCESS" if status else "FAILED", "Standalone PM flow executed")
            else:
                logging.info("Upgrading colocated PM")
                status = up.perform_pm_upgrade(upgrade_pm_cmd)
                add_step("Colocated PM Upgrade", "SUCCESS" if status else "FAILED", "Colocated PM flow executed")

        if run_checks:
            logging.info("Starting post-upgrade checks")
            post_upgrade_check.main()
            add_step("Post-Upgrade Checks", "SUCCESS", "Post-upgrade checks executed")

    except Exception as e:
        logging.exception("Upgrade flow failed")
        add_step("Upgrade Flow", "FAILED", str(e))
    finally:
        end_time = datetime.datetime.now()
        failures = [s for s in steps if s["status"] == "FAILED"]
        subject_status = "FAILED" if failures else "SUCCESS"
        summary_html = build_upgrade_summary_html(
            hostname,
            version,
            current_version,
            start_time,
            end_time,
            steps,
            run_checks
        )
        send_email(
            f"[{subject_status}] MX-ONE Upgrade Final Summary - {hostname}",
            summary_html,
            is_html=True
        )

        if failures:
            sys.exit(1)