#! /usr/bin/python
from __future__ import print_function, division
import serial # for pulling data from Current Cost
import xml.etree.ElementTree as ET # for XML parsing
import time
import sys
import os
import argparse
import threading
import signal

# TODO: Order new current cost and some more IAMs (check blog)

"""
 TODO: Automatically transfer files from study computer to living
 room computer for checking; then automatically upload to github once a week.
 
 The network might block us for a while so have a separate thread for each
 Current Cost's network access.  These threads will block until their relevant
 CurrentCost has new data to send.  Attempt to send new data.
 
"""

# TODO: Write a script to check sync between aggregate files on both computers

# TODO: Script which checks that certain files are being updated and emails me if not.

# TODO: Get old laptop running in office for logging.


#==============================================================================
# GLOBALS
#==============================================================================

_abort = False # Make this True to halt all threads
_directory = None # The _directory to write data to. Set by config.xml


#==============================================================================
# UTILITY FUNCTIONS
#==============================================================================

def print_to_stdout_and_stderr(msg):
    print(msg)
    print(msg, file=sys.stderr)


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
        raise IAMLoggerError("ERROR in radioIDs.dat. Duplicate {} found: {}\n"
                        .format(label, duplicates))        


def load_config():
    """Load config data from config files and init Current Costs.
    
    Sets global _directory variable.
    
    For each "serialport" listed in config.xml, init a new CurrentCost.
    
    Load and process radioIDs.dat.  Check for duplicate radioIDs and channels.
    
    Write a _directory/labels.dat file which maps channel number to label
    as per the original REDD file format.
    
    Returns:
        a list of initialised CurrentCost objects.
    
    """
    config_tree   = ET.parse("config.xml") # load config from config file
    global _directory
    _directory    = config_tree.findtext("directory") # File to save data to
    serials_etree = config_tree.findall("serialport")

    # Start a CurrentCost for each serial port in config.xml
    current_costs = []
    for serial_port in serials_etree:
        current_costs.append(CurrentCost(serial_port.text))
        
    # Loading radio_id mappings
    try:
        radio_id_fh = open("radioIDs.dat", "r") # "fh" = file handle
    except IOError, e: # file not found
        print("radio_ids.dat file not found. Ignoring.", str(e),
              sep="\n", file=sys.stderr)
    else:
        lines = radio_id_fh.readlines()
        radio_id_fh.close()

        sensors = {} # map radio IDs to Sensors
        radio_ids = [] # used to check for duplicate radio IDs
        channels = [] # used to check for duplicate channel numbers
        channel_map = {} # map channel to label (for creating labels.dat)

        for line in lines:
            partition = line.partition('#') # ignore comments
            fields = partition[0].strip().split()
            if len(fields) == 3:
                channel, label, radio_id = fields
                radio_id = int(radio_id)
                sensors[radio_id] = Sensor(radio_id, channel, label)
                radio_ids.append(radio_id)
                channels.append(channel)
                channel_map[channel] = label

        try:                        
            check_for_duplicates(radio_ids, 'radio_ids')
            check_for_duplicates(channels, 'channels')
        except IAMLoggerError, e: # duplicates found        
            print(str(e))
            raise
        
        CurrentCost.sensors = sensors
        
        # Write labels.dat file to disk
        labels_fh = open(_directory + 'labels.dat', 'w') # fh = file handle
        channel_keys = channel_map.keys()
        channel_keys.sort()
        for channel_key in channel_keys:
            labels_fh.write('{} {}\n'.format(channel_key, 
                                             channel_map[channel_key]))
        labels_fh.close()

    return current_costs


def _signal_handler(signal_number, frame):
    """Handle SIGINT and SIGTERM.
    
    Required to handle events like CTRL+C and kill.  Sets _abort to True
    to tell all threads to terminate ASAP.
    """
    
    signal_names = {signal.SIGINT: 'SIGINT', signal.SIGTERM: 'SIGTERM'}
    print("\nSignal {} received.".format(signal_names[signal_number]))
    global _abort
    _abort = True


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
        self.time_info.update()
        self.watts = watts
        self.location = Location(cc_channel, current_cost) 
        
        if str(self.location) in self.locations.keys():
            self.locations[ str(self.location) ] += 1
        else:
            self.locations[ str(self.location) ]  = 0
        
        self.write_to_disk()

    def __str__(self):
        return Sensor._STR_FORMAT.format(self.label, self.channel,
                                       self.location.cc_channel, self.watts, 
                                       self.time_info, self.radio_id, 
                                       self.locations) 

    def write_to_disk(self):
        """Dump a line of data to this Sensor's output file."""
        
        timecode = int(round(self.time_info.last_seen))
        
        # First check to see if we've already written this to disk 
        # (possibly because multiple _current cost monitors hear this sensor)
        if timecode == self._last_timecode_written_to_disk:
            print("Timecode {} already written to disk. Label={}, watts={}, "
                  "location={}".format(timecode, self.label, self.watts,
                                        self.location), file = sys.stderr)
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
        threading.Thread.__init__(self)
        self.print_xml = False
        self.port = port
        self.serial = None
        self.local_sensors = {}

        try:
            self._open_port()
        except (OSError, serial.SerialException):
            global _abort            
            _abort = True
            raise
        
        self._get_info()

    def _open_port(self):
        """Open the serial port."""
        
        if self.serial is not None and self.serial.isOpen():
            print("Closing serial port {}\n".format(self.port),
                   file=sys.stderr)
            try:
                self.serial.close()
            except Exception:
                pass
         
        print("Opening serial port ", self.port, file=sys.stderr)
        
        try:
            self.serial = serial.Serial(self.port, 57600)
        except OSError, e:
            print("Serial port " + self.port + 
                  " unavailable.  Is another process using it?", 
                  str(e), sep="\n", file=sys.stderr)
            raise
        except serial.SerialException, e:
            print("serial.SerialException:", str(e), 
                  "Is the correct USB port specified in config.xml?\n",
                   sep="\n", file=sys.stderr)
            raise
        
        self.serial.flushInput()

    def run(self):
        """This is what the threading framework runs."""
        
        global _abort
        try:
            if self.print_xml: # Just print XML to the screen
                while not _abort:
                    print(str(self.port), self.readline(), sep="\n")
            else:            
                while not _abort:
                    self.update()
        except Exception: # catch any exception
            _abort = True
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
        except OSError, e: # catch errors raised by serial.readline
            print("Serial port " + self.port + 
                  " unavailable.  Is another process using it?",
                  str(e), sep="\n", file=sys.stderr)
            raise
        except serial.SerialException, e:
            print("SerialException on port {}:".format(self.port), str(e),
                  "Has the device been unplugged?\n", 
                  sep="\n", file=sys.stderr)
            raise
        except ValueError, e: # Attempting to use a port that is not open
            print("ValueError: ", str(e), sep="\n", file=sys.stderr)
            raise
        
        return line

    def reset_serial(self, retry_attempt):
        """Reset the serial port.
        
        Args:
            retry_attempt (int): current retry number
        
        """ 
                   
        time.sleep(1) 
        print("retrying... reset_serial number {} of {}\n"
              .format(retry_attempt, CurrentCost.MAX_RETRIES), file=sys.stderr)
            
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
                print('XML error: ', str(e), line, sep='\n', file=sys.stderr)
            else:
                # Check if this is histogram data from the _current cost
                # (which we're not interested in)
                # (This could also be done by checking the size of 'line' 
                # - this would probably be faster although
                #  possibly the size of a "histogram" is variable)
                if tree.findtext('hist') is not None:
                    continue
                
                # Check if all the elements we're looking for exist in this XML
                success = True
                for key in data.keys():
                    data[key] = tree.findtext(key)
                    if data[key] is None:
                        success = False
                        print("Key \'{}\' not found in XML:\n{}"
                              .format(key, line), file=sys.stderr)
                        break
                    
                if success:
                    return data
                else:
                    continue
                                
        
        # If we get to here then we have failed after every retry    
        global _abort
        _abort = True
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
        data = {'id': None, 'sensor': None, 'ch1/watts': None}
        data = self.read_xml(data)
        # radio_id, hopefully unique to an IAM (but not necessarily unique):
        radio_id   = int(data['id'])
        cc_channel = int(data['sensor']) # channel on this Current Cost
        watts      = int(data['ch1/watts'])
        
        lock = threading.Lock()
        
        if radio_id not in CurrentCost.sensors.keys():
            print("making new Sensor for radio ID {}"
                  .format(radio_id),file=sys.stderr)
            lock.acquire()
            CurrentCost.sensors[radio_id] = Sensor(radio_id)
            lock.release()
        
        lock.acquire()
        CurrentCost.sensors[radio_id].update(watts, cc_channel, self)
        lock.release()
        
        # Maintain a local dict of sensors connected to this _current cost
        self.local_sensors[cc_channel] = CurrentCost.sensors[radio_id]

    def __str__(self):
        string  = "port      = {}\n".format(self.port)        
        string += "DSB       = {}\n".format(self.dsb)
        string += "Version   = {}\n\n".format(self.cc_version)    
        string += " "*41 + "|---PERIOD STATS (secs)---|\n"
        string += Sensor.HEADERS
        
        cc_channels = self.local_sensors.keys() # keyed by channel number
        cc_channels.sort()
        
        for cc_channel in cc_channels:
            sensor  = self.local_sensors[cc_channel]
            string += str(sensor)        
        
        string += "\n\n"
        
        return string


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
           
        global _abort
        _abort = True
        
        print_to_stdout_and_stderr("Stopping...")

        # Don't exit the main thread until our
        # worker CurrentCost threads have all quit
        for currentCost in self.current_costs:
            print_to_stdout_and_stderr("Waiting for monitor {} to stop..."
                                   .format(currentCost.port))
            currentCost.join()
            
        print_to_stdout_and_stderr("Done.")
            
    def __str__(self):
        string = ""             
        for current_cost in self.current_costs:
            string += str(current_cost)
            
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
                        'Useful for use with nohup command.')
    
    parser.add_argument('--print_xml', dest='print_xml', action='store_const',
                        const=True, default=False, help='Just dump XML from '
                        'the monitor(s) to std out. Do not log data. '
                        '(May not work on Windows)')
    
    args = parser.parse_args()

    # load config files and initialise Current Costs
    current_costs = load_config()

    # register SIGINT and SIGTERM handler
    print("setting signal handler")
    signal.signal(signal.SIGINT,  _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)    

    # initialise and run Manager
    manager = Manager(current_costs, args)        

    try:
        manager.run()
    except Exception:
        manager.stop()
        raise                

if __name__ == "__main__":
    main()
