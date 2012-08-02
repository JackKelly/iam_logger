#! /usr/bin/python
from __future__ import print_function
from slicedpie import meterSignal
from matplotlib import pyplot as plt
import pandas

"""
TODO: Take a look at TimeSeries.combine using a function something like this:

rng = pandas.date_range('1/1/2011', periods=10, freq='H')
rng2 = pandas.date_range('1/1/2011 05:00:00', periods=10, freq='H')
ts1 = pandas.Series(2*(len(rng)), index=rng)
ts2 = pandas.Series(2*(len(rng)), index=rng2)

def comparison(a,b):
    if numpy.isnan(a):
        return b
    elif numpy.isnan(b):
        return a
    elif a==b:
        return a
    else:
        raise Exception("oops. a={}, b={}".format(a,b))

ts2.combine(ts1, f)
"""

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
