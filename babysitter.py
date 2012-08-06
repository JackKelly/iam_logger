#! /usr/bin/python

from __future__ import print_function, division
import time
import sys
import logging
import subprocess
import os
import smtplib
from email.mime.text import MIMEText

"""

Requirements:

**********************************
SETUP SUDO FOR service restart ntp
**********************************
This Python script needs to be able to run 'service restart ntp'
without requiring a password.  Enable this by:
1. run 'sudo visudo'
2. add the following line: 'USER    ALL=NOPASSWD: /usr/sbin/service ntp *'
   (replace USER with your username!) 

"""

"""
TODO: take in config options for files to monitor, processes etc... or at least
      make it clearer where to modify these consts.

TODO: check disk space!

TODO: attach log to email.

"""

class Process(object):

    def __init__(self, name):
        self.name = name
        self.existed_last_time_checked = self.exists

    @property
    def pid(self):
        pid_string = subprocess.check_output(['pidof', '-x', self.name])
        return pid_string.strip()
    
    def restart(self):
        logging.info("Attempting to restart {}".format(self.name))
        try:
            subprocess.Popen(self.restart_command.split())
        except Exception:
            logging.warning("Failed to restart {}".format(self.name))
            logging.exception("")
        else:
            logging.info("Successfully restarted {}".format(self.name))

    @property
    def exists(self):
        try:
            self.pid
        except subprocess.CalledProcessError:
            return False
        else:
            return True
        
    def has_just_failed(self):
        if (not self.exists) and self.existed_last_time_checked:
            logging.warning("{} has just failed.".format(self.name))
            response = True
        else:
            response = False
        
        self.existed_last_time_checked = self.exists
        return response
    
    def __str__(self):
        return "process '{}' exists = {}".format(
                self.name, self.exists)


class File(object):
    def __init__(self, filename, threshold=120):
        """File constructor
        
        Args:
            filename = including full path
            threshold = time in seconds after which this file is considered 
                overdue
        """
        self.filename = filename
        self.threshold = threshold
        self.last_overdue_state = self.overdue
        
    @property
    def just_overdue(self):
        """Has the file only just recently become overdue?"""
        overdue = self.overdue
        if not self.last_overdue_state and self.overdue:
            response = True
            logging.warning("{} has not been modified for {:.1f}s".format(
                                 self.filename, self.seconds_since_modified))
            
        else:
            response = False
            
        self.last_overdue_state = overdue
        return response

    @property
    def overdue(self):
        return self.seconds_since_modified > self.threshold

    @property
    def seconds_since_modified(self):
        return time.time() - self.last_modified

    @property        
    def last_modified(self):
        return os.path.getmtime(self.filename)
    
    @property
    def filesize(self):
        return os.path.getsize(self.filename)


def send_email(body, subject):
    hostname = os.uname()[1]
    me = hostname + '<jack-list@xlk.org.uk>'
    you = 'jack@jack-kelly.com' 
    body += '\nUnixtime = ' + str(time.time()) + '\n'       
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] =  me
    msg['To'] = you
    
    logging.debug('sending message: \n{}'.format(msg.as_string()))
    
    retry = True
    while retry is True:
        try:
            logging.debug("SMPT_SSL")
            s = smtplib.SMTP_SSL('mail.xlk.org.uk')
            logging.debug("sendmail")                
            s.sendmail(me, [you], msg.as_string())
            logging.debug("quit")
            s.quit()
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
            logging.exception("")
            time.sleep(2)
        else:
            logging.info("Successfully sent message")
            retry = False


def main():

    logging.basicConfig(filename='babysitter.log', level=logging.DEBUG,
                        format='%(asctime)s level=%(levelname)s: '
                        'function=%(funcName)s'
                        '\n   %(message)s')
    logging.debug('MAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    iam_logger = Process('iam_logger.py')
    iam_logger.restart_command = 'nohup ./iam_logger.py'
    ntpd = Process('ntpd')
    ntpd.restart_command = 'sudo service ntp restart'
    processes = [iam_logger, ntpd]
    
    files = []
    files.append(File('/home/jack/workingcopies/domesticPowerData/BellendenRd/version2',
                      200))
    
    send_email(body="IAM logger babysitter running.\n{}\n{}".format(iam_logger, ntpd),
               subject="babysitter.py running")
    
    while True:
        msg = ""
        for process in processes:
            if process.has_just_failed():
                msg += "{} had just failed\n".format(process.name)
                msg += "Attempting to restart...\n"
                process.restart()
                time.sleep(5)
                msg += "Attempted to restart {}.  New run state = {}.\n".format(
                        process.name, process.exists)
                
        for f in files:
            if f.just_overdue:
                msg += "{} has not been modified for {:.1f}s".format(
                                 f.filename, f.seconds_since_modified)
        
        if msg != "":
            send_email(body=msg, subject="iam_logger errors.")
    
        time.sleep(10)

if __name__ == "__main__":
    main()