#! /usr/bin/python

from __future__ import print_function, division
import time
import logging
import subprocess
import os
import smtplib
from email.mime.text import MIMEText
from abc import ABCMeta, abstractproperty, abstractmethod

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

class Checker:
    """Abstract base class (ABC) for classes which check on the state of
    a particular part of the system. """
    
    
    __metaclass__ = ABCMeta

    FAIL = 0
    OK = 1

    def __init__(self, name):
        self.name = name
        self.last_state = self.state

    @abstractproperty
    def state(self):
        pass
    
    @property
    def state_as_str(self):
        return ['FAIL', 'OK'][self.state]
    
    @property
    def just_changed_state(self):
        state = self.state # cache to avoid this changing under us        
        if state == self.last_state:
            return False
        else:
            logging.info('state change: {}'.format(self))
            self.last_state = state
            return True
    
    def __str__(self):
        return '{} = {}'.format(self.name, self.state_as_str)
    

class Process(Checker):

    @property
    def pid(self):
        pid_string = subprocess.check_output(['pidof', '-x', self.name])
        return pid_string.strip()
    
    def restart(self):
        logging.info("Attempting to restart {}".format(self.name))
        try:
            subprocess.Popen(self.restart_command.split())
        except Exception:
            logging.exception("Failed to restart. {}".format(self))
        else:
            logging.info("Successfully restarted. {}".format(self) )

    @property
    def state(self):
        try:
            self.pid
        except subprocess.CalledProcessError:
            return Checker.FAIL
        else:
            return Checker.OK


class File(Checker):
    def __init__(self, name, threshold=120):
        """File constructor
        
        Args:
            name = including full path
            threshold = time in seconds after which this file is considered 
                overdue
        """
        self.threshold = threshold
        super(File, self).__init__(name)

    @property
    def state(self):
        return self.seconds_since_modified < self.threshold

    @property
    def message(self):
        return "{} was last modified {:.1f}s ago".format(
                        self.name, self.seconds_since_modified)        

    @property
    def seconds_since_modified(self):
        return time.time() - self.last_modified

    @property        
    def last_modified(self):
        return os.path.getmtime(self.name)
    
    def __str__(self):
        msg = super(File, self).__str__()
        msg += " last modified {:.1f}s ago".format(self.seconds_since_modified)
        return msg
    

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
    checkers = [iam_logger, ntpd]
    
    checkers.append(File('/home/jack/workingcopies/domesticPowerData/BellendenRd/version2/channel_99.dat',
                      200))
    
    send_email(body="IAM logger babysitter running.\n{}\n{}".format(iam_logger, ntpd),
               subject="babysitter.py running")
    
    while True:
        msg = ""
        for checker in checkers:
            if checker.just_changed_state:
                msg += str(checker) + "\n"
                if isinstance(checker, Process):
                    msg += "Attempting to restart...\n"
                    checker.restart()
                    time.sleep(5)
                    msg += str(checker) + "\n"
                            
        if msg != "":
            send_email(body=msg, subject="iam_logger errors.")
    
        time.sleep(10)

if __name__ == "__main__":
    main()