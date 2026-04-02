import paramiko
import requests
import time
import sys
#import argparse
import logging
from config import hostname, username, password, sudo_password

import pre_upgrade_check

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class FileDownloader:
    def __init__(self, ip, username, password, sudo_password, file_url, destination):
        self.ip = ip
        self.username = username
        self.password = password
        self.sudo_password = sudo_password
        self.file_url = file_url
        self.destination = destination

    def file_exists_and_complete(self, ssh):
        try:
            expected_size = self.get_file_size()
            command = f"stat -c%s {self.destination}"
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.readlines()
            errors = stderr.readlines()

            if errors:
                logging.info("---file didn't exist---")
                return False

            remote_file_size = int(output[0].strip())
            logging.info(f"---Found file of size: {remote_file_size} bytes---")

            if expected_size:
                if remote_file_size >= expected_size:
                    logging.info("---File is already fully downloaded.---")
                    return True
                else:
                    logging.info(f"Incomplete file. Expected: {expected_size}, Found: {remote_file_size}")
            return False

        except Exception as e:
            logging.error(f"An error occurred while checking file existence: {e}")
            return False

    def download_file_linux_via_ssh(self):
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(self.ip, username=self.username, password=self.password, timeout=1800)

            if self.file_exists_and_complete(ssh):
                logging.info("---File is already present, No need to download again---")
                ssh.exec_command(f'chmod -R 777 {self.destination}')
                return

            command = (
                f"echo {self.sudo_password} | sudo -S wget -c --tries=120 --timeout=120 "
                f"--waitretry=5 --read-timeout=60 --limit-rate=500k {self.file_url} "
                f"-O {self.destination} > /tmp/wget_output.log 2>&1"
            )
            print("--- command to download the bin file ---",command)
            stdin, stdout, stderr = ssh.exec_command(command)
            stdout.channel.recv_exit_status()

            output = stdout.readlines()
            errors = stderr.readlines()

            if output:
                logging.info("---Success: " + "".join(output))
            if errors:
                logging.error("---Errors: " + "".join(errors))
        except Exception as e:
            logging.error(f"---An error occurred: {e}")
        finally:
            logging.info("---closing the connection---")
            ssh.close()

    def get_file_size(self):
        try:
            response = requests.head(self.file_url, allow_redirects=True)
            if 'Content-Length' in response.headers:
                file_size = int(response.headers['Content-Length'])
                logging.info(f"File size: {file_size} bytes")
                return file_size
            else:
                logging.info("Could not retrieve the file size from the server.")
                return None
        except requests.RequestException as e:
            logging.error(f"An error occurred while extracting the information: {e}")
            return None

def copy_file_via_ssh(source_ip, source_username, source_password, source_file_path, dest_servers):
    try:
        source_ssh = paramiko.SSHClient()
        source_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        source_ssh.connect(source_ip, username=source_username, password=source_password)

        for dest_server in dest_servers:
            dest_ip = dest_server['ip']
            dest_username = dest_server['username']
            dest_password = dest_server['password']
            dest_file_path = dest_server['file_path']

            dest_ssh = paramiko.SSHClient()
            dest_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            dest_ssh.connect(dest_ip, username=dest_username, password=dest_password)

            source_shell = source_ssh.invoke_shell()
            time.sleep(1)

            scp_command = f"scp {source_file_path} {dest_username}@{dest_ip}:{dest_file_path}\n"
            source_shell.send(scp_command)
            time.sleep(1)

            while True:
                if source_shell.recv_ready():
                    output = source_shell.recv(2048).decode('utf-8')
                    logging.info(output)

                    if "Are you sure you want to continue connecting" in output:
                        source_shell.send("yes\n")
                        time.sleep(1)
                    elif "Password:" in output:
                        source_shell.send(f"{dest_password}\n")
                        time.sleep(1)
                    elif "$" in output or "#" in output or ":~>" in output:
                        break

            logging.info(f"File transfer to {dest_ip} completed.")
            dest_ssh.close()

    except paramiko.AuthenticationException:
        logging.error("Authentication failed, please verify your credentials.")
    except paramiko.SSHException as sshException:
        logging.error(f"Unable to establish SSH connection: {sshException}")
    except paramiko.BadHostKeyException as badHostKeyException:
        logging.error(f"Unable to verify server's host key: {badHostKeyException}")
    except Exception as e:
        logging.error(f"An error occurred: {e}")
    finally:
        source_ssh.close()

if __name__ == "__main__":


    if len(sys.argv) < 1:
        print("Usage: python script.py <hostname> <username> <password> <sudo_password> <version>")
        sys.exit(1)
    BUILD_VERSION = sys.argv[1]

    source_ip = hostname
    source_username = username
    source_password = password
    sudo_password = sudo_password
    file_path = '/local/home/mxone_admin/'

    parts = BUILD_VERSION.split('.')
    build_file = f"MX-ONE_{parts[0]}.{parts[1]}.sp{parts[2]}.hf{parts[3]}.rc{parts[4]}.bin"
    build_url = f'http://10.105.64.17/GIT/Release/refs/tags/mx{BUILD_VERSION}/install/{build_file}'

    downloader = FileDownloader(source_ip, source_username, source_password, sudo_password, build_url, file_path + build_file)
    downloader.download_file_linux_via_ssh()

    dest_servers = [
        {'ip': hostname, 'username': username, 'password': password, 'file_path': '/local/home/mxone_admin'}
    ]

    copy_file_via_ssh(source_ip, source_username, source_password, file_path + build_file, dest_servers)