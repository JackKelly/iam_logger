#! /usr/bin/python

"""
REQUIREMENTS:
    * gitpython           
"""

from __future__ import print_function, division
import serial # for pulling data from Current Cost
import xml.etree.ElementTree as ET # for XML parsing
import time
import datetime
import sys
import os
import argparse
import threading
import signal
import logging
try:
    import git # gitpython
except Exception, e:
    print("Failed to import git. Install gitpython using "
          "`sudo easy_install gitpython`.", file=sys.stderr)
    raise 

# TODO: Display sensor chan in stats

# TODO: Write a script to check sync between aggregate files on both computers

# TODO: Get old laptop running in office for logging.


#==============================================================================
# GLOBALS
#==============================================================================

_abort = False # Make this True to halt all threads
_directory = None # The _directory to write data to. Set by config.xml
_git_update_period = 60 * 60 # in seconds
_git_condition_variable = threading.Condition()

#==============================================================================
# UTILITY FUNCTIONS
#==============================================================================

def print_to_stdout_and_log(msg, level=logging.INFO):
    print(msg)
    logging.log(level, msg)
    # Logging messages simultaneously to stdout and log file could
    # be done by using a child logger


def check_for_duplicates(list_, label):
    """Check for duplicate entries in list_
    
    Raises:
        IAMLoggerError: duplicates found in list_
    
    """
    duplicates = {}
    for item in list_:
        count = list_.count(item)
        if count > 1 and item not in duplicates.keys():
            duplicates[item] = count
            
    if duplicates: # if duplicates contains any items
        raise IAMLoggerError("ERROR: Duplicate {} found: {}\n"
                        .format(label, duplicates))        


def load_config():
    """Load config data from config files and init Current Costs.
    
    Sets global _directory variable.
    
    For each "serialport" listed in config.xml, init a new CurrentCost.
    
    Returns:
        a list of initialised CurrentCost objects.
    
    """
    config_tree   = ET.parse("config.xml") # load config from config file
    
    # load _directory
    global _directory
    _directory    = config_tree.findtext("directory") # File to save data to
    if not _directory.endswith('/'):
        _directory = _directory + '/'
        
    # git update frequency
    global _git_update_period
    git_update_period = config_tree.findtext("gitupdatefrequency")
    if git_update_period is not None:
        _git_update_period = git_update_period
    
    # load serialports
    serials_etree = config_tree.findall("serialport")

    # Start a CurrentCost for each serial port in config.xml
    current_costs = []
    for serial_port in serials_etree:
        current_costs.append(CurrentCost(serial_port.text))
        
    load_radio_id_mapping('radioIDs.dat')

    return current_costs


def load_radio_id_mapping(filename):
    """Loads and processes radioIDs.dat or radioIDs_override.dat.
    
    Saves mapping from radio IDs to sensors in CurrentCost.sensors dict.
    
    If filename is not found then ignores (after printing an info message
    to stderr.)
    
    Args:
        filename (str): the filename to load.  e.g. "radioIDs.dat"

    Raises:
        IAMLoggerError: if duplicate channels or radioIDs are found
    """

    try:
        radio_id_fh = open(filename, "r") # "fh" = file handle
    except IOError, e: # file not found
        logging.info("LOADING CONFIG: {} file not found. Ignoring.\n{}"
                     .format((filename), str(e)))
    else:
        lines = radio_id_fh.readlines()
        radio_id_fh.close()

        radio_ids_and_s_chans = [] # used to check for duplicate radio IDs
        channels = [] # used to check for duplicate channel numbers
        labels = {} # map channel to label (for creating labels.dat)

        for line in lines:
            partition = line.partition('#') # ignore comments
            fields = partition[0].strip().split()
            if len(fields) == 3 or len(fields) == 4:
                channel, label, radio_id_and_s_chan = fields[:3]
                
                radio_id, dummy, s_chan = radio_id_and_s_chan.partition('/')
                if s_chan == '':
                    s_chan = 1
                
                radio_id = int(radio_id)
                s_chan   = int(s_chan)
                
                key = (radio_id, s_chan)
                
                CurrentCost.sensors[key] = Sensor(radio_id, 
                                                       channel, label)
                radio_ids_and_s_chans.append(key)
                channels.append(channel)
                labels[channel] = label
                
            if len(fields) == 4 and fields[3] == 'NEVER_ZERO':
                CurrentCost.sensors[key].never_zero = True;

        try:                        
            check_for_duplicates(radio_ids_and_s_chans, 'radio_ids_and_s_chans in {}'.format(filename))
            check_for_duplicates(channels, 'channels in {}'.format(filename))
        except IAMLoggerError, e: # duplicates found        
            logging.exception(str(e))
            raise
        
        # Write labels.dat file to disk
        # First check if file exists
        labels_filename = _directory + 'labels.dat'
        existing_labels = {}
        try:
            labels_fh = open(labels_filename, 'r')
        except IOError:
            logging.info(labels_filename + ' does not yet exist. Will create.')
        else:
            # Load existing labels.dat file
            lines = labels_fh.readlines()
            labels_fh.close()
            for line in lines:
                fields = line.split()
                if len(fields) == 2:
                    channel, label = fields
                    existing_labels[channel] = label
                
        # Merge existing_labels with labels if necessary
        if labels == existing_labels:
            logging.info("Existing labels.dat file already contains all "
                         "necessary labels so not writing to labels.dat.")
            # don't write to labels.dat
        else:
            if existing_labels != {}:
                logging.info("existing labels.dat is not empty and it isn't "
                             "the same as new labels so merging the two.")
                for e_channel, e_label in existing_labels.iteritems():
                    if e_channel not in labels.keys():
                        labels[e_channel] = e_label

            logging.info("Writing {} to disk.".format(labels_filename))                    
            labels_fh = open(labels_filename, 'w') # fh = file handle
            channels = labels.keys()
            channels.sort()
            for channel_key in channels:
                labels_fh.write('{} {}\n'.format(channel_key, 
                                                 labels[channel_key]))
            labels_fh.close()
    

def _abort_now(exception=None, notify_git=True):
    if exception is not None:
        print_to_stdout_and_log(str(exception), logging.CRITICAL )
    
    print_to_stdout_and_log("Aborting...")        
    global _abort
    _abort = True

    if notify_git:
        _notify_git()    


def _signal_handler(signal_number, frame):
    """Handle SIGINT and SIGTERM.
    
    Required to handle events like CTRL+C and kill.  Sets _abort to True
    to tell all threads to terminate ASAP.
    """
    
    signal_names = {signal.SIGINT: 'SIGINT', signal.SIGTERM: 'SIGTERM'}
    print_to_stdout_and_log("\nSignal {} received."
                                     .format(signal_names[signal_number]))
    _abort_now()


def _notify_git():
    logging.debug("Notifying git")
    _git_condition_variable.acquire()
    _git_condition_variable.notify()
    _git_condition_variable.release()    


def _alarm_handler(signal_number, frame):
    logging.info("_alarm_handler: SIGALRM alarm caught.")
    _notify_git()

#==============================================================================
# CLASSES
#==============================================================================


class IAMLoggerError(Exception):
    """Base class for errors in iam_logger."""


class TimeInfo(object):    
    """Record simple statistics about the time each Sensor is updated.
    
    Useful for finding IAMs which produce intermittent data.
    
    Static attributes:
            
        - HEADERS (str): column headers
    
    Attributes:
    
        - last_seen (float): Unix timecode.
    
    """
    
    _STR_FORMAT = '{:>7.2f}{:>7.1f}{:>7.1f}{:>7.1f}{:>7d}'
    _STR_FORMAT_TXT = '{:>7}{:>7}{:>7}{:>7}{:>7}' 
    HEADERS = _STR_FORMAT_TXT.format('MEAN', 'MAX', 'MIN', 'LAST', 'COUNT') 
    
    def __init__(self):
        self._count = -1
        self.last_seen =  0

    def update(self):
        """Get time now. Calculate time since last update.  
        Use this to update period statistics.
           
        """
        unix_time = time.time()
        self._count += 1        
        self._current = unix_time - self.last_seen
        
        if self._count == 0: # this is the first time we've run
            self._current = None
            self._mean = None
            self._max  = None
            self._min  = None
        elif self._count == 1:
            self._mean = self._current
            self._max  = self._current
            self._min  = self._current
        else:
            self._mean = (((float(self._mean) * (self._count - 1)) + self._current) 
                         / self._count)
            if self._current > self._max: self._max = self._current
            if self._current < self._min: self._min = self._current
            
        self.last_seen = unix_time

    def __str__(self):
        if self._count < 1:
            return TimeInfo._STR_FORMAT_TXT.format('-','-','-','-',self._count)
        else:
            return TimeInfo._STR_FORMAT.format(self._mean, self._max, 
                                             self._min, self._current, 
                                             self._count)


class Location(object):
    """Simple struct for representing the physical 'location' of a sensor.
    
    The 'location'  means the combination of Current Cost instance and 
    the cc_channel on that CC.
    
    Attributes:
        cc_channel (int): Current Cost channel.
        
        current_cost (CurrentCost): a CurrentCost object.
    """
       
    def __init__(self, cc_channel, current_cost):
        """Construct a Location object.
        
        Args:
            cc_channel (int): Current Cost channel.
            current_cost (CurrentCost): a CurrentCost object
        
        """
        
        self.cc_channel = cc_channel         
        self.current_cost = current_cost

    def __str__(self):
        return '{} {}'.format(self.current_cost.port, self.cc_channel)
    
    def __repr__(self):
        return 'Location({})'.format(str(self))


class Sensor(object):
    """Represent physical sensors: IAMs and CT clamps.
    
    Static attributes:

        HEADERS (str): human-readable column HEADERS:  
    
    Attributes:
        time_info (TimeInfo): statistics describing how frequently this
            sensor is updated.
            
        location (Location): the physical location of this Sensor.
        
        locations (list): every Location this sensor has been observed
            since this instance of iam_logger started running.
        
        radio_id (int): the radio ID used to identify this Sensor. Radio IDs
            *should* be unique to this Sensor but the Current Cost IAMs
            have no way to enforce uniqueness so conflicts can occur.
            (At startup, the load_config function checks for conflicting 
            radio IDs in the radioID.dat file.)
            
        channel (str): this Sensor's channel number (unique to this Sensor)
            taken from config file radioIDs.dat. 'channel' will not exist
            if there is no radio_id entry in radioIDs.dat for this sensor.
            
        label (str): human-readable label for this Sensor
            (taken from radioIDs.dat).
            
        watts (int): instantaneous power measured in watts.
        
        never_zero (bool): True if this sensor's measurement can never be zero.
            Default = False.  Useful for aggregate sensors.  Sometimes the CC
            records an aggregate reading of zero, which is clearly wrong.
    
    """
    
    # string to format both numeric data and human-readable column HEADERS:
    _STR_FORMAT = '{:>20.20} {:>4} {:>6} {:>5} {} {:>7} {}\n'
    HEADERS = _STR_FORMAT.format('LABEL', 'CHAN', 'CCchan', 'WATTS',
                                TimeInfo.HEADERS, 'RADIOID', 'LOCATIONS') 
    
    def __init__(self, radio_id, channel='-', label='-'):
        """Construct a Sensor.
        
        Args:
        
            radio_id (int): the radio ID unique to this physical sensor.
            
        Kwargs:
        
            channel (str): the channel number given in radioIDs.dat and
                used in the filename for the data file.
                
            label (str): the human-readable name for the appliance this 
                sensor is connected to.  e.g. "TV"
        """
        
        self.time_info = TimeInfo()
        self.location = '-'                        
        self.locations = {} 
        self.radio_id = radio_id 
        self.channel = channel
        self.label = label
        self.watts = '-'
        self._last_timecode_written_to_disk = None
        self.never_zero = False

    def update(self, watts, cc_channel, current_cost):
        """Process a new sample.
        
        We use timestamp from local computer, not from the Current Cost.
        
        Args:
        
            watts (int): instantaneous power measured in watts.
            
            cc_channel (int): the Current Cost channel this sensor 
                appears on.
            
            current_cost (CurrentCost): the Current Cost this sensor
                appears on
        
        """
        
        # Sometimes the Current Cost incorrectly claims the aggregate power
        # consumption is 0 watts.  This is incredibly unlikely so if this
        # happens then assume this is an error and ignore.
        if self.never_zero and watts == 0:
            return
        
        self.time_info.update()
        self.watts = watts
        self.location = Location(cc_channel, current_cost) 
        
        if str(self.location) in self.locations.keys():
            self.locations[ str(self.location) ] += 1
        else:
            self.locations[ str(self.location) ]  = 0
        
        self.write_to_disk()

    def __str__(self):
        if self.never_zero:
            label = "*" + self.label
        else:
            label = self.label
        return Sensor._STR_FORMAT.format(label, self.channel,
                                       self.location.cc_channel, self.watts, 
                                       self.time_info, self.radio_id, 
                                       self.locations) 

    def write_to_disk(self):
        """Dump a line of data to this Sensor's output file."""
        
        timecode = int(round(self.time_info.last_seen))
        
        # First check to see if we've already written this to disk 
        # (possibly because multiple _current cost monitors hear this sensor,
        # but can also occur when the computer fails to receive serial
        # data as soon as it's available, for example if the computer
        # is heavily loaded with another task.  I could try to work around
        # this problem by somehow using the timecode from the CC when
        # the timecode from the computer makes little sense.)
        if timecode == self._last_timecode_written_to_disk:
            logging.warning("SENSOR: Timecode {} already written to disk. "
                         "Label={}, watts={}, location={}"
                         .format(timecode, self.label,
                                 self.watts, self.location))
            return
        
        self._last_timecode_written_to_disk = timecode
        
        if self.channel == '-':
            chan = self.radio_id
        else:
            chan = self.channel
        
        filename =  _directory + "channel_" + str(chan) + ".dat"
        filehandle = open(filename, 'a+')
        data = '{:d} {} {}\n'.format(timecode, self.watts, self.location)
        filehandle.write(data)
        filehandle.close()
        

class Manager(object):
    """Singleton. Used to manage multiple CurrentCost objects.
    
    Attributes:
    
        current_costs (list): list of CurrentCost objects
        
        args : command line arguments
    
    """

    def __init__(self, current_costs, args):
        self.current_costs = current_costs
        self.args = args
        
    def run(self):
        """Start each CurrentCost thread."""
        
        for current_cost in self.current_costs:
            current_cost.print_xml = self.args.print_xml
            current_cost.start()
        
        # Use this main thread of control to continually
        # print out info
        if self.args.print_xml:
            print("Press CTRL+C to stop.\n")
            signal.pause() # Note: signal.pause can't be used on Windows!
        elif self.args.noDisplay:
            self.write_stats_to_file()                
        else:
            self.write_stats_to_screen()
        
        self.stop()

    def write_stats_to_screen(self):
        while not _abort:
            os.system('clear')
            print(str(self))
            print("Press CTRL+C to stop.\n")                
            time.sleep(1)            

    def write_stats_to_file(self):
        print("Press CTRL+C to stop.\n")
        while not _abort:
            stats_file_handle = open("stats.dat", "w")            
            print(str(self), file=stats_file_handle)
            stats_file_handle.close()
            time.sleep(60)            

    def stop(self):
        """Gracefully attempt to bring the system to a halt.
        
        Specifically we ask every CurrentCost thread to stop 
        by setting '_abort' to True and then we wait patiently
        for every CurrentCost to return from its last blocked read.
        
        """

        # Don't exit the main thread until our
        # worker CurrentCost threads have all quit
        for currentCost in self.current_costs:
            print_to_stdout_and_log("Waiting for monitor {} to stop..."
                                   .format(currentCost.port))
            currentCost.join()        
            
    def __str__(self):
        string = ""             
        for current_cost in self.current_costs:
            string += str(current_cost)
            
        return string

#==============================================================================
# threading.Thread subclasses
# (i.e. classes which are instantiated as threads)
#==============================================================================

class _PushToGit(threading.Thread):
    """Simple little thread for pushing files to git.
    
    Will only terminate rest of program if git.Repo() fails.  If an error 
    occurs later then exception info will be logged but _PushToGit won't
    try to bring down the entire iam_logger app.
    """
    
    # GitPython tutorial here:
    # http://packages.python.org/GitPython/0.3.2/tutorial.html
    #
    # GitPython API refernce here:
    # http://packages.python.org/GitPython/0.3.2/reference.html
    
    def __init__(self):
        """Init Thread superclass and git repo."""
        
        threading.Thread.__init__(self, name="_PushToGit")
        try:
            self.repo = git.Repo(_directory)
            self.origin = self.repo.remotes.origin
            self.index = self.repo.index
        except Exception, e:
            _abort_now(exception=e, notify_git=False)
            raise
        else:
            logging.info("GIT: repo {}".format(_directory))                 
    
    def _try_to_release(self):
        try:
            _git_condition_variable.release()
        except RuntimeError, e: # "cannot release un-acquired lock"
            logging.info("Ignoring exception while trying to release git "
                         "condition variable: " + str(e))
            pass # we don't care if we haven't acquired lock yet
    
    def run(self):
        try:
            self._git_push() # do a git push at start-up
        except Exception:
            logging.exception("")
            
        while not _abort:
            # _git_condition_variable will be notified when SIGALRM fires.
            # (this is the mechanism by which we periodically push to git)
            _git_condition_variable.acquire()
            _git_condition_variable.wait()
            
            if _abort:
                break
            
            try:
                self._git_push()
            except Exception:
                logging.exception("GIT exception.")

            signal.alarm(_git_update_period)
            self._try_to_release()
        
        self._try_to_release()
            
    def _git_push(self):
        """Push to git remote (e.g. github)."""
    
        try:
            # pull to make sure we're up to date otherwise
            # push will fail.            
            logging.info("GIT: Starting git pull. If we get stuck here then "
                         "ensure that\n"
                         "        git pull can be executed without a password.")
            info = self.origin.pull()[0]
            logging.info("GIT: pull response: {}".format(info.note))            
            logging.info("GIT: running git add...")             
            self.index.add([_directory + '*.dat'])
            hostname = os.uname()[1]
            commit_msg = 'Automatic upload from {}.'.format(hostname)
            logging.info("GIT: commiting with message: {}".format(commit_msg))
            response = self.index.commit(commit_msg)             
            logging.info("GIT: commit response: {}".format(response))
            logging.info("GIT: running git push...")             
            info = self.origin.push()[0]
            logging.info("GIT: push response: {}".format(info.summary.strip()))
        except IndexError, e:
            logging.info("GIT: _git_push caught IndexError. This is probably "
                         "because we're trying to terminate\n"
                         "       while git push is running: " + str(e))
        except Exception:
            raise
        else:
            next_run_time = ((datetime.datetime.now() +
                              datetime.timedelta(seconds=_git_update_period))
                              .strftime('%H:%M:%S'))
            logging.info("GIT: Finished git push.  Will run again in "
                         "{} seconds time at {}.\n"
                         .format(_git_update_period, next_run_time))


class CurrentCost(threading.Thread):
    """Represents a physical Current Cost ENVI home energy monitor.
    
    Static attributes:
    
        sensors: dict of all Sensors, keyed by radio_id
    
    Attributes:
    
        print_xml (bool): True if we just want to print XML from the 
            Current Cost to stdout.  Defaults to False.
            
        port (str): Serial port e.g. "/dev/ttyUSB0"
        
        serial (serial.Serial): a serial.Serial object.
        
        local_sensors (dict): Dict of Sensors on this CurrentCost,
            keyed by cc_channel.
            
        dsb (str): Days since birth. Only set after calling get_info().
        
        cc_version (string): CurrentCost version number.  Only set after
            calling get_info().
    
    """

    sensors = {}
    
    MAX_RETRIES = 10

    def __init__(self, port):
        self.port = port        
        threading.Thread.__init__(self, name="cc_"+port)
        self.print_xml = False
        self.serial = None
        self.local_sensors = {}

        try:
            self._open_port()
        except (OSError, serial.SerialException), e:
            _abort_now(exception=e)
            raise
        
        self._get_info()

    def _open_port(self):
        """Open the serial port."""
        
        if self.serial is not None and self.serial.isOpen():
            logging.info("SERIAL: Closing serial port {}\n".format(self.port))
            try:
                self.serial.close()
            except Exception:
                pass
         
        logging.info("SERIAL: Opening serial port {}".format(self.port))
        
        try:
            self.serial = serial.Serial(self.port, 57600)
        except (OSError, serial.SerialException), e:
            self._handle_serial_port_error(e)
            raise
        else:
            logging.info("SERIAL: Opened serial port {}".format(self.port))            
        
        self.serial.flushInput()

    def _handle_serial_port_error(self, error):
        if isinstance(error, OSError):
            print_to_stdout_and_log("SERIAL: Serial port " + self.port + 
                  " unavailable.  Is another process using it?\n" + str(error),
                  logging.WARNING)
        elif isinstance(error, serial.SerialException):
            print_to_stdout_and_log("SERIAL: serial.SerialException: \n"+str(error)+ 
                  "\nIs the correct USB port specified in config.xml?\n",
                   logging.WARNING)
        
    def run(self):
        """This is what the threading framework runs."""
        
        try:
            if self.print_xml: # Just print XML to the screen
                while not _abort:
                    print(str(self.port), self.readline(), sep="\n")
            else:            
                while not _abort:
                    self.update()
        except Exception, e: # catch any exception
            _abort_now(exception=e)
            raise
    
    def readline(self):
        """Read a line from the serial port.  Blocking.
        
        On error, print useful message and raise the error.
        
        Returns:
            line (str): A line of XML from the Current Cost.
        
        Raises:
            OSError, serial.SerialException, ValueError
        """
        
        try:
            line = self.serial.readline()
        except (OSError, serial.SerialException), e:
            self._handle_serial_port_error(e)
            raise
        except ValueError, e: # Attempting to use a port that is not open
            logging.error("SERIAL: ValueError: " + str(e))
            raise
        
        return line

    def reset_serial(self, retry_attempt):
        """Reset the serial port.
        
        Args:
            retry_attempt (int): current retry number
        
        """ 
                   
        time.sleep(1) 
        logging.warning("SERIAL: retrying... retry number {} of {}\n"
              .format(retry_attempt, CurrentCost.MAX_RETRIES))
            
        # Try to flush the serial port.
        try:
            self.serial.flushInput()
        except Exception: # Ignore errors.  We're going to retry anyway.
            pass
            
        # Try to re-open the port.
        try:
            self._open_port()
        except Exception: # Ignore errors.  We're going to retry anyway.
            pass
        
    def read_xml(self, data):
        """Reads a line from the serial port and processes XML. 
        
        Args:
            data (dict): The keys = the elements we search for in the XML.
            
        Returns:
            data dict is returned with the correct fields
            filled in from the XML.
            
        Raises:
            IAMLoggerError: if we fail after CurrentCost.MAX_RETRIES.
        
        """

        for retry_attempt in range(CurrentCost.MAX_RETRIES):
            try:
                line = self.readline()
                tree = ET.XML(line)
            except (OSError, serial.SerialException, ValueError): 
                # raised by readline()
                self.reset_serial(retry_attempt)
            except ET.ParseError, e: 
                # Catch XML errors (occasionally the _current cost 
                # outputs malformed XML)
                logging.warning('XML error:\n{}\n{}'.format(str(e), line))
            else:
                # Check if this is histogram data from the _current cost
                # (which we're not interested in)
                # (This could also be done by checking the size of 'line' 
                # - this would probably be faster although
                #  possibly the size of a "histogram" is variable)
                if tree.findtext('hist') is not None:
                    continue
                
                for key in data.keys():
                    data[key] = tree.findtext(key)
                    
                return data                                
        
        # If we get to here then we have failed after every retry    
        raise IAMLoggerError('read_xml failed after {} retries'
                             .format(CurrentCost.MAX_RETRIES))

    def _get_info(self):
        """Get DSB (days since birth) and version
        number from Current Cost monitor.
        
        """
        data            = self.read_xml({'dsb': None, 'src': None})
        self.dsb        = data['dsb'] 
        self.cc_version = data['src']

    def update(self):
        """Read data from serial port and update relevant sensor.
        
        If data from serial port reveals a novel Sensor with a radio ID
        we have not seen before then create a new Sensor (with not label
        or channel).
        """

        # For Current Cost XML details, see currentcost.com/cc128/xml.htm
        data = {'id': None, 'sensor': None, 'ch1/watts': None,
                'ch2/watts': None, 'ch3/watts': None}
        data = self.read_xml(data)
        # radio_id, hopefully unique to an IAM (but not necessarily unique):
        radio_id   = int(data['id'])
        cc_channel = int(data['sensor']) # channel on this Current Cost
        watts      = []
        watts.append(data['ch1/watts'])
        watts.append(data['ch2/watts'])
        watts.append(data['ch3/watts'])
        
        # convert watts to ints
        for i in range(3):
            if watts[i] is not None:
                watts[i] = int(watts[i])
        
        lock = threading.Lock()
        
        for s_chan in range(1,4): # s_chan = sensor channel (e.g. multiple CT clamps)
            if watts[s_chan-1] is not None:
                key = (radio_id, s_chan)
                if key not in CurrentCost.sensors.keys():
                    logging.info("CURRENTCOST: making new Sensor for radio ID {} and s_chan {}"
                         .format(radio_id, s_chan))
                    lock.acquire()
                    CurrentCost.sensors[key] = Sensor(radio_id)
                    lock.release()
        
                lock.acquire()
                CurrentCost.sensors[key].update(watts[s_chan-1], cc_channel, self)
                lock.release()
        
                # Maintain a local dict of sensors connected to this _current cost
                self.local_sensors[(cc_channel, s_chan)] = CurrentCost.sensors[key]

    def __str__(self):
        string  = "port      = {}\n".format(self.port)        
        string += "DSB       = {}\n".format(self.dsb)
        string += "Version   = {}\n\n".format(self.cc_version)    
        string += " "*41 + "|---PERIOD STATS (secs)---|\n"
        string += Sensor.HEADERS
        
        cc_channels = self.local_sensors.keys() # keyed by (cc_channel number, sensor chan)
        cc_channels.sort()
        
        for cc_channel in cc_channels:
            sensor  = self.local_sensors[cc_channel]
            string += str(sensor)        
        
        string += "\n\n"
        
        return string


#==============================================================================
# MAIN FUNCTION
#==============================================================================

def main():    
    # Process command line args
    parser = argparse.ArgumentParser(description='Log data from multiple '
                                     'Current Cost IAMs.')
    
    parser.add_argument('--noDisplay', dest='noDisplay', action='store_const',
                        const=True, default=False, 
                        help='Do not display info to std out. ' 
                        'Will be enabled automatically if called with nohup.')
    
    parser.add_argument('--print_xml', dest='print_xml', action='store_const',
                        const=True, default=False, help='Just dump XML from '
                        'the monitor(s) to std out. Do not log data. '
                        '(May not work on Windows)')
    
    parser.add_argument('--log', dest='loglevel', type=str, default='DEBUG',
                        help='DEBUG or INFO or WARNING (default: DEBUG)')
    
    args = parser.parse_args()

    # Set up logging
    numeric_level = getattr(logging, args.loglevel.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: {}'.format(args.loglevel))
    logging.basicConfig(filename='iam_logger.log', level=numeric_level,
                        format='%(asctime)s level=%(levelname)s: '
                        'function=%(funcName)s, thread=%(threadName)s'
                        '\n   %(message)s')
    logging.debug('\nMAIN: iam_logger.py starting up. Unixtime = {:.0f}'
                  .format(time.time()))

    # Check if iam_logger.py is being run using nohup
    if not os.isatty(sys.stdout.fileno()):
        logging.info("stdout is not a TTY so let's assume this program\n"
                     "   has been called using nohup: enabling --noDisplay.")
        args.noDisplay = True

    # load config files and initialise Current Costs
    current_costs = load_config()    
    
    # register SIGINT and SIGTERM handler
    logging.info("MAIN: setting signal handlers")
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    # We use the ALARM signal to trigger a git push: 
    signal.signal(signal.SIGALRM, _alarm_handler)
    logging.info("MAIN: Setting SIGALRM with period = {}s."
                 .format(_git_update_period))
    signal.alarm(_git_update_period)

    # start git push
    git_push = _PushToGit()
    git_push.start()
    
    # initialise and run Manager
    manager = Manager(current_costs, args)        

    try:
        manager.run()
    except Exception:
        manager.stop()
        raise
    
    print_to_stdout_and_log("Waiting for git thread to stop...")                 
    git_push.join()
    print_to_stdout_and_log("Done. Unixtime = {:.0f}\n\n"
                            .format(time.time()))
    logging.shutdown()      

if __name__ == "__main__":
    main()
