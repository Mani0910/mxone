import paramiko
import time
import re
import sys
import logging
from config import hostname, username, password, sudo_password

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SystemUpgradeManager:
    def __init__(self, hostname, username, password, sudo_password, port=22):
        self.hostname = hostname
        self.username = username
        self.password = password
        self.sudo_password = sudo_password
        self.port = port
        self.ssh_client = None

    def connect(self):
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                password=self.password,
            )
            print(f"Connected to {self.hostname}")
        except Exception as e:
            print(f"Failed to connect: {e}")

    def disconnect(self):
        if self.ssh_client:
            self.ssh_client.close()
            print("Disconnected")

    def distribute_builds(self, distribute_command):
        print(f"Distributing package with command: {distribute_command}")
        if self.ssh_client is None:
            print("--- initiate ssh client ---")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(hostname=self.hostname, port=self.port, username=self.username,
                                    password=self.password, )

        shell_prompt = self.ssh_client.invoke_shell()
        shell_prompt.send('su -l root\n')
        time.sleep(2)
        shell_prompt.send(self.sudo_password)
        shell_prompt.send('\n')

        time.sleep(5)
        output = shell_prompt.recv(4096)
        console_data = bytes.decode(output)
        #print(console_data)
        print("---now sending distribute command ---")
        shell_prompt.send(distribute_command)
        shell_prompt.send('\n')
        while True:
            time.sleep(30)
            output = shell_prompt.recv(4096)
            console_data = bytes.decode(output)
            print("--- waiting for distribute command to complete ---")
            #print("--- waiting for distribute command to complete ---",console_data)
            if len(console_data) == 0:
                logging.info("--- Package distribution completed. ---")
                break
            if re.search('unpack failed', console_data):
                logging.info("--- unpack failed, exiting ---")
                raise Exception('Unpack failed.')

            if re.search('not enough space on disk', console_data):
                logging.info("--- not enough space on disk, exiting ---")
                raise Exception('Not enough space on disk.')
            if re.search('Enter number to select package', console_data):
                logging.info("--- Enter number to select package from, sending 1 ---")
                shell_prompt.send('1')
                shell_prompt.send('\n')
            if re.search('Type "yes" to abort other process, anything else will abort this action', console_data):
                logging.info("--- typing yes to console to abort other actions ---")
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('(y/n)', console_data):
                logging.info("will be used, confirm: (y/n) is invoked--- sending y---")
                shell_prompt.send('y')
                shell_prompt.send('\n')
            if re.search('Enter bandwidth limit in Mbit/sec', console_data):
                logging.info('--- sending 0 for bandwidth limit ---')
                shell_prompt.send('0')
                shell_prompt.send('\n')
            if re.search('Package distribute ready', console_data):
                logging.info("--- Package successfully distributed ---")
                break
            if re.search(r'[\$#]', console_data):  # Check for shell prompt
                logging.info("--- successfully distribute build completed---")
                break
        time.sleep(30)
        logging.info("--- distribution of build is done successfully ---")

    def prepare_builds(self, prepare_command):
        logging.info(f"---prepare_command updated---: {prepare_command}")
        if self.ssh_client is None:
            print("--- initiate ssh client ---")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(hostname=self.hostname, port=self.port, username=self.username,
                                    password=self.password, )

        shell_prompt = self.ssh_client.invoke_shell()
        shell_prompt.send('su -l root\n')
        time.sleep(2)
        shell_prompt.send(self.sudo_password)
        shell_prompt.send('\n')
        time.sleep(5)
        output = shell_prompt.recv(4096)
        console_data = bytes.decode(output)
        #print(console_data)
        logging.info("---now execute prepare command---")
        shell_prompt.send(prepare_command)
        shell_prompt.send('\n')
        while True:
            time.sleep(30)
            output = shell_prompt.recv(4096)
            console_data = output.decode('utf-8', errors='ignore')  # Handle decoding errors
            #console_data = bytes.decode(output)
            #print(console_data)
            logging.info("--- waiting while prepare is being executed---")
            if len(console_data) == 0:
                logging.info("--- Prepare for upgrade ready ---")
                break
            if re.search('Type "yes" to abort other process, anything else will abort this action', console_data):
                logging.info("--- typing yes to console to abort other actions ---")
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('unpack failed', console_data):
                logging.info("--- unpack failed ---")
                raise Exception('Unpack failed.')

            if re.search('not enough space on disk', console_data):
                logging.info("--- not enough space on disk ---")
                raise Exception('Not enough space on disk.')

            if re.search("Prepare for upgrade failed", console_data):
                logging.info("--- Prepare for upgrade failed ---")
                #print(console_data)
                sys.exit(-1)
                break
            if re.search('Timeout! No answer received from', console_data):
                logging.info('--- Timeout! No answer received from ---')
                print(console_data)
                break
            if re.search('Prepare for upgrade ready', console_data):
                logging.info("Prepare for upgrade ready successfully.")
                break
            if re.search('Ok to continue (y/n)', console_data):
                logging.info("Warning! This is a reexecution of an earlier failed or still running task")
                shell_prompt.send('y')
                shell_prompt.send('\n')

            if re.search(r'[\$#]', console_data):  # Check for shell prompt
                logging.info("successfully prepare done, exiting now")
                break
        time.sleep(30)
        logging.info('--- prepare is exiting now ---')

    def perform_upgrade(self, upgrade_sn):
        logging.info(f"---starting upgrade with cmd---: {upgrade_sn}")
        if self.ssh_client is None:
            print("--- initiate ssh client ---")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(hostname=self.hostname, port=self.port, username=self.username,
                                    password=self.password, )

        shell_prompt = self.ssh_client.invoke_shell()
        shell_prompt.send('su -l root\n')
        time.sleep(2)
        shell_prompt.send(self.sudo_password)
        shell_prompt.send('\n')
        time.sleep(2)
        output = shell_prompt.recv(4096)
        console_data = bytes.decode(output)
        #print(console_data)
        logging.info("--- starting sn upgrade now ---")
        shell_prompt.send(upgrade_sn)
        shell_prompt.send('\n')
        time.sleep(60)
        for i in range(80):
            time.sleep(30)
            data = shell_prompt.recv(4096)
            #console_data = bytes.decode(data)
            console_data = data.decode('utf-8', errors='ignore')  # Handle decoding errors
            logging.info("--- upgrade is in progress ---")
            #logging.info(console_data)
            if len(console_data) == 0:
                logging.info("\n*** EOF\n")
                logging.info("--- End of upgrade ---")
                break
            if re.search('Type "yes" to abort other process, anything else will abort this action', console_data):
                logging.info("--- typing yes to console to abort other actions ---")
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('(y/n)', console_data):
                logging.info('---yes/no is asked,--- sending y')
                shell_prompt.send('y')
                shell_prompt.send('\n')
            elif re.search("Finished.", console_data):
                logging.info("---successfully upgraded---")
                shell_prompt.send('ts_about')
                shell_prompt.send('\n')
                break
            if re.search('not enough space on disk', console_data):
                raise Exception('Not enough space on disk.')
            if re.search("Prepare for upgrade failed", console_data):
                logging.info("--- *** Prepare for upgrade failed *** ---")
                sys.exit(-1)
                break
            if re.search('Timeout! No answer received from', console_data):
                logging.info('--- Timeout! No answer received from ---')
                break
            if re.search('Prepare for upgrade ready', console_data):
                logging.info("--- upgrade successfully done ---")
                break
            if re.search(r'[\$#]', console_data):  # Check for shell prompt
                logging.info("--- successfully perform_upgrade completed ---")
                break
        time.sleep(30)
        print('--- successfully perform_upgrade completed---')

    def perform_rollback(self, cmd):
        print("--- Starting rollback...")
        # Define rollback commands based on your system and strategy
        print("...Starting rollback...")
        # upgrade_sn = f"echo {self.sudo_password} | sudo -S {cmd}"

        if self.ssh_client is None:
            print("---ssh client is none ---")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(hostname=self.hostname, port=self.port, username=self.username,
                                    password=self.password, )

        shell_prompt = self.ssh_client.invoke_shell()
        shell_prompt.send('su -l root\n')
        time.sleep(2)
        shell_prompt.send(self.sudo_password)
        shell_prompt.send('\n')
        time.sleep(5)
        output = shell_prompt.recv(4096)
        console_data = bytes.decode(output)
        #print(console_data)
        print("-----now run rollback---")
        shell_prompt.send(cmd)
        shell_prompt.send('\n')
        time.sleep(10)
        # output = shell_prompt.recv(65535)
        while True:
            time.sleep(20)
            data = shell_prompt.recv(4096)
            console_data = data.decode('utf-8', errors='ignore')  # Handle decoding errors
            if len(console_data) == 0:
                print("\n*** EOF\n")
                print("end")
                break
            if re.search('Type "yes" to abort other process, anything else will abort this action', console_data):
                print("--- typing yes to console to abort other actions ---")
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('confirm: (y/n)', console_data):
                print('yes no is asked, sending yes')
                shell_prompt.send('y\n')

            elif re.search("Service Node Manager rollback finished.", console_data):
                shell_prompt.send('ts_about')
                shell_prompt.send('\n')
                break

            if re.search('Timeout! No answer received from', console_data):
                print('Timeout! No answer received from')
                break
            if re.search(r'[\$#]', console_data):  # Check for shell prompt
                print("---successfully shell prompt received. exiting now---")
                break
            print("---------rollback successful--------")
        print('--- Please wait upgrade in progress ---')

    def perform_pm_upgrade(self, upgrade_pm_cmd):
        print(f"---perform_ pm upgrade --- {upgrade_pm_cmd}")
        if self.ssh_client is None:
            print("--- ssh client is none so creating one ---")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(hostname=self.hostname, port=self.port, username=self.username,
                                    password=self.password, )

        shell_prompt = self.ssh_client.invoke_shell()
        shell_prompt.send('su -l root\n')
        time.sleep(2)
        shell_prompt.send(self.sudo_password)
        shell_prompt.send('\n')
        time.sleep(2)
        logging.info("--- now run pm upgrade ---")
        ansi_escape = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -/]*[@-~]')
        shell_prompt.send(upgrade_pm_cmd)
        shell_prompt.send('\n')
        logging.info("--- waiting for upgrade to start ---")
        time.sleep(40)
        _upgradePM = True
        start_time = time.time()  # Record the start time
        timeout = 2000  # Set a timeout duration (e.g., 1 hour)
        while time.time() - start_time < timeout:
            time.sleep(60)
            #data = shell_prompt.recv(4096)
            if shell_prompt.recv_ready():
                data = shell_prompt.recv(4096)
            else:
                logging.info("--- No data received, waiting --- ")
                time.sleep(30)  # Add a small delay to avoid busy-waiting
            #console_data = bytes.decode(data)
            console_data = data.decode('utf-8', errors='ignore')  # Handle decoding errors
            mxoneOutPut = ansi_escape.sub('', console_data).replace("\r\n", " ")

            if len(console_data) == 0:
                logging.info("\n*** EOF *** \n")
                break
            if re.search('(y/n)', mxoneOutPut):
                logging.info("--- sending string y --- ")
                shell_prompt.send('y\n')
            if re.search('Type "yes" to abort other process, anything else will abort this action', mxoneOutPut):
                logging.info("--- typing yes to console to abort other actions ---")
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('Enter bandwidth limit in Mbit ', mxoneOutPut):
                shell_prompt.send('\n')
            if re.search('LICENSE AGREEMENT', mxoneOutPut):
                shell_prompt.send('\n')
            if re.search('Do you want to proceed?', mxoneOutPut):
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('Do you want to continue', mxoneOutPut):
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('installed. Upgrade not possible', mxoneOutPut):
                logging.info("Allready upgraded,continue without upgrade ")
                break
            if re.search('is already installed.', mxoneOutPut):
                logging.info("Allready upgraded,continue without upgrade ")
                break
            if re.search('Upgrade not possible', mxoneOutPut):
                logging.info("--- upgrade not possible ---")
                shell_prompt.send('\n')
                _upgradePM = False
                break
            if re.search('ERROR: Database MP not found!! Exiting script', mxoneOutPut):
                logging.info("--- ERROR: Database MP not found!! Exiting script ---")
                _upgradePM = False
                break
            if re.search('System Setup Admin last name', mxoneOutPut):
                logging.info("--- ERROR: PM is not installed / No user present!! Exiting script ---")
                # _upgradePM=False
                break
            if re.search('System Setup Admin first name', mxoneOutPut):
                logging.info("--- ERROR: PM is not installed / No user present!! Exiting script ---")
                # _upgradePM=False
                break
            if re.search('Restart ordered', mxoneOutPut):
                logging.info('--- Sucessfully upgraded and ordered restart ---')
                time.sleep(120)
                _upgradePM = True
                break
            if re.search(r'Restart now?', mxoneOutPut):
                shell_prompt.send('\n')
                logging.info('--- Sucessfully upgraded and ordered restart ---')
                time.sleep(120)
            if re.search(r'Press enter key to close this dialogue', mxoneOutPut):
                shell_prompt.send('\n')
                logging.info('--- Sucessfully closed the dialogue window ---')
                time.sleep(60)
            if  re.search('Timeout! No answer received from', mxoneOutPut):
                logging.info('--- Timeout! No answer received from ---')
                break
            # if re.search(r'[\$#]', mxoneOutPut):  # Check for shell prompt
            #     logging.info("--- jboss restarted successfully ---")
            #     logging.info(mxoneOutPut)
            #     break
            logging.info("--- pm upgrade is in progress ---")
        logging.info("--- pm upgrade is done restart jboss ---")
        shell_prompt.send("systemctl restart mxone_jboss.service")
        shell_prompt.send("\n")
        time.sleep(600)
        logging.info("--- jboss restarted successfully ---")
        return _upgradePM

    def perform_standalone_pm_upgrade(self, pm_file_loc, dest_ip,
                                      dest_username, dest_password, dest_file_path, dest_sudo_password, version,
                                      port=22):

        if self.ssh_client is None:
            print("---------ssh client is none -------------------")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(hostname=self.hostname, port=self.port, username=self.username,
                                    password=self.password, )

        scp_cmd = f"scp {pm_file_loc} {dest_username}@{dest_ip}:{dest_file_path}\n"
        print("--- scp_cmd ---", scp_cmd)
        shell_prompt = self.ssh_client.invoke_shell()
        time.sleep(1)
        shell_prompt.send('su -l root\n')
        time.sleep(2)
        shell_prompt.send(self.sudo_password)
        shell_prompt.send('\n')
        time.sleep(5)

        print("--- sending pm file copy command on standalone pm ---")
        shell_prompt.send(scp_cmd)
        time.sleep(160)

        while True:
            if shell_prompt.recv_ready():
                output = shell_prompt.recv(2048).decode('utf-8', errors='ignore')
                #console_data = output.decode('utf-8', errors='ignore')  # Handle decoding errors
                logging.info(output)

                if "Are you sure you want to continue connecting" in output:
                    shell_prompt.send("yes")
                    shell_prompt.send("\n")
                    time.sleep(1)
                elif "Password:" in output:
                    shell_prompt.send(f"{dest_password}")
                    shell_prompt.send("\n")
                    time.sleep(1)
                elif "$" in output or "#" in output or ":~>" in output:
                    break
            logging.info("--- standalone PM  ---")

        self.ssh_client.close()

        pm_install_cmd = "sh " + dest_file_path + "mxone_pm_install-" + version + ".bin"
        
        logging.info("--- standalone pm install cmd --- %s", pm_install_cmd)

        dest_ssh = paramiko.SSHClient()
        dest_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        dest_ssh.connect(dest_ip, username=dest_username, password=dest_password)
        time.sleep(2)

        sh_prompt = dest_ssh.invoke_shell()
        sh_prompt.send('su -l root\n')
        time.sleep(2)
        sh_prompt.send(dest_sudo_password)
        sh_prompt.send('\n')
        time.sleep(2)
        output = sh_prompt.recv(4096)
        #console_data = bytes.decode(output)
        console_data = output.decode('utf-8', errors='ignore')  # Handle decoding errors
        #print(console_data)
        print("--- now running standalone pm upgrade ---")
        ansi_escape = re.compile(r'(\x9B|\x1B\[)[0-?]*[ -/]*[@-~]')
        sh_prompt.send(pm_install_cmd)
        sh_prompt.send('\n')
        time.sleep(10)
        _upgradePM = True
        print("--- executing upgrade command ---")
        start_time = time.time()  # Record the start time
        timeout = 2000  # Set a timeout duration (e.g., 1 hour)
        while time.time() - start_time < timeout:
            time.sleep(30)
            data = sh_prompt.recv(4096)
            if sh_prompt.recv_ready():
                data = sh_prompt.recv(4096)
            else:
                logging.info("No data received, waiting...")
                time.sleep(20)  # Add a small delay to avoid busy-waiting
            #console_data = bytes.decode(data)
            console_data = data.decode('utf-8', errors='ignore')  # Handle decoding errors
            logging.info("--- data on terminals ---")
            #print(console_data)
            mxoneOutPut = ansi_escape.sub('', console_data).replace("\r\n", " ")
            #print(mxoneOutPut)
            if len(console_data) == 0:
                print("\n*** EOF\n")
                print("end")
                break
            if re.search('(y/n)', mxoneOutPut):
                sh_prompt.send('y\n')
            if re.search('Type "yes" to abort other process, anything else will abort this action', mxoneOutPut):
                logging.info("--- typing yes to console to abort other actions ---")
                shell_prompt.send('yes')
                shell_prompt.send('\n')
            if re.search('Enter bandwidth limit in Mbit ', mxoneOutPut):
                sh_prompt.send('\n')
            if re.search('LICENSE AGREEMENT', mxoneOutPut):
                sh_prompt.send('\n')
            if re.search('Do you want to proceed?', mxoneOutPut):
                sh_prompt.send('\n')
            if re.search('Do you want to continue', mxoneOutPut):
                sh_prompt.send('\n')
            if re.search('installed. Upgrade not possible', mxoneOutPut):
                logging.info("---Allready upgraded,continue without upgrade---")
                break
            if re.search('is already installed.', mxoneOutPut):
                logging.info("Allready upgraded,continue without upgrade ")
                break
            if re.search('Upgrade not possible', mxoneOutPut):
                logging.info("upgrade not possible")
                sh_prompt.send('\n')
                _upgradePM = False
                break
            if re.search('ERROR: Database MP not found!! Exiting script', mxoneOutPut):
                logging.info("ERROR: Database MP not found!! Exiting script")
                _upgradePM = False
                break
            if re.search('System Setup Admin last name', mxoneOutPut):
                logging.info("ERROR: PM is not installed / No user present!! Exiting script")
                break
            if re.search('System Setup Admin first name', mxoneOutPut):
                logging.info("ERROR: PM is not installed / No user present!! Exiting script")
                break
            if re.search('Restart ordered', mxoneOutPut):
                logging.info('Sucessfully upgraded and ordered restart')
                time.sleep(120)
                _upgradePM = True
                break
            if re.search(r'Restart now?', mxoneOutPut):
                sh_prompt.send('\n')
                logging.info('Sucessfully upgraded and ordered restart')
                time.sleep(120)
                break
            if re.search(r'Press enter key to exit script.', mxoneOutPut):
                logging.info('upgrade not possible')
                time.sleep(1)
                sh_prompt.send('\n')
                break
            if  re.search('Timeout! No answer received from', mxoneOutPut):
                logging.info('Timeout! No answer received from')
                break

            if re.search(r'Press enter key to close this dialogue', mxoneOutPut):
                sh_prompt.send('\n')
                logging.info('--- Sucessfully closed the dialogue window ---')
                time.sleep(60)
            if re.search(r'[\$#]', mxoneOutPut):  # Check for shell prompt
                logging.info("pm is successfully updated. exiting now")
                logging.info(mxoneOutPut)
                break
            logging.info("--- standalone pm upgrade is in progress ---")
        logging.info("--- jboss restarts order now ---")
        sh_prompt.send("systemctl restart mxone_jboss.service")
        sh_prompt.send("\n")
        time.sleep(600)
        logging.info("--- jboss restarted successfully exiting the PM upgrade---")
        return _upgradePM


if __name__ == "__main__":
    # Replace with actual credentials and hostname
    hostname = hostname
    username = username
    password = password
    sudo_password = sudo_password

    file_url = 'http://10.105.64.17/GIT/Release/refs/tags/mx7.8.0.0.23/install/MX-ONE_7.8.sp0.hf0.rc23.bin'  # URL of the file to download
    local_path = '/local/home/mxone_admin/'
    file_name = 'MX-ONE_8.0.sp0.hf0.rc33.bin'
    destination = local_path + file_name  # Path on the Linux system

    file_version = re.findall(r'\d+', file_name)
    latest_version = '.'.join(file_version)
    print("version = ", latest_version)

    distribute_cmd = 'sh ' + destination + ' --package_distribute'
    prepare_cmd = '/opt/mxone_install/' + latest_version + '/target/install_scripts/sn_install_main.sh "upgrade_prepare" "" "" "silent"'
    upgrade_cmd = '/opt/mxone_install/' + latest_version + '/target/install_scripts/sn_install_main.sh "upgrade" "" "" "silent"'
    #rollback_cmd = 'sh /opt/mxone_install/7.8.0.0.23/target/install_scripts/sn_install_main.sh "rollback" "" "" 7.8.0.0.13'

    print("distribute_cmd = ", distribute_cmd)
    print("prepare_cmd = ", prepare_cmd)
    print("upgrade_cmd = ", upgrade_cmd)
    #print("rollback_cmd = ", rollback_cmd)

    manager = SystemUpgradeManager(hostname, username, password, sudo_password)

    try:
        manager.connect()
        manager.distribute_builds(distribute_cmd)
        manager.prepare_builds(prepare_cmd)
        manager.perform_upgrade(upgrade_cmd)
        #manager.perform_rollback(rollback_cmd)
    finally:
        manager.disconnect()
