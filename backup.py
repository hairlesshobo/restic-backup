#!/usr/bin/env python3

import json
import logging
import os
import platform
import pprint
import subprocess
import socket
import sys
import time
import yaml
from pyzabbix import ZabbixMetric, ZabbixSender

restic_path = 'restic'
zabbix_config = '/etc/zabbix/zabbix_agent2.conf'
config_file_name = 'config.yml'

hostname = platform.node() 
hostname_fqdn = socket.getfqdn()

script_path = os.path.realpath(os.path.dirname(__file__))

logging.basicConfig(level=logging.DEBUG)

def show_usage(error=''):
    if len(error) > 0:
        print(error)
        print()

    print("Usage: ./backup.py <mode> [profile]")
    print()
    print("  mode:")
    print("    backup     run a backup job")
    print("    publish    publish the current config to the zabbix server")
    print()
    print(f"  backup_name: Must be a backup, as configured in {config_file_name}")
    exit(-1)

class ResticBackup:
    def __init__(self):
        self.config = self.read_config()

    def read_config(self):
        config_file_path = os.path.join(script_path, config_file_name)

        if not os.path.exists(config_file_path):
            logging.critical("ERROR: Config file doesn't exist")
            exit(-1)

        logging.info(f"Reading config file {config_file_path}")

        with open(config_file_path, "r") as stream:
            try:
                config = yaml.safe_load(stream)

                return config
            except yaml.YAMLError as exc:
                print(exc)

    def send_discovery(self):
        keys = self.config['backups']

        discovery = {'data': [{'{#PROFILE}': item} for item in keys]}
        metrics = list()
        metrics.append(ZabbixMetric(hostname_fqdn,
                                    'restic.backup.profiles',
                                    json.dumps(discovery)))

        key_list = list(keys.keys())

        logging.info(f"Sending discovery: {key_list}")
        
        result = ZabbixSender(use_config=zabbix_config).send(metrics)
        logging.debug(pprint.pformat(result))


    def run_cleanup(self, backup_name):
        retention = []

        # Read the retention config
        if 'mode' == 'clean' and 'retention' in backup_def:
            ret_def = backup_def['retention']

            if 'daily' in ret_def:
                retention.append('--keep-within-daily')
                retention.append(ret_def['daily'])

            if 'weekly' in ret_def:
                retention.append('--keep-within-weekly')
                retention.append(ret_def['weekly'])

            if 'monthly' in ret_def:
                retention.append('--keep-within-monthly')
                retention.append(ret_def['monthly'])
                
            if 'yearly' in ret_def:
                retention.append('--keep-within-yearly')
                retention.append(ret_def['yearly'])


        print(retention)

        command_builder = []
        command_builder.append(restic_path)
        command_builder.extend(retention)
        command_builder.append('--tag')
        command_builder.append(backup_name)


        print(command_builder)



    def run_backup(self, backup_name):
        if backup_name not in self.config['backups']:
            print(f"ERROR: Backup definition named '{backup_name}' not found!")
            show_usage()

        self.__zbx_send_status(backup_name, 'Starting')

        backup_def = self.config['backups'][backup_name]

        command_builder = []
        command_builder.append(restic_path)
        command_builder.append('--json')
        command_builder.append('--tag')
        command_builder.append(backup_name)

        # Add any exlcudes that were configured
        if 'exclude' in backup_def:
            for exclude in backup_def['exclude']:
                command_builder.extend(['--exclude', exclude])

        command_builder.append('backup')

        # TODO: add check to make sure source exists
        for source in backup_def['source']:
            command_builder.append(source)

        logging.debug(command_builder)

        # prepare the environment for running restic
        new_env = self.config['env']
        new_env['HOME'] = os.environ['HOME']
        
        process = subprocess.Popen(command_builder, 
                                   env=new_env, 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE)

        self.__zbx_send_status(backup_name, 'Running')

        for json_line in process.stdout:
            logging.debug(json_line)
            obj = json.loads(json_line)
            #print(obj)
            
            if 'message_type' in obj:
                if obj['message_type'] == 'status':
                    self.__send_status_metrics(backup_name, obj)

                if obj['message_type'] == 'summary':
                    self.__send_finished_metrics(backup_name, obj)

        error_lines = []

        for error_line in process.stderr:
            error_lines.append(error_line.decode('utf-8'))

        self.__send_metric([{self.__zbx_hkey(backup_name, 'last_error'): "".join(error_lines)}])
        
        process.wait()

        logging.debug(f'restic return code: {process.returncode}')

        if process.returncode == 0:
            # if there were any errors printed on stderr but the returncode was 0,
            # this should be considered a warning
            if len(error_lines) == 0:
                self.__zbx_send_status(backup_name, 'Success')
            else:
                self.__zbx_send_status(backup_name, 'Warning')
        elif process.returncode == 1:
            self.__zbx_send_status(backup_name, 'Failed')
        elif process.returncode == 3:
            self.__zbx_send_status(backup_name, 'Warning')

    def __zbx_send_status(self, backup_name, status):
        self.__send_metric([
            {self.__zbx_hkey(backup_name, 'status'): status},
            {'restic.last_report.job': backup_name}])


    def __zbx_hkey(self, backup_name, key):
        return f'restic.backup[{backup_name},{key}]'


    def __send_metric(self, kvp_list):
        kvp_list = [{'restic.last_report.time': int(time.time())}, *kvp_list]

        metrics = []
        
        for kvp in kvp_list:
            for key in kvp.keys():
                metrics.append(ZabbixMetric(hostname_fqdn, key, kvp[key]))

        logging.debug(f'Sending metrics: {metrics}')
        response = ZabbixSender(use_config=zabbix_config).send(metrics)
        logging.debug(f'ZBX Response: {response}')


    def __send_status_metrics(self, backup_name, pobj):
        metrics_kvp = []
        
        metrics_kvp.append({self.__zbx_hkey(backup_name, 'time'): int(time.time())})

        if 'percent_done' in pobj:
            # I've noticed that restic will sometimes send a percentage > 1.0, so we
            # are going to filter out any values that are outside the acceptable range
            if pobj['percent_done'] >= 0 and pobj['percent_done'] <= 1.0:
                metrics_kvp.append({self.__zbx_hkey(backup_name, 'percent_done'): int(round(pobj['percent_done'] * 100, 0))})

                # when the percentage complete is out of range, the elapsed and remaining seconds also
                # seem to be way off as well, so we sip these if percentage is out of range
                if 'elapsed_seconds' in pobj:
                    metrics_kvp.append({self.__zbx_hkey(backup_name, 'elapsed_seconds'): pobj['elapsed_seconds']})
                if 'seconds_remaining' in pobj:
                    metrics_kvp.append({self.__zbx_hkey(backup_name, 'seconds_remaining'): pobj['seconds_remaining']})


        if 'total_files' in pobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'total_files'): pobj['total_files']})
        if 'files_done' in pobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'files_done'): pobj['files_done']})
        if 'total_bytes' in pobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'total_bytes'): pobj['total_bytes']})
        if 'bytes_done' in pobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'bytes_done'): pobj['bytes_done']})

        self.__send_metric(metrics_kvp)


    def __send_finished_metrics(self, backup_name, fobj):
        metrics_kvp = []

        metrics_kvp.append({self.__zbx_hkey(backup_name, 'time'): int(time.time())})

        # todo: calculate files total here

        if 'files_new' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'files_new'): fobj['files_new']})
        if 'files_changed' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'files_changed'): fobj['files_changed']})
        if 'files_unmodified' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'files_unmodified'): fobj['files_unmodified']})
        if 'dirs_new' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'dirs_new'): fobj['dirs_new']})
        if 'dirs_changed' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'dirs_changed'): fobj['dirs_changed']})
        if 'dirs_unmodified' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'dirs_unmodified'): fobj['dirs_unmodified']})
        if 'data_added' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'data_added'): fobj['data_added']})
        if 'total_files_processed' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'total_files_processed'): fobj['total_files_processed']})
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'files_done'): fobj['total_files_processed']})
        if 'total_bytes_processed' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'total_bytes_processed'): fobj['total_bytes_processed']})
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'bytes_done'): fobj['total_bytes_processed']})
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'total_bytes'): fobj['total_bytes_processed']})
        if 'total_duration' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'elapsed_seconds'): int(round(fobj['total_duration'], 0))})
        if 'snapshot_id' in fobj:
            metrics_kvp.append({self.__zbx_hkey(backup_name, 'snapshot_id'): fobj['snapshot_id']})
        
        metrics_kvp.append({self.__zbx_hkey(backup_name, 'seconds_remaining'): 0})
        metrics_kvp.append({self.__zbx_hkey(backup_name, 'percent_done'): 100})

        self.__send_metric(metrics_kvp)


    def test(self):
        backup.__send_metric([])


if len(sys.argv) < 2:
    show_usage()

mode = sys.argv[1]

if mode != 'publish' and len(sys.argv) < 3:
    show_usage()

if len(sys.argv) > 2:
    backup_name = sys.argv[2]

backup = ResticBackup()

if mode == 'publish':
    backup.send_discovery()
elif mode == 'backup':
    backup.run_backup(backup_name)
else:
    show_usage(f'Unsupported mode: {mode}')
