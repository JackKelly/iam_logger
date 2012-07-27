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
TODO:

* Cope with fact that we can't trust the serial ports; 
  we probably want to use radioIDs... it'd be nice to
  find a way to track individual monitors though.

* Use a new class to store data for each sensor
  (keeping track of average delay between received data etc)

"""

######################################
#     GLOBALS                        #
######################################

exceptionRaised = False
sigIntReceived  = False

class CurrentCost(object):

    def __init__(self, port):
        # open serial port
        try:
            self.port   = port
            print("opening serial port ", port)
            self.serial = serial.Serial(self.port, 57600)
            self.serial.flushInput()
        except OSError, e:
            print("Serial port " + port + " unavailable.  Is another process using it?", file=sys.stderr)
            raise

    def read(self):
        """Read one line from serial port."""

        # For Current Cost XML details, see currentcost.com/cc128/xml.htm
        watts = None

        while watts == None and sigIntReceived == False:
            try:
                line = self.serial.readline()
                tree     = ET.XML( line )
                radioID  = tree.findtext("id")
                sensor   = tree.findtext("sensor")
                watts    = tree.findtext("ch1/watts")
                UNIXtime = int( round( time.time()) )
            except OSError, inst: # catch errors raised by serial.readline
                print("Serial port " + self.port + " unavailable.  Is another process using it?", file=sys.stderr)
                line = None
                raise
            except Exception, inst: # Catch XML errors (occasionally the current cost outputs malformed XML)
                print("XML error: " + str(inst) + "\n", file=sys.stderr)
                line = None

        return sensor, radioID, watts, UNIXtime


class TimeInfo(object):
    """Class for recording simple statistics about time. Useful during IAM testing."""
    def __init__(self, UNIXtime):
        self.lastSeen = UNIXtime
        self.mean = None
        self.max  = None
        self.min  = None
        self.current = None
        self.count = 0

    def newTime(self, UNIXtime):
        self.current = UNIXtime - self.lastSeen
        self.count += 1
        if self.mean == None:
            self.mean = self.current
            self.max  = self.current
            self.min  = self.current
        else:
            self.mean = ((self.mean*(self.count-1))+self.current) / float(self.count)
            if self.current > self.max: self.max = self.current
            if self.current < self.min: self.min = self.current
            
        self.lastSeen = UNIXtime

    def __str__(self):
        if self.mean == None:
            return "     -       -      -      -      {:d}".format(self.count)
        else:
            return '{:>7.2f}{:>7d}{:>7d}{:>7d}{:>7d}'.format(self.mean, self.max, self.min, self.current, self.count)

class IAMTester(threading.Thread):

    def __init__(self, dataStore, port):
        threading.Thread.__init__(self)
        self.dataStore = dataStore
        self.sensors   = self.dataStore.new()
        self.currentCost = CurrentCost( port )


    def _run(self):
        lock = threading.Lock() # I don't think locking is strictly necessary
                                # (because each thread has its own self.sensors object) 
                                # but it's probably better to be safe than sorry, just in case something 
                                # odd happens because dataStore keeps a list of every sensor)
        while sigIntReceived == False:
            sensor, radioID, watts, UNIXtime = self.currentCost.read()
            if sensor not in self.sensors.keys():
                lock.acquire()
                self.sensors[sensor] = {'radioID': [radioID], 'timeInfo': TimeInfo(UNIXtime)}
                lock.release()
            else:
                lock.acquire()
                self.sensors[sensor]['timeInfo'].newTime( UNIXtime )
                lock.release()
                if radioID not in self.sensors[sensor]['radioID']:
                    lock.acquire()
                    self.sensors[sensor]['radioID'].append(radioID)
                    lock.release()
            lock.acquire()
            self.sensors[sensor]['watts']    = watts
            lock.release()

    def run(self):
        try:
            self._run()
        except:
            global exceptionRaised
            exceptionRaised = True
            raise


class DataStore(object):
    def __init__(self):
        self.monitors = [] # Each item in this list represents the data from a single monitor

    def new(self):
        self.monitors.append({})
        return self.monitors[-1]

    def printAndSleep(self):
        self.print()
        time.sleep(1)
    
    def print(self):
        os.system('clear')
        radioIDs = set([])
        monitorNum = 0

        # make a list of radioIDs
        duplicateRadioIDs = set([])
        for monitor in self.monitors:
            for sensorNum, sensorData in monitor.iteritems():
                for radioID in sensorData['radioID']:
                    if radioID in radioIDs:
                        duplicateRadioIDs = set.union( duplicateRadioIDs, [radioID] )
                    else:
                        radioIDs = set.union( radioIDs, [radioID] )

        # print
        print("                          ------PERIOD STATS (secs)----")
        print("SENSOR WATTS  RADIOID     MEAN     MAX    MIN   CURRENT COUNT")
        for monitor in self.monitors:
            print('\nmonitorNum = ', monitorNum)
            monitorNum += 1
            keys = monitor.keys()
            keys.sort()
            for key in keys:
                sensor = monitor[key]
                msg = '{:>3}{:>9d}{:>11}'.format(key, int(sensor['watts']), sensor['radioID'])
                msg += str(sensor['timeInfo'])
                for radioID in monitor[key]['radioID']:
                    if radioID in duplicateRadioIDs:
                        # this radioID has been seen more than once so colour it red
                        msg = '\033[91m' + msg + '\033[0m'
                        break
                print(msg)

        print("\nPress CTRL+C to stop.\n")


#######################################
# TEST IAMs                           #
#######################################

def testIAMs( config, threads ):
    # initialise serial port
    dataStore = DataStore()

    # launch threads to monitor each serial port
    for monitor in config['monitors']:
        thread = IAMTester(dataStore, monitor['port'] )
        thread.start()
        threads.append( thread )

    # continually print to screen until either an exception is raised
    # in a worker thread or SIGINT is received
    global sigIntReceived
    global exceptionRaised
    while exceptionRaised == False and sigIntReceived == False:
        try:
            dataStore.printAndSleep()
        except Exception, e:
            print("ERROR: ", e, file=sys.stderr)
            exceptionRaised = True
            break

    sigIntReceived = True

    print("Shutting down...")

    return threads


#########################################
#      LOAD CONFIG                      #
#########################################

def loadConfig():
    configTree     = ET.parse("config.xml") # load config from config file
    filename       = configTree.findtext("filename") # File to save data to
    monitorsETree  = configTree.findall("monitor")

    monitors = []

    for monitor in monitorsETree:
        monitorID = monitor.attrib['id']
        monitorSerial = monitor.findtext("serialport")
        monitorMapping = monitor.findtext("mapping")

        mapping = monitorMapping.split("\n")
        mappingMatrix = []
        for line in mapping:
            strippedLine = line.strip()
            if strippedLine != '':
                mappingMatrix.append( [i.strip() for i in strippedLine.split(',')] )

        monitors.append({'id': monitorID, 'port': monitorSerial, 'mapping': mappingMatrix})

    return {'filename': filename, 'monitors': monitors}


#########################################
#      HANDLE SIGINTs                   #
# So we do the right thing with CTRL+C  #
#########################################

def signalHandler(signal, frame):
    print("SIGINT received.")
    global sigIntReceived
    sigIntReceived = True


########################################
#  PROCESS COMMAND LINE ARGUMENTS      #
########################################

if __name__ == "__main__":
    # Process command line args
    parser = argparse.ArgumentParser(description='Log data from multiple Current Cost IAMs.')
    parser.add_argument('--testIAMs', dest='testIAMs', action='store_const', 
                        const=True, default=False, help='Mode to help diagnose problems with IAMs')
    args = parser.parse_args()

    # load config
    config = loadConfig()

    # register SIGINT handler
    print("setting signal handler")
    signal.signal(signal.SIGINT, signalHandler)

    # list for storing all our threads
    threads = []

    # run relevant function
    if args.testIAMs:
        threads = testIAMs( config, threads )

    # Don't exit the main thread until our worker threads have all quit
    for thread in threads:
        thread.join()

    print("Done.\n")
