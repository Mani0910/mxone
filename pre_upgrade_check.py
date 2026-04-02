from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http import client
from importlib.metadata import files

import paramiko
import socket
import logging
import re
import datetime
import sys
import signal

from requests import delete
from config import hostname, username, password, sudo_password as config_sudo_password

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


def check_disk_usage(client, threshold=50):
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


def check_opt_versions(client, sudo_password):
    """
    Check installed versions in /opt directory and delete old versions.
    For each location, keep only current (N) and previous (N-1) versions.
    """
    logger.info("Checking /opt versions...")

    print("\n===== OPT VERSIONS CHECK =====\n")

    # Get version details from ts_about
    output = execute_command(client, "ts_about")
    if not output:
        print("/opt Check: Failed to get current version\n")
        return False

    def extract_version(text, patterns):
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return m.group(1)
        return None

    def parse_version(version_string):
        try:
            return tuple(int(part) for part in version_string.split('.'))
        except Exception:
            return None

    def shell_quote(value):
        return "'" + str(value).replace("'", "'\"'\"'") + "'"

    versions_from_ts = {
        "mxone": extract_version(output, [r"Version:\s*([\d\.]+)"]),
        "service_node": extract_version(output, [
            r"MX-ONE\s+Service\s+Node\s*[:\-]?\s*([\d\.]+)",
            r"Service\s+Node\s*[:\-]?\s*([\d\.]+)"
        ]),
        "snm": extract_version(output, [
            r"MX-ONE\s+SNM(?:\s+Installation)?\s*[:\-]?\s*([\d\.]+)",
            r"\bSNM\s+(?:Version\s*)?[:\-]?\s*([\d\.]+)"
        ]),
        "pm": extract_version(output, [
            r"MX-ONE\s+PM(?:\s+Installation)?\s*[:\-]?\s*([\d\.]+)",
            r"\bPM\s+(?:Version\s*)?[:\-]?\s*([\d\.]+)"
        ])
    }

    if not versions_from_ts["mxone"]:
        print("/opt Check: Current MX-ONE version not found in ts_about\n")
        return False

    # Fallback to MX-ONE version if component-specific version is missing in ts_about
    for component in ("service_node", "snm", "pm"):
        if not versions_from_ts[component]:
            versions_from_ts[component] = versions_from_ts["mxone"]

    print(f"Current Version (MX-ONE): {versions_from_ts['mxone']}")
    print(f"Current Version (Service Node): {versions_from_ts['service_node']}")
    print(f"Current Version (SNM): {versions_from_ts['snm']}")
    print(f"Current Version (PM): {versions_from_ts['pm']}")
    print()

    def check_single_location(location_path, expected_version):
        """Read available version directories and classify keep/delete."""
        versions = []

        ls_cmd = f"ls -d {location_path}/* 2>/dev/null || ls {location_path} 2>/dev/null"
        ls_output = execute_sudo_command(client, ls_cmd, sudo_password, timeout=5)

        if not ls_output:
            return None

        seen = set()
        for raw_line in ls_output.strip().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("total ") or line.startswith("dr") or line.startswith("lr"):
                continue
            if "->" in line:
                continue

            if line.startswith(location_path + "/"):
                entry_path = line
            elif "/" not in line:
                entry_path = f"{location_path}/{line}"
            else:
                continue

            basename = entry_path.rstrip("/").split("/")[-1]
            match = re.match(r"^(\d+(?:\.\d+)+)$", basename)
            if not match:
                continue

            version_str = match.group(1)
            parsed = parse_version(version_str)
            if not parsed:
                continue

            dedupe_key = (version_str, entry_path)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            versions.append({
                "version": version_str,
                "path": entry_path,
                "parsed": parsed
            })

        if not versions:
            return None

        versions.sort(key=lambda item: item["parsed"], reverse=True)

        expected_parsed = parse_version(expected_version)
        current_item = next((item for item in versions if item["parsed"] == expected_parsed), None)

        keep = []
        previous_item = None

        if current_item:
            keep.append(current_item)
            for item in versions:
                if item["parsed"] < current_item["parsed"]:
                    previous_item = item
                    keep.append(item)
                    break
        else:
            # If expected version is not found in this path, keep latest 2 to avoid deleting active installs.
            keep = versions[:2]
            if keep:
                current_item = keep[0]
            if len(keep) > 1:
                previous_item = keep[1]

        delete = [item for item in versions if item not in keep]

        return {
            "expected_version": expected_version,
            "current": current_item,
            "previous": previous_item,
            "all_versions": versions,
            "keep": keep,
            "delete": delete
        }

    def print_and_delete(result):
        """Print summary and delete old versions."""
        if not result:
            print("  No versions found\n")
            return True, 0

        print(f"  Expected Version : {result['expected_version']}")
        print(f"\n  {'Version':<20} {'Location':<45} {'Status':<12}")
        print(f"  {'-' * 20} {'-' * 45} {'-' * 12}")

        for item in result["all_versions"]:
            if result["current"] and item["path"] == result["current"]["path"]:
                status = "CURRENT"
            elif result["previous"] and item["path"] == result["previous"]["path"]:
                status = "PREVIOUS"
            elif item in result["delete"]:
                status = "DELETE"
            else:
                status = "KEEP"

            path_display = item["path"].replace('/opt/', '')[-40:]
            print(f"  {item['version']:<20} {path_display:<45} {status:<12}")

        print()

        if not result["delete"]:
            print("  PASS - No old versions\n")
            return True, 0

        print(f"  Deleting {len(result['delete'])} old version(s)...")

        deleted_count = 0
        all_deleted_ok = True

        for item in result["delete"]:
            version_path = item["path"]

            print(f"    Processing: {item['version']}")
            print(f"      Path: {version_path}")

            execute_sudo_command(
                client,
                f"chmod -R u+rwX {version_path}",
                sudo_password,
                timeout=8
            )

            execute_sudo_command(
                client,
                f"rm -rf {version_path}",
                sudo_password,
                timeout=10
            )

            verify_result = execute_sudo_command(
                client,
                f"test -e {version_path} && echo EXISTS || echo DELETED",
                sudo_password,
                timeout=5
            )

            if verify_result and "DELETED" in verify_result:
                print(f"      [OK] Deleted: {item['version']}")
                deleted_count += 1
            else:
                print(f"      [FAIL] Could not delete: {item['version']}")
                all_deleted_ok = False

        print()
        return all_deleted_ok, deleted_count

    checks = [
        ("1. MX-ONE Version Check", "/opt/mxone_install", "mxone", False),
        ("2. MX-ONE Service Node Version Check", "/opt/eri_sn", "service_node", True),
        ("3. MX-ONE SNM Installation Version Check", "/opt/mxone_snm_install", "snm", True),
        ("4. MX-ONE SNM Microservices Version Check", "/opt/mxone_snm_microservices", "snm", True),
        ("5. MX-ONE PM Installation Version Check", "/opt/mxone_pm_install", "pm", True),
        ("6. MX-ONE PM Microservices Version Check", "/opt/mxone_pm_microservices", "pm", True),
    ]

    all_status = True
    total_deleted = 0

    for title, location_path, component_key, optional_path in checks:
        print(title)
        print("=" * 80)

        expected_version = versions_from_ts[component_key]
        result = check_single_location(location_path, expected_version)

        if not result:
            print(f"  No versions found in {location_path}\n")
            # Some paths may be absent depending on node role/deployment type.
            if not optional_path:
                all_status = False
            continue

        location_status, deleted_count = print_and_delete(result)
        total_deleted += deleted_count
        all_status = all_status and location_status
        print()

    # Summary
    print("\n" + "=" * 80)
    print("CLEANUP SUMMARY")
    print("=" * 80 + "\n")
    print(f"Total versions deleted: {total_deleted}\n")

    if total_deleted > 0:
        print("All cleanups completed successfully\n")
    else:
        print("No old versions to delete\n")

    return all_status


def cleanup_old_bins(client, path="/local/home/mxone_admin"):
    """Keep current (N) and previous (N-1) .bin files, delete rest"""

    # STEP 1: Get current version from ts_about
    stdin, stdout, stderr = client.exec_command("ts_about")
    output = stdout.read().decode()

    match = re.search(r'Version:\s*([\d\.]+)', output)
    if not match:
        print("Could not find current version")
        return

    current_version_str = match.group(1)
    current_version = tuple(map(int, current_version_str.split(".")))

    print(f"\nCurrent Version: {current_version_str}")

    # STEP 2: Get all .bin files
    stdin, stdout, stderr = client.exec_command(f"ls {path}/*.bin 2>/dev/null")
    files = stdout.read().decode().strip().splitlines()

    if not files:
        print("No .bin files found")
        return

    version_map = []

    # STEP 3: Extract versions
    for file in files:
        file_name = file.split("/")[-1]

        nums = re.findall(r'\d+', file_name)
        version_tuple = tuple(map(int, nums))
        version_str = '.'.join(nums)

        version_map.append((file, version_tuple, version_str))

    # STEP 4: Sort (latest first)
    version_map.sort(key=lambda x: x[1], reverse=True)

    print("\nAll available versions:")
    for f, _, v_str in version_map:
        print(f"{f} -> {v_str}")

    # STEP 5: Find current (N)
    keep = []
    delete = []

    current_index = None
    for i, (_, v, _) in enumerate(version_map):
        if v[:len(current_version)] == current_version:
            current_index = i
            break

    # STEP 6: Decide keep/delete
    if current_index is not None:
        keep.append(version_map[current_index])  # N

        if current_index + 1 < len(version_map):
            keep.append(version_map[current_index + 1])  # N-1

        for i, item in enumerate(version_map):
            if i not in [current_index, current_index + 1]:
                delete.append(item)
    else:
        print("Current version not found in files → fallback to latest 2")
        keep = version_map[:2]
        delete = version_map[2:]

    # STEP 7: Print actions
    print("\nKeeping:")
    for f, _, v in keep:
        print(f"{f} -> {v}")

    if not delete:
        print("\nNo files to delete ")
    else:
        print("\nDeleting:")
        for f, _, v in delete:
            print(f"{f} -> {v}")
            client.exec_command(f"rm -f {f}")


def cleanup_old_bins_md5_sha(client, path="/local/home/mxone_admin/install_sw"):
    """Keep current (N) and previous (N-1) .bin/.md5/.sha256 files, delete rest"""

    print(f"\n===== Checking path: {path} =====")

    # STEP 1: Get current version
    stdin, stdout, stderr = client.exec_command("ts_about")
    output = stdout.read().decode()

    match = re.search(r'Version:\s*([\d\.]+)', output)
    if not match:
        print("Could not find current version")
        return

    current_version_str = match.group(1)
    current_version = tuple(map(int, current_version_str.split(".")))

    print(f"Current Version: {current_version_str}")

    # STEP 2: Get .bin files
    stdin, stdout, stderr = client.exec_command(f"ls {path}/*.bin 2>/dev/null")
    files = stdout.read().decode().strip().splitlines()

    if not files:
        print("No .bin files found")
        return

    version_map = []

    # STEP 3: Extract versions
    for file in files:
        file_name = file.split("/")[-1]

        nums = re.findall(r'\d+', file_name)
        version_tuple = tuple(map(int, nums))
        version_str = '.'.join(nums)

        version_map.append((file, version_tuple, version_str))

    # STEP 4: Sort
    version_map.sort(key=lambda x: x[1], reverse=True)

    print("\nAll available versions:")
    for f, _, v in version_map:
        print(f"{f} -> {v}")

    keep = []
    delete = []

    # STEP 5: Find current
    current_index = None
    for i, (_, v, _) in enumerate(version_map):
        if v[:len(current_version)] == current_version:
            current_index = i
            break

    # STEP 6: Logic
    if current_index is not None:
        keep.append(version_map[current_index])

        if current_index + 1 < len(version_map):
            keep.append(version_map[current_index + 1])

        for i, item in enumerate(version_map):
            if i not in [current_index, current_index + 1]:
                delete.append(item)
    else:
        print("Current version not found → keeping latest 2")
        keep = version_map[:2]
        delete = version_map[2:]

    # STEP 7: Output - Show all file types (.bin, .md5, .sha256)
    print("\nKeeping:")
    for f, _, v in keep:
        print(f"{f} -> {v}")
        print(f"{f}.md5")
        print(f"{f}.sha256")

    if not delete:
        print("\nNo files to delete")
    else:
        print("\nDeleting:")
        for f, _, v in delete:
            print(f"{f} -> {v}")
            print(f"{f}.md5")
            print(f"{f}.sha256")
            client.exec_command(f"rm -f {f}")
            client.exec_command(f"rm -f {f}.md5")
            client.exec_command(f"rm -f {f}.sha256")


def check_data_backup(client):
    logger.info("Starting Data Backup...")
    print("\n===== DATA BACKUP CHECK =====\n")

    # Step 1: Run backup
    #print("Running command: data_backup")
    backup_output = execute_command(client, "data_backup")

    if not backup_output:
        msg = "Data backup command failed (no output)"
        print(f"Data Backup      : FAIL - {msg}\n")
        return False, msg

    if "successful" not in backup_output.lower():
        msg = "Data backup command did not report success"
        print(f"Data Backup      : FAIL - {msg}")
        print(backup_output.strip())
        print()
        return False, msg

    print("Data Backup      : PASS - data_backup completed")

    logger.info("Backup completed. Verifying status...")

    # Step 2: Check system status
    #print("Running command: status -system")
    status_output = execute_command(client, "status -system")

    if not status_output:
        msg = "No output from status -system"
        print(f"Data Dump Status : FAIL - {msg}\n")
        return False, msg

    latest_data_dump = None

    # Step 3: Find latest Data Dump entry
    for line in status_output.splitlines():
        line = line.strip()

        if line.startswith("Data Dump"):
            latest_data_dump = line
            break  # first occurrence in history is the latest entry

    if not latest_data_dump:
        msg = "No Data Dump entry found in status -system"
        print(f"Data Dump Status : FAIL - {msg}\n")
        return False, msg

    # Step 4: Validate latest Data Dump status
    if "successful" in latest_data_dump.lower():
        msg = f"Latest Data Dump is successful -> {latest_data_dump}"
        print(f"Data Dump Status : PASS")
        print(f"Latest Entry     : {latest_data_dump}\n")
        return True, msg

    msg = f"Latest Data Dump is not successful -> {latest_data_dump}"
    print(f"Data Dump Status : FAIL")
    print(f"Latest Entry     : {latest_data_dump}\n")
    return False, msg

# ================= EMAIL NOTIFICATIONS =================
from config import EMAIL_ENABLED, SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD, RECIPIENTS
import smtplib


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
        # swap_memory returns (bool, message)
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
    """Build full HTML summary email for pre/post upgrade checks."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Check labels for display (only include checks that should appear in email)
    check_labels = {
        "ts_about": "Version Check (ts_about)",
        "license_status": "License Validity",
        "disk_usage": "Disk Usage",
        "swap_memory": "Swap Memory",
        "alarm": "Alarm Status",
        "comfunc": "Common Functions",
    }

    # Build table rows
    rows_html = ""
    pass_count = 0
    fail_count = 0
    warn_count = 0

    for key, result in results.items():
        if key not in check_labels:
            continue

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

    # Summary stats row
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
            f"[ALERT] Pre-Upgrade Check - SSH Connection Failed ({hostname})",
            get_email_template(
                "SSH Connection Failed",
                "#dc3545",
                f'<p style="color:#495057;font-size:14px;">Could not connect to <strong>{hostname}</strong>. Pre-upgrade checks were not executed.</p>'
            ),
            is_html=True
        )
        return

    try:
        print("\n===== PRE UPGRADE CHECK =====\n")

        sudo_password = config_sudo_password

        results = {
            "ts_about": check_ts_about(client),
            "license_status": check_license_validity(output=execute_command(client, "license_status")),
            "opt_versions": check_opt_versions(client, sudo_password),
            "cleanup_bins": cleanup_old_bins(client),
            "cleanup_bins_md5_sha": cleanup_old_bins_md5_sha(client),
            "disk_usage": check_disk_usage(client),
            "swap_memory": check_swap_memory(client),
            "alarm": check_alarms(client),
            "comfunc": check_comfunc(client),
            "data_backup": check_data_backup(client),
        }

        # Send summary report email
        print("\n===== SENDING PRE-UPGRADE REPORT EMAIL =====\n")
        email_html, fail_count = build_summary_email("Pre-Upgrade Check", results, hostname)
        status_tag = "PASSED" if fail_count == 0 else "FAILED"
        send_email(
            f"[{status_tag}] Pre-Upgrade Check Report - {hostname}",
            email_html,
            is_html=True
        )

    finally:
        client.close()


if __name__ == "__main__":
    main()