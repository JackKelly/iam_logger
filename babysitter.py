#! /usr/bin/python

from __future__ import print_function, division
import time
import logging.handlers
import subprocess
import os
import smtplib
from email.mime.text import MIMEText
from abc import ABCMeta, abstractproperty
import xml.etree.ElementTree as ET # for XML parsing
import signal
import sys

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

# TODO: check disk space!
# TODO: attach log to email.


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
            logger.info('state change: {}'.format(self))
            self.last_state = state
            return True
    
    def __str__(self):
        return '{} = {}'.format(self.name.rpartition('/')[2], # remove path
                                self.state_as_str)
    

class Process(Checker):

    def __init__(self, name):
        self.restart_string = None
        super(Process, self).__init__(name)

    @property
    def pid(self):
        pid_string = subprocess.check_output(['pidof', '-x', self.name])
        return pid_string.strip()
    
    def restart(self):
        if self.restart_string is None:
            logger.info("No restart string for {}".format(self.name))
            return
        
        logger.info("Attempting to restart {}".format(self.name))
        try:
            subprocess.Popen(self.restart_command.split())
        except Exception:
            logger.exception("Failed to restart. {}".format(self))
        else:
            logger.info("Successfully restarted. {}".format(self) )

    @property
    def state(self):
        try:
            self.pid
        except subprocess.CalledProcessError:
            return Checker.FAIL
        else:
            return Checker.OK


class File(Checker):
    def __init__(self, name, timeout=120):
        """File constructor
        
        Args:
            name (str) : including full path
            timeout (int or str) : time in seconds after which this file is 
                considered overdue.
        """
        self.timeout = int(timeout)
        super(File, self).__init__(name)

    @property
    def state(self):
        return self.seconds_since_modified < self.timeout     

    @property
    def seconds_since_modified(self):
        return time.time() - self.last_modified

    @property        
    def last_modified(self):
        return os.path.getmtime(self.name)
    
    def __str__(self):
        msg = super(File, self).__str__()
        msg += ", last modified {:.1f}s ago.".format(self.seconds_since_modified)
        return msg
    

class Manager(object):
    """Manages multiple Checker objects"""
    
    def __init__(self):
        self._checkers = []
        
    def append(self, checker):
        self._checkers.append(checker)
        logger.info('Added Checker to Manager: {}'.format(self._checkers[-1]))
        
    def run(self):
        msg = "IAM logger babysitter running.\n{}".format(self)
        self.send_email(body=msg, subject="babysitter.py running")
        
        while True:
            msg = ""
            for checker in self._checkers:
                if checker.just_changed_state:
                    msg += str(checker) + "\n"
                    if isinstance(checker, Process):
                        msg += "Attempting to restart...\n"
                        checker.restart()
                        time.sleep(5)
                        msg += str(checker) + "\n"
                            
            if msg != "":
                self.send_email(body=msg, subject="iam_logger errors.")
    
            time.sleep(10)
            
    def load_config(self, config_file):
        config_tree = ET.parse(config_file)

        self.SMTP_SERVER = config_tree.findtext("smtp_server")
        self.EMAIL_FROM  = config_tree.findtext("email_from")
        self.EMAIL_TO    = config_tree.findtext("email_to")
        self.USERNAME    = config_tree.findtext("username")
        self.PASSWORD    = config_tree.findtext("password")
    
        logger.info('SMTP_SERVER={}\nEMAIL_FROM={}\nEMAIL_TO={}'
                     .format(self.SMTP_SERVER, self.EMAIL_FROM, self.EMAIL_TO))
    
        files_etree = config_tree.findall("file")
        for f in files_etree:
            self.append(File(f.findtext('location'), 
                             int(f.findtext('timeout'))))
            
        processes_etree = config_tree.findall("process")
        for process in processes_etree:
            p = Process(process.findtext('name'))
            p.restart_command = process.findtext('restart_command')
            self.append(p)

    def send_email(self, body, subject):
        hostname = os.uname()[1]
        me = hostname + '<' + self.EMAIL_FROM + '>'
        body += '\nUnixtime = ' + str(time.time()) + '\n'       
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] =  me
        msg['To'] = self.EMAIL_TO
    
        logger.debug('sending message: \n{}'.format(msg.as_string()))
    
        retry = True
        while retry is True:
            try:
                logger.debug("SMPT_SSL")
                s = smtplib.SMTP_SSL(self.SMTP_SERVER)
                logger.debug("logging in")
                s.login(self.USERNAME, self.PASSWORD) # TODO take these from config!
                
                logger.debug("sendmail")                
                s.sendmail(me, [self.EMAIL_TO], msg.as_string())
                logger.debug("quit")
                s.quit()
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError):
                logger.exception("")
                time.sleep(2)
            except smtplib.SMTPAuthenticationError:
                error_msg = "SMTP authentication error. Please check username and password in config file."
                print(error_msg)
                logger.exception(error_msg)
                raise
            else:
                logger.info("Successfully sent message")
                retry = False
        
    def __str__(self):
        msg = ""
        for checker in self._checkers:
            msg += '{}\n'.format(checker)
        return msg


def _init_logger():
    global logger

    # create logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # create console handler for stderr
    ch_stderr = logging.StreamHandler()
    ch_stderr.setLevel(logging.INFO)
    stderr_formatter = logging.Formatter('%(asctime)s %(levelname)s: '
                        '%(message)s', datefmt='%d/%m/%y %H:%M:%S')
    ch_stderr.setFormatter(stderr_formatter)
    logger.addHandler(ch_stderr)
    
    # create file handler for babysitter.log
    fh = logging.FileHandler('babysitter.log')
    fh.setLevel(logging.DEBUG)
    fh_formatter = logging.Formatter('%(asctime)s level=%(levelname)s: '
                        'function=%(funcName)s, thread=%(threadName)s'
                        '\n   %(message)s')
    fh.setFormatter(fh_formatter)    
    logger.addHandler(fh)


def _shutdown():
    logger.info("Shutting down.")
    logging.shutdown() 
        
        
def _signal_handler(signal_number, frame):
    raise KeyboardInterrupt()


def main():
    
    _init_logger()
    logger.debug('MAIN: babysitter.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # register SIGINT and SIGTERM handler
    logger.info("MAIN: setting signal handlers")
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Wrap this in try... except so we can send any unexpected exceptions
    # to logging
    try:
        manager = Manager()
        manager.load_config("babysitter_config.xml")    
        manager.run()
    except KeyboardInterrupt:
        _shutdown()
    except Exception:
        logger.exception("")
        _shutdown()
        raise
    

if __name__ == "__main__":
    main()