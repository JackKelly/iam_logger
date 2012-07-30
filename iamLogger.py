#! /usr/bin/python
from __future__ import print_function, division
import serial # for pulling data from Current Cost
import xml.etree.ElementTree as ET # for XML parsing
import time # for printing UNIX timecode
import urllib2 # for sending data to Pachube
import json # for assembling JSON data for Pachube
import sys # for printing errors to stderr
import os # to find names of existing files
import argparse
import threading
import signal

"""
TODO: Cope with fact that we can't trust the serial ports; 
  we probably want to use radioIDs... it'd be nice to
  find a way to track individual monitors though.
  We should also take advantage of the fact that all 
  monitors record aggregate data. We could merge these together.
"""

"""
TODO: handle mappings
"""

######################################
#     GLOBALS                        #
######################################

abort = False # Make this True to halt all threads
directory = None # The directory to write data to

#####################################
#     SENSOR CLASS                  #
#####################################
class Sensor(object):
    
    def __init__(self):
        self.timeInfo = TimeInfo()        
        self.radioIDs = {} # list of all radioIDs seen by this sensor
        self.radioID  = None # current radioID
        
        
    def update(self, watts, radioID):        
        self.timeInfo.update()
        self.watts = watts
        
        self.radioID = radioID
        if radioID in self.radioIDs.keys():
            self.radioIDs[radioID] += 1
        else:
            self.radioIDs[radioID] = 0
        
    def __str__(self):
        return '{:>7d}{}{:>9} {}'.format(self.watts, self.timeInfo, self.radioID, self.radioIDs)
    
    def writeData(self):
        return '{:d}\t{}\t{}\n'.format(int(round(self.timeInfo.lastSeen)), self.watts, self.radioID)


#####################################
#     CURRENT COST CLASS            #
#####################################
class CurrentCost(threading.Thread):
    
    id = None # The ID for this Current Cost.  e.g. "A" or "B" or "C" etc.

    def __init__(self, port ):
        threading.Thread.__init__(self)
        global abort        
        try:
            self.port = port
            self.serial = None
            self._openPort()
            self._getInfo()
            self.sensors   = {}
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
            while abort == False:
                self.update()
        except:
            abort = True
            raise
            

    def readXML(self, data):
        """Reads a line from the serial port and returns an ElementTree"""
        RETRIES = 10
        for i in range(RETRIES):
            try:
                line = self.serial.readline()
                tree = ET.XML(line)
                
                # Check if this is histogram data from the current cost (which we're not interested in)
                if tree.findtext('hist') != None:
                    print("Skipping histogram data from Current Cost \'{}\'.\n".format(self.id), file=sys.stderr)
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
                
            except OSError, e: # catch errors raised by serial.readline
                print("Serial port " + self.port + " unavailable.  Is another process using it?", str(e), sep="\n", file=sys.stderr)
            except serial.SerialException, e:
                print("SerialException on port {}:".format(self.port), str(e), "Has the device been unplugged?\n", sep="\n", file=sys.stderr)
            except ET.ParseError, e: # Catch XML errors (occasionally the current cost outputs malformed XML)
                print("XML error: ", str(e), line, sep="\n", file=sys.stderr)
            except ValueError, e: # Attempting to use a port that is not open
                print("ValueError: ", str(e), sep="\n", file=sys.stderr)
            
            # We should only execute the following after an exception
            # Retry...
            time.sleep(1) 
            print("retrying... retry number {} of {}\n".format(i, RETRIES), file=sys.stderr)
            
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
        

    def writeToDisk(self, sensorNum):
        filename =  directory + "channel_" + self.id + str(sensorNum) + ".dat"
        try:
            filehandle = open(filename, 'a+')
        except Exception, e:
            print("Failed to open file {}.\n".format(filename), str(e), sep="\n", file=sys.stderr)
            raise

        filehandle.write( self.sensors[sensorNum].writeData() )
        filehandle.close()
        

    def update(self):
        """Read sensor data from serial port."""

        # For Current Cost XML details, see currentcost.com/cc128/xml.htm
        data = {'id': None, 'sensor': None, 'ch1/watts': None}
        
        data     = self.readXML( data )
        radioID  = int( data['id']        )
        sensor   = int( data['sensor']    )
        watts    = int( data['ch1/watts'] )

        if sensor not in self.sensors.keys():
            self.sensors[sensor] = Sensor()
        
        self.sensors[sensor].update(watts, radioID)
        
        self.writeToDisk( sensor )

    def __str__(self):
        string  = "----------------\n"
        string += "monitorID = {}\n".format(self.id)
        string += "port      = {}\n".format(self.port)        
        string += "DSB       = {}\n".format(self.dsb)
        string += "Version   = {}\n\n".format(self.CCversion)
        string += "               -----PERIOD STATS (secs)--\n"
        string += "SENSOR  WATTS  MEAN    MAX    MIN CURRENT COUNT  RADIOID RADIOIDs\n\n"
        keys = self.sensors.keys()
        keys.sort()
        for key in keys:
            sensor  = self.sensors[key]
            string += '{:>5}{}\n'.format(key, str(sensor))        
        
        string += "\n"
        return string


class TimeInfo(object):
    """Class for recording simple statistics about time. Useful during IAM testing."""
    
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
            return '{:>7.2f}{:>7.1f}{:>7.1f}{:>7.1f}{:>7d}'.format(self.mean, self.max, self.min, self.current, self.count)


class Manager(object):
    """
    Singleton. Used to manage multiple CurrentCost objects. 
    """
    
    def __init__(self, monitors):
        self.monitors = monitors
        
        
    def run(self, noDisplay=True):
        # Start each monitor thread
        for monitor in self.monitors:
            monitor.start()
        
        # Use this main thread of control to continually
        # print out info
        if noDisplay:
            print("Press CTRL+C to stop.\n")
            signal.pause()
        else:
            while abort == False:
                os.system('clear')
                print(str(self))
                self.checkDuplicateRadioIDs()
                print("\nPress CTRL+C to stop.\n")                
                time.sleep(1)            
        
        self.stop()
        

    def stop(self):
        global abort
        abort = True
        
        print("Stopping...")

        # Don't exit the main thread until our worker threads have all quit
        for monitor in self.monitors:
            print("Waiting for monitor {} to stop...\n".format(monitor.id))
            monitor.join()
            
        print("Done.\n")


    def checkDuplicateRadioIDs(self):
        """
        Look across all monitors to check for duplicated radioIDs
        """
        
        # make a list of radioIDs
        radioIDs = set([])
        duplicateRadioIDs = set([])
        for monitor in self.monitors:
            for sensorNum, sensorData in monitor.sensors.iteritems():
                for radioID in sensorData.radioIDs.keys():
                    if radioID in radioIDs:
                        duplicateRadioIDs = set.union(duplicateRadioIDs, [radioID])
                    else:
                        radioIDs = set.union(radioIDs, [radioID])
                        
        if len(duplicateRadioIDs) > 0:
            print("\nDuplicate radio IDs: {}\n".format(duplicateRadioIDs))
    
            
    def __str__(self):
        string = ""
        for monitor in self.monitors:
            string += str(monitor)
            
        return string


#########################################
#      LOAD CONFIG                      #
#########################################

def loadConfig():
    configTree    = ET.parse("config.xml") # load config from config file
    global directory
    directory     = configTree.findtext("directory") # File to save data to
    monitorsETree = configTree.findall("monitor")

    monitors = []

    for monitor in monitorsETree:
        currentCost    = CurrentCost( monitor.findtext("serialport") )
        currentCost.id = monitor.attrib['id']
        
        monitorMapping = monitor.findtext("mapping")
        mapping = monitorMapping.split("\n")
        mappingMatrix = []
        for line in mapping:
            strippedLine = line.strip()
            if strippedLine != '':
                mappingMatrix.append([i.strip() for i in strippedLine.split(',')])

        currentCost.mapping = mappingMatrix

        monitors.append( currentCost )

    return monitors


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
    args = parser.parse_args()

    # load config
    monitors = loadConfig()

    # register SIGINT handler
    print("setting signal handler")
    signal.signal(signal.SIGINT, signalHandler)

    manager = Manager(monitors)        

    print(args.noDisplay)

    try:
        manager.run(args.noDisplay)
    except:
        manager.stop()
        raise                
