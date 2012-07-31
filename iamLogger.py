#! /usr/bin/python
from __future__ import print_function, division
import serial # for pulling data from Current Cost
import xml.etree.ElementTree as ET # for XML parsing
import time # for printing UNIX timecode
import sys # for printing errors to stderr
import os # to find names of existing files
import argparse
import threading
import signal

"""
TODO: Cope with fact that we can't trust the serial ports; 
  we probably want to use radioIDs... it'd be nice to
  find a way to track individual currentCosts though.
  We should also take advantage of the fact that all 
  currentCosts record aggregate data. We could merge these together.
"""

"""
TODO: handle mappings
"""

######################################
#     GLOBALS                        #
######################################

abort = False # Make this True to halt all threads
directory = None # The directory to write data to

class TimeInfo(object):
    """Class for recording simple statistics about time. Useful during IAM testing."""
    
    strFormat = '{:>7.2f}{:>7.1f}{:>7.1f}{:>7.1f}{:>7d}'
    headers   = '{:>7}{:>7}{:>7}{:>7}{:>7}'.format('MEAN', 'MAX', 'MIN', 'LAST', 'COUNT')
    
    def __init__(self):
        self.count    = -1
        self.lastSeen = 0

    def update(self):
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
            return "     -       -      -      -      {:d}".format(self.count)
        else:
            return TimeInfo.strFormat.format(self.mean, self.max, self.min, self.current, self.count)


class Location(object):
    def __init__(self, ccChannel, currentCost):
        self.ccChannel = ccChannel
        self.currentCost = currentCost
        
    def __str__(self):
        return '{} {}'.format(self.currentCost.port, self.ccChannel)
    
    def __repr__(self):
        return 'Location({})'.format(str(self))

    
#####################################
#     SENSOR CLASS                  #
#####################################
class Sensor(object):
    
    strFormat = '{:>20.20} {:>4} {:>6} {:>5} {} {:>7} {}\n'
    headers   = strFormat.format('LABEL', 'CHAN', 'CCchan', 'WATTS', TimeInfo.headers, 'RADIOID', 'LOCATIONS')
    
    def __init__(self, radioID, channel='-', label='-'):
        self.timeInfo  = TimeInfo()
        self.location  = '-'      
        self.locations = {} # list of all (channel, CurrentCost) this sensor has been seen on
        self.radioID   = radioID # the radioID (unique to this Sensor)
        self.channel   = channel # the channel number (also unique to this Sensor) taken from config file
        self.label     = label # human-readable label for this Sensor
        self.watts     = '-'
        self.lastTimecodeWrittenToDisk = None
        
    def update(self, watts, ccChannel, currentCost):
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
        lastSeen = int(round(self.timeInfo.lastSeen))
        
        # First check to see if we've already written this to disk (possibly because multiple current cost monitors hear this sensor)
        if lastSeen == self.lastTimecodeWrittenToDisk:
            print("Timecode {} already written to disk.".format(lastSeen), file = sys.stderr)
            return
        
        self.lastTimecodeWrittenToDisk = lastSeen
        
        if self.channel == '-':
            chan = self.radioID
        else:
            chan = self.channel
        
        filename =  directory + "channel_" + str( chan ) + ".dat"
        filehandle = open(filename, 'a+')
        data = '{:d} {} {}\n'.format(lastSeen, self.watts, self.location)
        filehandle.write( data )
        filehandle.close()



#####################################
#     CURRENT COST CLASS            #
#####################################
class CurrentCost(threading.Thread):
    
    sensors = None # Static variable.  A dict of all sensors; keyed by radioID.

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
        """Simply read a line from the serial port. Blocking.  On error, print useful message and raise the error."""
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
        """ Reset the serial port"""            
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
        """Reads a line from the serial port and returns an ElementTree"""
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
        """Get DSB and version number from Current Cost monitor"""
        data = {'dsb': None, 'src': None}
        data           = self.readXML( data )
        self.dsb       = data['dsb'] 
        self.CCversion = data['src']
        

    def update(self):
        """Read ccChannel data from serial port."""

        # For Current Cost XML details, see currentcost.com/cc128/xml.htm
        data = {'id': None, 'sensor': None, 'ch1/watts': None}
        
        data      = self.readXML( data )
        radioID   = int( data['id']        ) # radioID
        ccChannel = int( data['sensor']    ) # channel on this Current Cost
        watts     = int( data['ch1/watts'] )
        
        if radioID not in CurrentCost.sensors.keys():
            print("making new sensors for {}\n".format(radioID),file=sys.stderr)
            print(CurrentCost.sensors.keys(), file=sys.stderr)
            CurrentCost.sensors[radioID] = Sensor(radioID) 
        
        CurrentCost.sensors[radioID].update(watts, ccChannel, self)
        
        # Maintain a local dict of sensors connected to this current cost
        self.localSensors[ ccChannel ] = CurrentCost.sensors[ radioID ]
        

    def __str__(self):
        string  = "port      = {}\n".format(self.port)        
        string += "DSB       = {}\n".format(self.dsb)
        string += "Version   = {}\n\n".format(self.CCversion)    
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
        self.monitors = currentCosts
        self.args     = args
        
        
    def run(self):
        # Start each monitor thread
        for monitor in self.monitors:
            monitor.printXML = self.args.printXML
            monitor.start()
        
        # Use this main thread of control to continually
        # print out info
        if self.args.noDisplay or self.args.printXML:
            print("Press CTRL+C to stop.\n")
            signal.pause()
        else:
            while abort == False:
                os.system('clear')
                print(str(self))
                print("\nPress CTRL+C to stop.\n")                
                time.sleep(1)            
        
        self.stop()
        

    def stop(self):
        global abort
        abort = True
        
        print("Stopping...")

        # Don't exit the main thread until our worker threads have all quit
        for monitor in self.monitors:
            print("Waiting for monitor {} to stop...\n".format(monitor.port))
            monitor.join()
            
        print("Done.\n")


#    def checkDuplicateRadioIDs(self):
#        """
#        Look across all currentCosts to check for duplicated radioIDs
#        """
#        
#        # make a list of radioIDs
#        radioIDs = set([])
#        duplicateRadioIDs = set([])
#        for monitor in self.monitors:
#            for sensorNum, sensorData in monitor.sensors.iteritems():
#                for radioID in sensorData.radioIDs.keys():
#                    if radioID in radioIDs:
#                        duplicateRadioIDs = set.union(duplicateRadioIDs, [radioID])
#                    else:
#                        radioIDs = set.union(radioIDs, [radioID])
#                        
#        if len(duplicateRadioIDs) > 0:
#            print("\nDuplicate radio IDs: {}\n".format(duplicateRadioIDs))
    
            
    def __str__(self):
        string = ""             
        for monitor in self.monitors:
            string += str(monitor)
            
        return string


#########################################
#      LOAD CONFIG                      #
#########################################

class SensorLabel(object):
    def __init__(self, channel, label):
        self.channel = channel
        self.label   = label
        

def loadConfig():
    configTree    = ET.parse("config.xml") # load config from config file
    global directory
    directory     = configTree.findtext("directory") # File to save data to
    serialsETree  = configTree.findall("serialport")

    # Start a CurrentCost for each serial port in config.xml
    currentCosts = []
    for serialPort in serialsETree:
        currentCost    = CurrentCost( serialPort.text )
        currentCosts.append( currentCost )
    
    # Handle mapping from radio IDs to labels and channel numbers
    sensors = {}
    
    # Loading radioID mappings
    radioIDfileHandle = open("radioIDs.dat", "r")
    lines = radioIDfileHandle.readlines()
    for line in lines:
        fields = line.strip().split()
        if len(fields) == 3:
            channel, label, radioID = fields
            radioID = int(radioID)
            sensors[ radioID ] = Sensor( radioID, channel, label )
    
    # Set static variable in Sensor class
    CurrentCost.sensors = sensors

    return currentCosts


#########################################
#      HANDLE SIGINTs                   #
# So we do the right thing with CTRL+C  #
#########################################

def signalHandler(signal, frame):
    print("\nSIGINT received.")
    global abort
    abort = True


########################################
#  PROCESS COMMAND LINE ARGUMENTS      #
########################################

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
