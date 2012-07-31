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

# TODO: When in noDisplay mode, write stats summary to a file once a minute.

# TODO: Automatically transfer files from study computer to living room computer for checking; then automatically upload to github once a week.

# TODO: Write a script to check sync between aggregate files on both computers

# TODO: Script which checks that certain files are being updated and emails me if not.

# TODO: Get old laptop running in office for logging.

######################################
#     GLOBALS                        #
######################################

abort = False # Make this True to halt all threads
directory = None # The directory to write data to. Set by config.xml

class TimeInfo(object):
    """Class for recording simple statistics about the time each Sensor is updated.
       Useful for finding IAMs which produce intermittent data."""
    
    strFormat = '{:>7.2f}{:>7.1f}{:>7.1f}{:>7.1f}{:>7d}' # string for formatting numeric data
    strFormatTxt = '{:>7}{:>7}{:>7}{:>7}{:>7}' # string for formatting human-readable column headers
    headers   = strFormatTxt.format('MEAN', 'MAX', 'MIN', 'LAST', 'COUNT') # column headers
    
    def __init__(self):
        self.count    = -1
        self.lastSeen =  0

    def update(self):
        """Get time now. Calculate time since last update.  Use this to update period statistics."""
        unixTime = time.time()
        self.count += 1        
        self.current = unixTime - self.lastSeen
        
        if self.count == 0: # this is the first time we've run
            self.current = None
            self.mean = None
            self.max  = None
            self.min  = None
        elif self.count == 1:
            self.mean = self.current
            self.max  = self.current
            self.min  = self.current
        else:
            self.mean = ((float(self.mean) * (self.count - 1)) + self.current) / self.count
            if self.current > self.max: self.max = self.current
            if self.current < self.min: self.min = self.current
            
        self.lastSeen = unixTime


    def __str__(self):
        if self.count < 1:
            return TimeInfo.strFormatTxt.format('-','-','-','-',self.count)
        else:
            return TimeInfo.strFormat.format(self.mean, self.max, self.min, self.current, self.count)


class Location(object):
    """Simple struct for representing the physical 'location' of a sensor.  The 'location'
       means the combination of Current Cost instance and the ccChannel on that CC."""
    
       
    def __init__(self, ccChannel, currentCost):
        self.ccChannel = ccChannel # ccChannel = Current Cost Channel; to distinguish from 'channel' (which is specified in config file radioIDs.dat)
        self.currentCost = currentCost
    
        
    def __str__(self):
        return '{} {}'.format(self.currentCost.port, self.ccChannel)
    
    
    def __repr__(self):
        return 'Location({})'.format(str(self))

    
#####################################
#     SENSOR CLASS                  #
#####################################
class Sensor(object):
    """Class for representing physical sensors: Individual Appliance Monitors and CT clamps"""
    
    strFormat = '{:>20.20} {:>4} {:>6} {:>5} {} {:>7} {}\n' # string to format both numeric data and human-readable column headers
    headers   = strFormat.format('LABEL', 'CHAN', 'CCchan', 'WATTS', TimeInfo.headers, 'RADIOID', 'LOCATIONS') # human-readable column headers
    
    def __init__(self, radioID, channel='-', label='-'):
        self.timeInfo  = TimeInfo() # statistics summarising how frequently this Sensor updates.
        self.location  = '-' # physical location of this Sensor
        self.locations = {} # list of all physical locations this sensor has been seen on
        self.radioID   = radioID # this Sensor's radioID (unique to this Sensor)
        self.channel   = channel # this Sensor's channel number (also unique to this Sensor) taken from config file radioIDs.dat. 'channel' will not exist if there is no radioID entry in radioIDs.dat for this sensor.
        self.label     = label # human-readable label for this Sensor (taken from radioIDs.dat)
        self.watts     = '-'
        self.lastTimecodeWrittenToDisk = None
        
        
    def update(self, watts, ccChannel, currentCost):
        """Process a new sample.  We use timestamp from local computer, not from the Current Cost"""
        self.timeInfo.update()
        self.watts = watts
        self.location = Location(ccChannel, currentCost) 
        
        if str(self.location) in self.locations.keys():
            self.locations[ str(self.location) ] += 1
        else:
            self.locations[ str(self.location) ]  = 0
        
        self.writeToDisk()
        
        
    def __str__(self):
        return Sensor.strFormat.format(self.label, self.channel, self.location.ccChannel, self.watts, self.timeInfo, self.radioID, self.locations) 
    
    
    def writeToDisk(self):
        """Dump a line of data to this Sensor's output file"""
        timecode = int(round(self.timeInfo.lastSeen))
        
        # First check to see if we've already written this to disk (possibly because multiple current cost monitors hear this sensor)
        if timecode == self.lastTimecodeWrittenToDisk:
            print("Timecode {} already written to disk. Label={}, watts={}, location={}".format(
                   timecode, self.label, self.watts, self.location), file = sys.stderr)
            return
        
        self.lastTimecodeWrittenToDisk = timecode
        
        if self.channel == '-':
            chan = self.radioID
        else:
            chan = self.channel
        
        filename =  directory + "channel_" + str( chan ) + ".dat"
        filehandle = open(filename, 'a+')
        data = '{:d} {} {}\n'.format(timecode, self.watts, self.location)
        filehandle.write( data )
        filehandle.close()


#####################################
#     CURRENT COST CLASS            #
#####################################
class CurrentCost(threading.Thread):
    """Represents a physical Current Cost ENVI home energy monitor."""
    
    
    sensors = {} # Static variable.  A dict of all sensors; keyed by radioID.

    def __init__(self, port):
        threading.Thread.__init__(self)
        global abort        
        try:
            self.printXML = False # Should we be in "printXML" mode?
            self.port = port
            self.serial = None
            self._openPort()
            self._getInfo()
            self.localSensors = {} # Dict of references to Sensors on this CurrentCost; keyed by ccChannel
        except (OSError, serial.SerialException):
            abort = True
            raise


    def _openPort(self):
        """Open the serial port."""
        if self.serial != None and self.serial.isOpen():
            print("Closing serial port {}\n".format(self.port), file=sys.stderr)
            try:
                self.serial.close()
            except:
                pass
         
        print("Opening serial port ", self.port, file=sys.stderr)
        
        try:
            self.serial = serial.Serial(self.port, 57600)
            self.serial.flushInput()
        except OSError, e:
            print("Serial port " + self.port + " unavailable.  Is another process using it?", str(e), sep="\n", file=sys.stderr)
            raise
        except serial.SerialException, e:
            print("serial.SerialException:", str(e), "Is the correct USB port specified in config.xml?\n", sep="\n", file=sys.stderr)
            raise


    def run(self):
        """This is what the threading framework runs."""
        global abort
        try:
            if self.printXML == True: # Just print XML to the screen
                while abort == False:
                    print( str(self.port), self.readLine(), sep="\n" )
            else:            
                while abort == False:
                    self.update()
        except:
            abort = True
            raise
    
    
    def readLine(self):
        """Read a line from the serial port.  Blocking.  On error, print useful message and raise the error."""
        try:
            line = self.serial.readline()
        except OSError, e: # catch errors raised by serial.readline
            print("Serial port " + self.port + " unavailable.  Is another process using it?", str(e), sep="\n", file=sys.stderr)
            raise
        except serial.SerialException, e:
            print("SerialException on port {}:".format(self.port), str(e), "Has the device been unplugged?\n", sep="\n", file=sys.stderr)
            raise
        except ValueError, e: # Attempting to use a port that is not open
            print("ValueError: ", str(e), sep="\n", file=sys.stderr)
            raise
        
        return line


    def resetSerial(self, i, RETRIES):
        """Reset the serial port"""            
        time.sleep(1) 
        print("retrying... resetSerial number {} of {}\n".format(i, RETRIES), file=sys.stderr)
            
        # Try to flush the serial port.
        try:
            self.serial.flushInput()
        except: # Ignore errors.  We're going to retry anyway.
            pass
            
        # Try to re-open the port.
        try:
            self._openPort()
        except: # Ignore errors.  We're going to retry anyway.
            pass
        

    def readXML(self, data):
        """Reads a line from the serial port and returns an ElementTree. 
           'data' is a dict where the keys are the elements we search for in the XML.
           'data' is returned with the correct fields filled in from the XML."""
        RETRIES = 10
        for i in range(RETRIES):
            try:
                line = self.readLine()
                tree = ET.XML(line)
                
                # Check if this is histogram data from the current cost (which we're not interested in)
                # (This could also be done by checking the size of 'line' - this would probably be faster although
                #  possibly the size of a "histogram" is variable)
                if tree.findtext('hist') != None:
                    continue
                
                # Check if all the elements we're looking for exist in this XML
                success = True
                for key in data.keys():
                    data[key] = tree.findtext(key)
                    if data[key] == None:
                        success = False
                        print("Key \'{}\' not found in XML:\n{}".format(key, line), file=sys.stderr)
                        break
                    
                if success:
                    return data
                else:
                    continue
                
            except (OSError, serial.SerialException, ValueError): # raised by readLine()
                self.resetSerial(i, RETRIES)
            except ET.ParseError, e: # Catch XML errors (occasionally the current cost outputs malformed XML)
                print("XML error: ", str(e), line, sep="\n", file=sys.stderr)
                self.resetSerial(i, RETRIES)
        
        # If we get to here then we have failed after every retry    
        global abort
        abort = True
        raise Exception("readXML failed after {} retries".format(RETRIES))
        

    def _getInfo(self):
        """Get DSB (days since birth) and version number from Current Cost monitor"""
        data = {'dsb': None, 'src': None}
        data           = self.readXML( data )
        self.dsb       = data['dsb'] 
        self.ccVersion = data['src']
        

    def update(self):
        """Read data from serial port."""

        # For Current Cost XML details, see currentcost.com/cc128/xml.htm
        data = {'id': None, 'sensor': None, 'ch1/watts': None}
        
        data      = self.readXML( data )
        radioID   = int( data['id']        ) # radioID, hopefully unique to an IAM (but not necessarily unique)
        ccChannel = int( data['sensor']    ) # channel on this Current Cost
        watts     = int( data['ch1/watts'] )
        
        lock = threading.Lock()
        
        if radioID not in CurrentCost.sensors.keys():
            print("making new Sensor for radio ID {}".format(radioID),file=sys.stderr)
            lock.acquire()
            CurrentCost.sensors[radioID] = Sensor(radioID)
            lock.release()
        
        lock.acquire()
        CurrentCost.sensors[radioID].update(watts, ccChannel, self)
        lock.release()
        
        # Maintain a local dict of sensors connected to this current cost
        self.localSensors[ ccChannel ] = CurrentCost.sensors[ radioID ]
        

    def __str__(self):
        string  = "port      = {}\n".format(self.port)        
        string += "DSB       = {}\n".format(self.dsb)
        string += "Version   = {}\n\n".format(self.ccVersion)    
        string += "                                         |---PERIOD STATS (secs)---|\n"
        string += Sensor.headers
        
        ccChannels = self.localSensors.keys() # keyed by channel number
        ccChannels.sort()
        
        for ccChannel in ccChannels:
            sensor  = self.localSensors[ccChannel]
            string += str(sensor)        
        
        string += "\n\n"
        
        return string


class Manager(object):
    """
    Singleton. Used to manage multiple CurrentCost objects. 
    """
    
    def __init__(self, currentCosts, args):
        self.currentCosts = currentCosts # list of Current Costs
        self.args         = args # command line arguments
        
        
    def run(self):
        # Start each monitor thread
        for currentCost in self.currentCosts:
            currentCost.printXML = self.args.printXML
            currentCost.start()
        
        # Use this main thread of control to continually
        # print out info
        if self.args.noDisplay or self.args.printXML:
            print("Press CTRL+C to stop.\n")
            signal.pause()
        else:
            while abort == False:
                os.system('clear')
                print(str(self))
                print("Press CTRL+C to stop.\n")                
                time.sleep(1)            
        
        self.stop()
        

    def stop(self):
        """Gracefully attempt to bring the system to a halt.
           Specifically we ask every CurrentCost thread to stop by setting 'abort' to True
           and then we wait patiently for every CurrentCost to return from its last
           blocked read."""
        global abort
        abort = True
        
        print("Stopping...")

        # Don't exit the main thread until our worker CurrentCost threads have all quit
        for currentCost in self.currentCosts:
            print("Waiting for monitor {} to stop...".format(currentCost.port))
            currentCost.join()
            
        print("Done.")
    
            
    def __str__(self):
        string = ""             
        for currentCost in self.currentCosts:
            string += str(currentCost)
            
        return string


def checkForDuplicates(lst, label):
    """Check for duplicate entries in a list. If duplicates are found then raise an Exception."""
    duplicates = {}
    for item in lst:
        count = lst.count( item )
        if count > 1 and item not in duplicates.keys():
            duplicates[item] = count
            
    if duplicates: # if duplicates contains any items
        raise Exception("ERROR in radioIDs.dat. Duplicate {} found: {}\n".format(label, duplicates))        


#########################################
#      LOAD CONFIG                      #
#########################################

def loadConfig():
    """Load config data from config files."""
    configTree    = ET.parse("config.xml") # load config from config file
    global directory
    directory     = configTree.findtext("directory") # File to save data to
    serialsETree  = configTree.findall("serialport")

    # Start a CurrentCost for each serial port in config.xml
    currentCosts = []
    for serialPort in serialsETree:
        currentCost = CurrentCost( serialPort.text )
        currentCosts.append( currentCost )
        
    # Loading radioID mappings
    try:
        radioIDfileHandle = open("radioIDs.dat", "r") # if file doesn't exist then skip the rest of this try block
        lines = radioIDfileHandle.readlines()
        radioIDfileHandle.close()

        # Handle mapping from radio IDs to labels and channel numbers
        sensors = {}
    
        # list of radioIDs to check for duplicates
        radioIDs = []
    
        # list of channels to check for duplicates
        channels = []
        
        # mapping from channel number to label (for creating labels.dat)
        channelNumMap = {}

        for line in lines:
            partition = line.partition('#') # ignore comments
            fields = partition[0].strip().split()
            if len(fields) == 3:
                channel, label, radioID = fields
                radioID = int(radioID)
                sensors[ radioID ] = Sensor( radioID, channel, label )
                radioIDs.append( radioID )
                channels.append( channel )
                channelNumMap[ channel ] = label
                        
        checkForDuplicates( radioIDs, 'radioIDs' )
        checkForDuplicates( channels, 'channels' )
        
        # Set static variable in Sensor class
        CurrentCost.sensors = sensors
        
        # write labels.dat file to disk
        labelsDatHandle = open(directory + 'labels.dat', 'w')
        chanKeys = channelNumMap.keys()
        chanKeys.sort()
        for chanKey in chanKeys:
            labelsDatHandle.write('{} {}\n'.format(chanKey, channelNumMap[chanKey]))
        labelsDatHandle.close()

    except IOError, e: # file not found
        print("radioIDs.dat file not found. Ignoring.", str(e), sep="\n", file=sys.stderr)
    except Exception, e: # duplicates found        
        print(str(e))
        raise
            
    return currentCosts


#########################################
#      HANDLE SIGINTs                   #
# So we do the right thing with CTRL+C  #
#########################################

def signalHandler(signal, frame):
    print("\nSIGINT received.")
    global abort
    abort = True


###############################################
#  PROCESS COMMAND LINE ARGUMENTS AND RUN     #
###############################################

if __name__ == "__main__":
    # Process command line args
    parser = argparse.ArgumentParser(description='Log data from multiple Current Cost IAMs.')
    parser.add_argument('--noDisplay', dest='noDisplay', action='store_const',
                        const=True, default=False, help='Do not display info to std out. Useful for use with nohup command.')
    parser.add_argument('--printXML', dest='printXML', action='store_const',
                        const=True, default=False, help='Just dump XML from the monitor(s) to std out. Do not log data.')
    
    args = parser.parse_args()

    # load config
    currentCosts = loadConfig()

    # register SIGINT handler
    print("setting signal handler")
    signal.signal(signal.SIGINT, signalHandler)

    manager = Manager(currentCosts, args)        

    try:
        manager.run()
    except:
        manager.stop()
        raise                
