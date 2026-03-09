#Various FRB related functions, written by AstroFlash members over the years
# incl Kenzie, Mark, Dante, David, Jeff

import numpy as np
import pandas as pd
from scipy.signal import decimate, fftconvolve


def normalise(ds, t_cent, t_sig):
    """
    Calibrate the dynamic spectrum for the bandpass
    Per frequency channel it subtracts the off burst mean and divides by the off burst std
    """
    ds=ds.copy()
    ds_off = np.concatenate((ds[:,0:int(t_cent-3*t_sig)],ds[:,int(t_cent+3*t_sig):]),axis=1)
    for chan in range(ds_off.shape[0]):
        ds[chan,:] = ds[chan,:] - np.mean(ds_off[chan,:])
        ds_off[chan,:] = ds_off[chan,:] - np.mean(ds_off[chan,:])
        if np.std(ds_off[chan,:])!=0:
            ds[chan,:] = ds[chan,:] /  np.std(ds_off[chan,:])
        else:
            ds[chan,:] = 0
    return ds

def normalize(ds,op):
    ds=ds.copy().astype(float)
    op=op.copy().astype(float)
    nds=np.zeros_like(ds)

    for chan in range(ds.shape[0]):
        channel=ds[chan,:]
        offburstchannel=op[chan,:]

        chan_mean=np.median(offburstchannel)
        chan_std=np.std(offburstchannel)
        if chan_std != 0:
            newchannel=(channel-chan_mean)/chan_std

        nds[chan,:]=newchannel

    return nds

def delta(f1, f2, DM):
    """
    Calculate the dispersive time delay in milliseconds.
    Arguments:
        f1 -- frequency of channel 1 in megahertz
        f2 -- frequency of channel 2 in megahertz. Should be greater than f1.
        DM -- dispersion measure in pc/cm^3
    Returns:
        Dispersive time delay in milliseconds between f1 and f2.
    Exceptions:
        ValueError -- if f1 > f2
    """
    if f1 > f2:
        raise ValueError('f1 must not be greater than f2')

    return (4.148808e6*(f1**(-2) - f2**(-2))*DM)

def dedisperse(data, dm, freqs, tsamp):
    """ data is a 2d numpy array
    dm is the dispersion measure in units of pc/cc
    freqs is a 1d numpy array containing the frequency per channel in units of MHz
    tsamp is the sampling time in units of seconds """
    freqs = freqs.astype(np.float64)
    reffreq = np.max(freqs)
    ndata = np.zeros_like(data)
    dmconst = 1 / (2.41 * 10**-4) # MHz^2 pc^−1 cm^3 s
    time_shift = dmconst * dm * (reffreq**-2.0 - freqs**-2.0)
    bin_shift = np.round(time_shift / tsamp).astype(np.int32)
    assert len(bin_shift) == data.shape[0]
    assert ndata.shape == data.shape
    for i, bs in enumerate(bin_shift):
        ndata[i, :] = np.roll(data[i, :], bs)
    return ndata


def downsample(ds,tdown=1,fdown=1):
    if fdown!=1:
        ds=ds.reshape(ds.shape[0]//fdown, fdown,ds.shape[-1]).sum(axis=1)
    if tdown!=1:
        ds=ds.reshape(ds.shape[0], int(ds.shape[-1]/tdown), tdown).sum(axis=2)
    return ds

def decimate_2d(arr,tfac=1,ffac=1):

    print('Assuming {} channels and {} time bins...'.format(arr.shape[0],arr.shape[1]))
    print('Output array has {} channels and {} time bins...'.format(arr.shape[0]//ffac,arr.shape[1]//tfac))


    downsamp_arr_t = np.zeros((arr.shape[0],arr.shape[1]//tfac))

    for i in range(arr.shape[0]):
        downsamp_arr_t[i,:]=decimate(arr[i,:],tfac)


    downsamp_arr_tf = np.zeros((arr.shape[0]//ffac,arr.shape[1]//tfac))

    for i in range(downsamp_arr_t.shape[1]):
            downsamp_arr_tf[:,i]=decimate(downsamp_arr_t[:,i],ffac)

    return downsamp_arr_tf

def radiometer(tsamp, bw, npol, SEFD):
    """
    radiometer(tsamp, bw, npol, Tsys, G):
    tsamp is the time resolution in milliseconds
    bw is the bandwidth in MHz
    npol is the number of polarizations
    Tsys is the system temperature in K (typical value for Effelsberg = 20K)
    G is the telescope gain in K/Jy (typical value for Effelsberg = 1.54K/Jy)
    """

    return (SEFD)*(1/np.sqrt((bw*1.e6)*npol*tsamp*1e-3))

def acf_2d(array):
    return fftconvolve(array, array[::-1, ::-1], mode='same')

def acf_1d(array):
    return fftconvolve(array, array[::-1], mode='same')

def gaussian_2d(xy, A, x0, y0, sigma_x, sigma_y, offset):
    x, y = xy
    func = A * np.exp(-(((x - x0) ** 2) / (2 * sigma_x ** 2) + ((y - y0) ** 2) / (2 * sigma_y ** 2))) + offset
    return func.ravel()

def gaussian_1d(x, A, mu, sigma, offset):
    return A * np.exp( -((x - mu) ** 2) / (2 * sigma ** 2) ) + offset