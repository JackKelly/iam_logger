#! /usr/bin/python
from __future__ import print_function
from slicedpie import meterSignal
from matplotlib import pyplot as plt
import pandas

filename = '/home/jack/workingcopies/domesticPowerData/BellendenRd/version2/channel_99.dat'
signal = meterSignal.read_csv(filename, separator=' ', colnames=['watts', 'port', 'cc_channel'])
signal2 = pandas.DataFrame(signal)

merged = pandas.ordered_merge(signal.watts, signal2.watts)

print(signal)

fig = plt.figure()
ax  = fig.add_subplot(1,1,1)
meterSignal.plot_signal(merged, ax)
fig.autofmt_xdate()
plt.show()
