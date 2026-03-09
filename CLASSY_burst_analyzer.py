"""
This is an interactive burst analyzer that loads in either a single filterbank, or a list containing a filterbank on each line.

There are three stages:
    1. preview burst - adjust the crop window, on-burst region, and select different (sub)burst peaks
    2. RFI flagging - mask the channels contaminated with RFI, set the spectral extent of the burst
    3. compute burst properties - take a look at the burst properties that have been calculated, optionally save these properties to a dictionary and move on to the next burst
Once all bursts have been analyzed a .npz is creating containing a dictionary with all burst properties.

"""

#standard packages
import argparse
import sys
from astropy import units as u
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, TextBox
import pandas as pd
#fancier packages/custom scripts
import basic_funcs
import your
import jess.channel_masks
from pathlib import Path
from scipy.optimize import curve_fit
import glob

import matplotlib
matplotlib.use('tkagg')
############################################################################

# Helper function to load in a burst from a filterbank file. Likely needs adjusting for different telescopes/receivers.
def load_burst(bfile,dm,telescope):
    """
    Input:
        - bfile, filterbank file contaiing a burst, assumes NRT data for now
        - dm, dispersion measure of the burst in pc/cc
        - telescope, for SEFD, only supports NRT for now
        
    Output:
        - stokes_I, 200ms of normalised, dedispersed, stokes I data containing the burst
        - metadata, a dictionary containing useful header information from the filterbank file
    """
    if args.telescope == "NRT": #only NRT supported so far
        Tsys = 35  # K
        G = 1.4  # K/Jy
        SEFD = Tsys / G # Jy
        npol = 2

    # Get important header info
    y = your.Your(bfile)
    tsamp = y.your_header.tsamp #seconds
    freqres = np.abs(y.your_header.foff) #MHz
    start_mjd = y.your_header.tstart
    bw = y.your_header.bw #MHz

    # Determine frequency channels
    freq_lo = y.your_header.fch1
    freq_hi = y.your_header.fch1 + (y.your_header.foff * (y.your_header.nchans - 1) )
    freq_lo, freq_hi = min(freq_lo,freq_hi), max(freq_lo,freq_hi)
    freqs = np.linspace(freq_lo,freq_hi,y.your_header.nchans)

    # NRT specific, burst are extracted to either be at ~100/500 ms into the file
    if "500ms" in bfile:
        nstart = int(450e-3/tsamp)
        plus_mjd_sec = 450e-3 #keep track of how far into the file we're reading
    else:
        nstart = int(50e-3/tsamp)
        plus_mjd_sec = 50e-3
    nread = y.your_header.nspectra # read in all the data incase the dispersive sweep is massive  
    # Get the burst_data, dedisperse
    d = y.get_data(nstart,nread,npoln=4) #read in aa,bb,cr,ci
    aa, bb = d[:,0,:].T, d[:,1,:].T
    stokes_i = aa + bb #NRT has a linear basis
    stokes_i = basic_funcs.dedisperse(stokes_i,dm,freqs,tsamp)
    # Initially crop 200ms, and correct the bandpass using the last 25% of data
    prelim_crop_start = int(0e-3 / tsamp)
    prelim_crop_end = int(151e-3 / tsamp)
    stokes_i = stokes_i[:,prelim_crop_start:prelim_crop_end]
    n1 = int(3/4 * prelim_crop_end)
    n2 = prelim_crop_end
    stokes_i = basic_funcs.normalize(stokes_i,stokes_i[:,n1:n2])
    
    # Create a little dictionary with all these values
    metadata = {
        'tsamp' : tsamp, # seconds
        'freqres' : freqres, # MHz
        'start_mjd' : start_mjd, # topocentric @ NRT
        'plus_mjd_sec' : plus_mjd_sec,
        'SEFD' : SEFD, # Jy
        'bw' : bw, # MHz
        'npol' : npol,
        'freqs' : freqs # MHz
        }
    return stokes_i, metadata



class BurstAnalyzer:
    # STAGE 0 INITIALIZE
    def __init__(self, data, metadata, burst_file, args):
        # Burst data and header metadata
        self.data = data
        self.tsamp = metadata['tsamp']
        self.freqres = metadata['freqres']
        self.start_mjd = metadata['start_mjd']
        self.plus_mjd_sec = metadata['plus_mjd_sec']
        self.SEFD = metadata['SEFD']
        self.bw = metadata['bw']
        self.npol = metadata['npol']
        self.freqs = metadata['freqs']
        self.burst_file = burst_file
        self.args = args
        self.vmin = np.quantile(data, 0.2)
        self.vmax = np.quantile(data, 0.8)
        
        # "Stage 1: preview burst" defaults and variables
        self.time_factor = 1
        self.freq_factor = 1
        self.crop_start = 0
        self.crop_end = self.data.shape[1] # number of time bins
        self.burst_regions = [] #list to save multiple burst regions as (start,end) tuples
        self.event_start = 2 * (self.data.shape[1] // 10) # assume the burst is between 20 and 30% of the total file length
        self.event_end = 3 * (self.data.shape[1] // 10)
        
        self.peak_positions = [] # to save all burst peaks
        self.manual_peaks = False # No manual peaks have been selected
        self.crop_clicks = [] # to save the clicks to crop
        self.event_clicks = [] # to save the clicks to select the event region in the time profile
        self.onburst_clicks = [] # to save the clicks to select the bursts in the time profile


        # "Stage 2: flag RFI" defaults and variables
        self.masked_ds = None # start with no masking
        self.masked_channels = [] # to keep track of masked channels
        self.spec_ex_lo = 0 # default specex is total bw
        self.spec_ex_hi = len(self.freqs) - 1
        self.spec_ex_clicks = [] # to save the clicks to select the spectral extent in the frequency spectrum
        self.zap_start = None # to zap a range     

        # "Stage 3: compute" defaults
        self.plus_mjd_sec_updated = None # to recalulculate the TOA at the selected peaks, move to stage 1?
        self.t_peak_positions = []
        self.integrated_sn = None
        self.badfit = False
        self.is_special = False
        self.onedgauss_fits = []
        self.acf_fits = None

        # The current stage: "preview", "flag", or "compute".
        self.stage = "preview"
        
        # Set up figure
        self.fig = None
        self.ax_space = None # space on top of the plot
        self.ax_top = None # top panel for freq-avg'd time profile
        self.ax_main = None # main panel for dynamic spectrum
        self.ax_right = None # right panel for time-avg'd freq spectrum
        self.button_axes = [] 
        self.button_cids = []
        self.cmap = 'viridis'
        self.setup_figure()        

    def setup_figure(self):
        # Set up this monstrosity of a figure
        self.figure = plt.figure(figsize=(8,8))
        gs = self.figure.add_gridspec(3, 2, width_ratios=(4, 1), height_ratios=(1, 1, 3), wspace=0, hspace=0)
        self.ax_space = self.figure.add_subplot(gs[0, 0])
        self.ax_top = self.figure.add_subplot(gs[1, 0])
        self.ax_main = self.figure.add_subplot(gs[2, 0], sharex=self.ax_top)
        self.ax_right = self.figure.add_subplot(gs[2, 1], sharey=self.ax_main)
        
        for spine in self.ax_space.spines.values():
            spine.set_color('white')
            
        # Connect a single mouse-click event handler
        self.figure.canvas.mpl_connect('button_press_event', self.on_click)
        self.update_plot()  

    def clear_buttons(self):
        # to clear buttons when moving to the next stage
        for cid in self.button_cids:
            cid.disconnect_events()
        for ax in self.button_axes:
            ax.remove()
        self.button_axes = []
        self.button_cids = []
        
    def update_plot(self):
        # clear axes
        self.ax_space.clear()
        self.ax_main.clear()
        self.ax_top.clear()
        self.ax_right.clear()
        # redraw
        if self.stage == "preview": 
            self.draw_preview()
        elif self.stage == "flag":
            self.draw_flag()
        elif self.stage == "compute":
            self.draw_compute()
        self.figure.canvas.draw_idle()


    # STAGE 1 PREVIEW
    def draw_preview(self):
        # downsample and crop data
        if self.masked_ds is None:
            self.masked_ds = self.data.copy()
            
        if args.jess:
            self.masked_ds = np.ma.masked_array(self.masked_ds, mask=np.zeros(self.masked_ds.shape, dtype=bool), fill_value=np.nan)
            temp_mask = jess.channel_masks.channel_masker(dynamic_spectra=self.masked_ds.data.T, test='skew', sigma=3, show_plots=False) 
            for idx, booli in enumerate(temp_mask):
                self.masked_ds.mask[idx, :] = booli
            for idx in [0,1,126,127]:
                self.masked_ds.mask[idx, :] = True
            
           
        # plot main panel
        self.ax_main.imshow(self.masked_ds, aspect='auto', origin='lower', extent=[self.crop_start, self.crop_end, 0, self.masked_ds.shape[0]],
                            cmap='viridis', interpolation='hanning', vmin=self.vmin, vmax=self.vmax)
        self.ax_main.set_xlim([self.crop_start, self.crop_end])
        self.ax_main.set_xlabel('Time bins')
        self.ax_main.set_ylabel('Frequency channels') 
        self.ax_main.axhline(self.spec_ex_lo, color='darkviolet', ls='--')
        self.ax_main.axhline(self.spec_ex_hi, color='darkviolet', ls='--')
        self.ax_main.axvline(self.event_start, color='darkviolet', lw=1)
        self.ax_main.axvline(self.event_end, color='darkviolet', lw=1)
        # plot top panel
        self.ax_top.plot(np.linspace(self.crop_start, self.crop_end, self.masked_ds.shape[1]), self.masked_ds.sum(axis=0), drawstyle='steps-mid', color='k')
        self.ax_top.axvline(self.event_start, color='darkviolet', lw=1)
        self.ax_top.axvline(self.event_end, color='darkviolet', lw=1)
        for region in self.burst_regions:
            start, end = region
            self.ax_top.fill_between([start, end], 0, 1, color='orange', alpha = 0.3, transform=self.ax_top.get_xaxis_transform())
        # plot side panel
        self.ax_right.plot(self.masked_ds.sum(axis=1), np.arange(self.masked_ds.shape[0]), drawstyle='steps-mid', color='k')
        self.ax_right.axhline(self.spec_ex_lo, color='darkviolet', ls='--')
        self.ax_right.axhline(self.spec_ex_hi, color='darkviolet', ls='--')
        
        
        # auto-detect a peak
        if not self.manual_peaks:
            peak_disp = np.argmax(np.nansum(self.masked_ds, axis=0))
            peak_og = np.linspace(self.crop_start, self.crop_end, self.masked_ds.shape[1])[peak_disp]
            self.peak_positions = [peak_og]
        # plot red lines at the peaks
        for p in self.peak_positions:
            p_disp = p 
            self.ax_top.axvline(p_disp, ls='--', color='red', lw=1)
        # update the TOA with the first peak position
        if self.peak_positions:
            self.plus_mjd_sec_updated = self.plus_mjd_sec + self.peak_positions[0] * self.tsamp 
        
        
        # some instruction annotations
        self.ax_space.text(0., 0.98, "Crop: hold 'c', click twice in top panel.",
                          transform=self.ax_space.transAxes, fontsize=10, color='white',
                          verticalalignment='top', bbox=dict(facecolor='red', alpha=1))
        self.ax_space.text(0., 0.78, "Event start/end: hold 'e', click twice in top panel.",
                          transform=self.ax_space.transAxes, fontsize=10, color='white',
                          verticalalignment='top', bbox=dict(facecolor='orange', alpha=1))
        self.ax_space.text(0., 0.58, "Burst regions: hold 'b', click twice per burst (Gaussian fit area) in top panel.",
                          transform=self.ax_space.transAxes, fontsize=10, color='white',
                          verticalalignment='top', bbox=dict(facecolor='lime', alpha=1))
        self.ax_space.text(0., 0.38, "Peaks: hold 'm'/'d' and click to add/delete a peak position.",
                          transform=self.ax_space.transAxes, fontsize=10, color='white',
                          verticalalignment='top', bbox=dict(facecolor='cyan', alpha=1))
        res_info = f"Time resolution: {self.time_factor * self.tsamp * 1e6} us, " \
                  f"Frequency resolution: {self.freq_factor * self.freqres} MHz"
        self.ax_space.text(0., 0.18, res_info, transform=self.ax_space.transAxes, fontsize=10,
                          color='white', verticalalignment='top', bbox=dict(facecolor='darkviolet', alpha=1))       
        
        
        # create stage 1 buttons
        self.clear_buttons()
        ax_btn_td = self.figure.add_axes([0.1, 0.95, 0.1, 0.04])
        ax_btn_tu = self.figure.add_axes([0.1, 0.9, 0.1, 0.04])
        ax_btn_fd = self.figure.add_axes([0.21, 0.95, 0.1, 0.04])
        ax_btn_fu = self.figure.add_axes([0.21, 0.9, 0.1, 0.04])
        ax_btn_rr = self.figure.add_axes([0.4, 0.95, 0.1, 0.04])
        ax_btn_dm = self.figure.add_axes([0.4, 0.9, 0.1, 0.04])
        ax_text_dm = self.figure.add_axes([0.51, 0.9, 0.1, 0.04])
        ax_btn_n1 = self.figure.add_axes([0.6, 0.95, 0.1, 0.04])

        self.btn_td = Button(ax_btn_td, 'Time Down') 
        self.btn_td.on_clicked(self.on_timeres_down)
        self.btn_tu = Button(ax_btn_tu, 'Time Up') 
        self.btn_tu.on_clicked(self.on_timeres_up)
        self.btn_fd = Button(ax_btn_fd, 'Freq Down') 
        self.btn_fd.on_clicked(self.on_freqres_down)
        self.btn_fu = Button(ax_btn_fu, 'Freq Up') 
        self.btn_fu.on_clicked(self.on_freqres_up)
        self.btn_rr = Button(ax_btn_rr, 'Reset') 
        self.btn_rr. on_clicked(self.on_reset_preview)
        self.btn_n1 = Button(ax_btn_n1, 'Next') 
        self.btn_n1.on_clicked(self.on_next_from_preview)
        self.btn_dm = Button(ax_btn_dm, 'Set DM') 
        self.btn_dm.on_clicked(self.on_set_dm)
        self.text_dm = TextBox(ax_text_dm, '', initial=str(self.args.dm))
        self.button_cids.extend([self.btn_td, self.btn_tu, self.btn_fd, self.btn_fu, self.btn_rr, self.btn_n1, self.btn_dm, self.text_dm])
        self.button_axes.extend([ax_btn_td, ax_btn_tu, ax_btn_fd, ax_btn_fu, ax_btn_rr, ax_btn_n1, ax_btn_dm, ax_text_dm])
        
        # Hide some tick labels
        plt.setp(self.ax_space.get_xticklabels(), visible=False)
        plt.setp(self.ax_space.get_yticklabels(), visible=False)
        plt.setp(self.ax_top.get_xticklabels(), visible=False)
        plt.setp(self.ax_top.get_yticklabels(), visible=False)
        plt.setp(self.ax_right.get_yticklabels(), visible=False)
        plt.setp(self.ax_right.get_yticklabels(), visible=False)

        
    # Create stage 1 button functions
    def on_timeres_down(self, event):
        self.time_factor *= 2
        array = self.data[:,self.crop_start:self.crop_end]
        splicet = array.shape[1]-array.shape[1]%self.time_factor
        splicef = array.shape[0]-array.shape[0]%self.freq_factor
        array = array[:splicef,:splicet]
        self.masked_ds = basic_funcs.decimate_2d(arr=array, tfac=self.time_factor, ffac=self.freq_factor)
        self.update_plot()

    def on_timeres_up(self, event):
        if self.time_factor > 1:
            self.time_factor //= 2
        array = self.data[:,self.crop_start:self.crop_end]
        splicet = array.shape[1]-array.shape[1]%self.time_factor
        splicef = array.shape[0]-array.shape[0]%self.freq_factor
        array = array[:splicef,:splicet]
        self.masked_ds = basic_funcs.decimate_2d(arr=array, tfac=self.time_factor, ffac=self.freq_factor)
        self.update_plot()

    def on_freqres_down(self, event):
        self.freq_factor *= 2
        array = self.data[:,self.crop_start:self.crop_end]
        splicet = array.shape[1]-array.shape[1]%self.time_factor
        splicef = array.shape[0]-array.shape[0]%self.freq_factor
        array = array[:splicef,:splicet]
        self.masked_ds = basic_funcs.decimate_2d(arr=array, tfac=self.time_factor, ffac=self.freq_factor)
        self.update_plot()

    def on_freqres_up(self, event):
        if self.freq_factor > 1:
            self.freq_factor //= 2
        array = self.data[:,self.crop_start:self.crop_end]
        splicet = array.shape[1]-array.shape[1]%self.time_factor
        splicef = array.shape[0]-array.shape[0]%self.freq_factor
        array = array[:splicef,:splicet]
        self.masked_ds = basic_funcs.decimate_2d(arr=array, tfac=self.time_factor, ffac=self.freq_factor)
        self.update_plot()

    def on_reset_preview(self, event): # see __init__ for more info
        self.time_factor = 1
        self.freq_factor = 1
        self.crop_start = 0
        self.crop_end = self.data.shape[1]
        self.event_start = 2 * (self.data.shape[1] // 10)
        self.event_end = 3 * (self.data.shape[1] // 10)
        self.masked_ds = None
        
        self.peak_positions = []
        self.burst_regions = []
        self.manual_peaks = False
        self.crop_clicks = []
        self.event_clicks = []
        self.onburst_clicks = []
        self.update_plot()
        
    def on_next_from_preview(self, event):
        #Move to Stage 2: RFI flagging,
        self.stage = "flag"
        self.time_factor = 1
        self.freq_factor = 1
        self.masked_ds = self.data.copy()
        self.masked_ds = np.ma.masked_array(self.masked_ds, mask=np.zeros(self.masked_ds.shape, dtype=bool), fill_value=np.nan)[:,self.crop_start:self.crop_end]
        self.update_plot() 

    def on_set_dm(self, event):
        # Set time/frequency resolution back to original
        new_dm = float(self.text_dm.text)
        if new_dm is not None:
            self.time_factor = 1
            self.freq_factor = 1
            delta_dm = new_dm - self.args.dm
            self.masked_ds = self.data.copy()
            self.masked_ds = basic_funcs.dedisperse(self.masked_ds, delta_dm, self.freqs, self.tsamp)
            self.data = basic_funcs.dedisperse(self.data, delta_dm, self.freqs, self.tsamp)
            self.masked_ds = self.masked_ds[:,self.crop_start:self.crop_end]
            self.args.dm = new_dm
            self.update_plot()
            
    # STAGE 2 FLAG
    def draw_flag(self):
        # plot main panel
        self.ax_main.imshow(self.masked_ds, aspect='auto', origin='lower', extent=[self.crop_start, self.crop_end, 0, self.masked_ds.shape[0]],
                            cmap='viridis', interpolation='hanning', vmin=self.vmin, vmax=self.vmax)
        self.ax_main.set_xlim([self.crop_start, self.crop_end])
        self.ax_main.set_xlabel('Time bins')
        self.ax_main.set_ylabel('Frequency channels') 
        # plot top panel
        self.ax_top.plot(np.linspace(self.crop_start, self.crop_end, self.masked_ds.shape[1]), self.masked_ds.sum(axis=0), drawstyle='steps-mid', color='k')
        self.ax_top.axvline(self.event_start, color='darkviolet', lw=1)
        self.ax_top.axvline(self.event_end, color='darkviolet', lw=1)
        for region in self.burst_regions:
            start, end = region
            self.ax_top.fill_between([start, end], 0, 1, color='orange', alpha = 0.3, transform=self.ax_top.get_xaxis_transform())
        # plot side panel
        self.ax_right.plot(self.masked_ds.sum(axis=1), np.arange(self.masked_ds.shape[0]), drawstyle='steps-mid', color='k')
        for p in self.peak_positions: # peak positions
            self.ax_top.axvline(p, ls='--', color='red', lw=1)
        # plot side panel
        self.ax_right.plot(np.nansum(self.masked_ds,axis=1), np.arange(self.masked_ds.shape[0]), drawstyle='steps-mid', color='k')
        self.ax_right.axhline(self.spec_ex_lo, color='darkviolet', ls='--') # specex
        self.ax_right.axhline(self.spec_ex_hi, color='darkviolet', ls='--')
        
        
        # some instruction annotations
        self.ax_space.text(0., 0.7, "Hold 'b' and click twice on the spectrum to select spectral extent.",
                           transform=self.ax_space.transAxes, fontsize=10, color='white',
                           verticalalignment='top', bbox=dict(facecolor='darkviolet', alpha=1))
        self.ax_space.text(0., 0.45, "Hold 'z' and click to zap individual channels.\n"
                                     "Left then right click to zap a range.",
                           transform=self.ax_space.transAxes, fontsize=10, color='white',
                           verticalalignment='top', bbox=dict(facecolor='darkviolet', alpha=1))
        
        # create stage 2 buttons
        self.clear_buttons()
        ax_btn_resetmask = self.figure.add_axes([0.1, 0.85, 0.1, 0.04])
        ax_btn_undo = self.figure.add_axes([0.1, 0.92, 0.1, 0.04])
        ax_btn_jess = self.figure.add_axes([0.21, 0.85, 0.1, 0.04])
        ax_btn_clr = self.figure.add_axes([0.21, 0.92, 0.1, 0.04])
        ax_btn_n2 = self.figure.add_axes([0.32, 0.92, 0.1, 0.04])
        self.btn_resetmask = Button(ax_btn_resetmask, 'Reset') 
        self.btn_resetmask.on_clicked(self.on_reset_mask)
        self.btn_undo=Button(ax_btn_undo, 'Undo') 
        self.btn_undo.on_clicked(self.on_undo_mask)
        self.btn_jess=Button(ax_btn_jess, 'Jess') 
        self.btn_jess.on_clicked(self.on_jess)
        self.btn_clr=Button(ax_btn_clr, 'Colour') 
        self.btn_clr.on_clicked(self.on_colour)
        self.btn_n2=Button(ax_btn_n2, 'Next') 
        self.btn_n2.on_clicked(self.on_next_from_flag)
        self.button_cids.extend([self.btn_resetmask, self.btn_undo, self.btn_jess, self.btn_clr, self.btn_n2])
        self.button_axes.extend([ax_btn_resetmask, ax_btn_undo, ax_btn_jess, ax_btn_clr, ax_btn_n2])

        # Hide some tick labels
        plt.setp(self.ax_space.get_xticklabels(), visible=False)
        plt.setp(self.ax_space.get_yticklabels(), visible=False)
        plt.setp(self.ax_top.get_xticklabels(), visible=False)
        plt.setp(self.ax_top.get_yticklabels(), visible=False)
        plt.setp(self.ax_right.get_yticklabels(), visible=False)
        plt.setp(self.ax_right.get_yticklabels(), visible=False)



    # Create stage 2 button functions
    def on_reset_mask(self, event):
        self.masked_ds.mask[:, :] = False
        self.masked_channels = []
        self.update_plot()

    def on_undo_mask(self, event):
        if len(self.masked_channels) > 0:
            latest_channel = int(self.masked_channels[-1])
            self.masked_channels = self.masked_channels[:-1]
            print(f'Removing channel {latest_channel} from mask')
            self.masked_ds.mask[latest_channel,:] = False
            self.update_plot()

    def on_jess(self, event):
        offburst = np.concatenate((self.masked_ds[:, :self.event_start],
                                   self.masked_ds[:, self.event_end:]), axis=1) # define the offburst region for jess
        bool_mask = jess.channel_masks.channel_masker(dynamic_spectra=offburst.T, test='skew', sigma=3, show_plots=False)
        for idx, bool in enumerate(bool_mask):
            if bool and idx not in self.masked_channels:
                self.masked_channels = np.append(self.masked_channels,idx)
                self.masked_ds.mask[idx, :] = True
        self.update_plot()

    def on_colour(self, event):
        #cycle through some colormaps
        if self.cmap == 'seismic':
            self.cmap = 'viridis'
        elif self.cmap == 'viridis':
            self.cmap = 'bone'
        elif self.cmap == 'bone':
            self.cmap = 'gist_rainbow'
        elif self.cmap == 'gist_rainbow':
            self.cmap = 'seismic'
        else:
            self.cmap = 'viridis'
        self.update_plot()

    def on_next_from_flag(self, event):
        self.stage = "compute"
        self.update_plot()


    # STAGE 3 COMPUTE
    def draw_compute(self):
        self.clear_buttons()
        # convert timebinsand vertical lines in top panel to milliseconds
        zero_time_range = np.linspace(self.crop_start*self.tsamp*1e3, self.crop_end*self.tsamp*1e3, self.masked_ds.shape[1])
        t_event_start = self.event_start // self.time_factor * self.tsamp * 1000  # in ms now
        t_event_end = self.event_end // self.time_factor * self.tsamp * 1000  # in ms now
        self.t_peak_positions = [((pp // self.time_factor)) * self.tsamp * 1000 for pp in self.peak_positions]
        
        #plot
        self.ax_main.imshow(self.masked_ds, aspect='auto', origin='lower', interpolation='hanning', vmin=self.vmin, vmax=self.vmax, 
                            extent=[zero_time_range[0],zero_time_range[-1],self.freqs[0],self.freqs[-1]])
        self.ax_main.set_xlabel('Time (ms)')
        self.ax_main.set_ylabel('Frequency (MHz)') 
        # plot top panel
        prof = np.nanmean(self.masked_ds, axis=0)
        offprof = np.concatenate([prof[0:self.event_start-self.crop_start], prof[self.event_end-self.crop_start:]])
        self.prof = (prof - np.nanmean(offprof)) / np.nanstd(offprof)
        burstonly_prof = np.nanmean(self.masked_ds[self.spec_ex_lo:self.spec_ex_hi, :], axis=0)
        burstonly_offprof = np.concatenate([burstonly_prof[0:self.event_start-self.crop_start], burstonly_prof[self.event_end-self.crop_start:]])
        burstonly_prof = (burstonly_prof - np.nanmean(burstonly_offprof)) / np.nanstd(burstonly_offprof)     
        
        self.ax_top.plot(zero_time_range, burstonly_prof, drawstyle='steps-mid', color='silver')
        self.ax_top.plot(zero_time_range, self.prof, drawstyle='steps-mid', color='k')  
        self.ax_top.axvline(t_event_start, color='darkviolet', ls='--') 
        self.ax_top.axvline(t_event_end, color='darkviolet', ls='--') 
     #   for region in self.burst_regions:
     #       start, end = region
     #       self.ax_top.fill_between([start/self.time_factor, end/self.time_factor], 0, 1, color='orange', alpha = 0.3, transform=self.ax_top.get_xaxis_transform())


        for t in self.t_peak_positions: 
            self.ax_top.axvline(t, ls='--', color='red', lw=1)
        self.ax_top.set_ylabel('S/N')
        # plot side panel
        self.ax_right.plot(np.nanmean(self.masked_ds[:, self.event_start:self.event_end], axis=1), self.freqs, drawstyle='steps-mid', color='silver')
        self.ax_right.plot(np.nanmean(self.masked_ds, axis=1), self.freqs, drawstyle='steps-mid', color='k')        
        self.ax_right.axhline(self.freqs[self.spec_ex_lo], color='darkviolet', ls='--') # specex
        self.ax_right.axhline(self.freqs[self.spec_ex_hi], color='darkviolet', ls='--')

        plt.setp(self.ax_top.get_xticklabels(), visible=False)
        plt.setp(self.ax_right.get_yticklabels(), visible=False)
        
        # create stage 3 buttons
        self.clear_buttons()
        ax_btn_save = self.figure.add_axes([0.7, 0.85, 0.1, 0.04])
        ax_btn_nextburst = self.figure.add_axes([0.7, 0.8, 0.1, 0.04])
        ax_btn_properties = self.figure.add_axes([0.7, 0.75, 0.1, 0.04])
        ax_btn_badfit = self.figure.add_axes([0.5, 0.8, 0.1, 0.04])
        ax_btn_special = self.figure.add_axes([0.5, 0.75, 0.1, 0.04])
        
        self.btn_save = Button(ax_btn_save, 'Save')
        self.btn_nextburst = Button(ax_btn_nextburst, 'Next Burst')
        self.btn_properties = Button(ax_btn_properties, 'Burst Properties')
        self.btn_badfit = Button(ax_btn_badfit, 'Bad Fit')
        self.btn_special = Button(ax_btn_special, 'Special')
        
        self.btn_save.on_clicked(self.on_save)
        self.btn_nextburst.on_clicked(self.on_nextburst)
        self.btn_properties.on_clicked(self.get_burst_properties)
        self.btn_badfit.on_clicked(self.on_badfit)
        self.btn_special.on_clicked(self.on_special)
        
        self.button_cids.extend([self.btn_save, self.btn_nextburst, self.btn_properties, self.btn_badfit, self.btn_special])
        self.button_axes.extend([ax_btn_save, ax_btn_nextburst, ax_btn_properties, ax_btn_badfit, ax_btn_special])
        
        # Hide some tick labels
        plt.setp(self.ax_space.get_xticklabels(), visible=False)
        plt.setp(self.ax_space.get_yticklabels(), visible=False)
        self.ax_space.tick_params(axis='y', which='both', length=0)
        plt.setp(self.ax_right.get_xticklabels(), visible=False)
        plt.setp(self.ax_right.get_yticklabels(), visible=False)

    # compute burst properties
    def get_burst_properties(self, event):
        # Gaussian fit to each burst region
        zero_time_range = np.linspace(self.crop_start*self.tsamp*1e3, self.crop_end*self.tsamp*1e3, self.masked_ds.shape[1])
        for region in self.burst_regions:
            start, end = region
            start = start - self.crop_start
            end = end - self.crop_start
            xdata = zero_time_range[start:end]
            ydata = self.prof[start:end]
            initial_guess = (np.max(ydata), xdata[np.argmax(ydata)], (xdata[1]-xdata[0])/2, 0) #/2 is somewhat arbitrary
            bounds = ([0, xdata[0], 0, -10], [np.inf, xdata[-1], xdata[-1]-xdata[0], 10])
            popt, _ = curve_fit(basic_funcs.gaussian_1d, xdata, ydata, p0 = initial_guess, bounds=bounds, maxfev=10000)
            self.ax_top.plot(xdata, basic_funcs.gaussian_1d(xdata,*popt), color='red')
            self.onedgauss_fits.append(popt)
        
        # Temporal
        self.MJD = self.start_mjd + (self.plus_mjd_sec_updated / (24 * 3600))
        filename = Path(self.burst_file).name
        if args.telescope == 'NRT':
            self.burst_time_from_filename = float(filename.split('_')[1].split('s')[0]) #This is NRT specific
            self.MJD_offset = (self.MJD - self.burst_time_from_filename) * (24 * 3600) * 1e3  # in ms
        t_event_start = self.event_start * self.tsamp * 1e3
        t_event_end = self.event_end * self.tsamp * 1e3
        self.event_duration = t_event_end - t_event_start
        
        # Energetics
        self.integrated_sn = self.prof[self.event_start-self.crop_start:self.event_end-self.crop_start]
        flux_prof = self.prof[self.event_start-self.crop_start:self.event_end-self.crop_start] * basic_funcs.radiometer(self.tsamp * 1e3, self.bw, self.npol, self.SEFD)
        self.peak_flux = np.max(flux_prof)
        self.fluence_Jyms = np.sum(flux_prof * self.tsamp * 1e3)
        self.iso_E = None
        if self.args.distance and self.args.redshift:
            self.iso_E = 4 * np.pi * self.fluence_Jyms * u.Jy * u.ms * self.bw * u.MHz * \
                    (self.args.distance * u.megaparsec)**2 / (1 + self.args.redshift)
        # Spectral
        
        # print values
        annotation = (f"Fluence: {np.round(self.fluence_Jyms, 2)} Jy ms \n"
                    f"Peak Flux Density: {np.round(self.peak_flux, 2)} Jy \n"
                    f"Event duration: {np.round(t_event_end - t_event_start, 2)} ms \n"
                    f"Spectral extent: {self.freqs[self.spec_ex_hi] - self.freqs[self.spec_ex_lo]} MHz \n"
                    f"MJD @ peak: {self.MJD} \n")
#                    f"MJD offset: {np.round(self.MJD_offset, 3)} ms")
        self.ax_space.text(0., 0.98, annotation, transform=self.ax_space.transAxes,
                        fontsize=10, color='white', verticalalignment='top',
                        bbox=dict(facecolor='black', alpha=1))
        self.figure.canvas.draw_idle()
    
    def fit_gaussian_1d(self, xdata, ydata, initial_guess):
        bounds = ([0, xdata[0], 0, -10], [np.inf, xdata[-1], xdata[-1]-xdata[0], 10])
        popt, _ = curve_fit(basic_funcs.gaussian_1d, xdata, ydata, p0=initial_guess, bounds=bounds)
        return popt
    
    def fit_gaussian_2d(self, x, y, array, initial_guess):
        xx, yy = np.meshgrid(x, y)
        xdata = np.vstack((xx.ravel(), yy.ravel()))
        ydata = array.ravel()
        bounds = ([0, x[0], y[0], 0, 0, -10], [np.inf, x[-1], y[-1], x[-1]-x[0], y[-1]-y[0], 10])
        popt, _ = curve_fit(basic_funcs.gaussian_2d, xdata, ydata, p0=initial_guess, bounds=bounds)
        return popt
    
    def plot_2d_gaussian_fit(self, x, y, array, popt):
        xx, yy = np.meshgrid(x, y)
        
        fitted_gaussian = basic_funcs.gaussian_2d((xx, yy), *popt).reshape(array.shape)
        spectrum = np.mean(fitted_gaussian, axis=1)
        # Fit 1D Gaussian to normalized timeseries for proper scaling
        poptx = self.fit_gaussian_1d(x, self.prof, initial_guess=(np.max(self.prof), popt[1], popt[3], 0))
        print(poptx)
        timeseries = basic_funcs.gaussian_1d(x, *poptx)
        self.ax_main.contour(xx, yy, fitted_gaussian, levels=[np.max(fitted_gaussian)*0.05, np.max(fitted_gaussian)*0.34])
        self.ax_top.plot(x, timeseries, color='red', linestyle='--')
        # self.ax_right.plot(spectrum, y, color='red', linestyle='--')
        self.figure.canvas.draw_idle()

    # Create stage 3 button functions
    def on_save(self, event):
        # Initialize DataFrame for burst properties
        if args.burst_df:
            self.burst_df = pd.read_csv(args.burst_df)
        else:
            args.burst_df = 'burst_properties.csv'
            self.burst_df = pd.DataFrame(columns=[
                "burst_name", "DM", "MJD_at_peak", "peak_positions_ms", "peak_flux",
                "fluence_Jyms", "iso_E", "event_duration_ms", "1D_Gaussian_fits", "acf_fits", "spectral_extent_MHz", "mask", "bad_fit?", "special?", "integrated_s/n"
            ])
            
        # create a dictionary for the current burst
        burst_props = {
            "burst_name": Path(self.burst_file).stem,
            "DM": self.args.dm,
            "MJD_at_peak": self.MJD,
 #           "MJD_offset_ms": self.MJD_offset,
            "peak_positions_ms": self.t_peak_positions,
            "peak_flux": self.peak_flux,
            "fluence_Jyms": self.fluence_Jyms,
            "iso_E": self.iso_E,
            "event_duration_ms": self.event_duration,
            "1D_Gaussian_fits": self.onedgauss_fits,
            "acf_fits": self.acf_fits,
            "spectral_extent_MHz": self.freqs[self.spec_ex_hi] - self.freqs[self.spec_ex_lo],
            "mask": self.masked_channels,
            "bad_fit?": self.badfit,
            "special?": self.is_special,
            "integrated_s/n": self.integrated_sn.data
        }
        
        # Check if burst name already exists in the DataFrame
        if burst_props["burst_name"] in self.burst_df["burst_name"].values:
            print(f"Burst properties for {burst_props['burst_name']} already exist. Skipping save.")

        else:
            # Potentially save the entire burst dynamic spectrum array/profile, to load the .npz use np.load, .files gives the idx
            if args.array_save_mode:
                # burst_database = {'file':self.burst_file, 'ds': self.masked_ds, 'prof':self.prof}
                np.savez(f"{Path(self.burst_file)}.npz", data=self.masked_ds, profile=self.prof, mask=self.masked_channels)
            # Append the burst properties to the DataFrame
            self.burst_df = pd.concat([self.burst_df, pd.DataFrame([burst_props])], ignore_index=True)

            # Save the DataFrame to a CSV file
            self.burst_df.to_csv(self.args.burst_df, index=False)

            print(f"Burst properties saved for {burst_props['burst_name']}")


    def on_nextburst(self, event):
        plt.close()
    
    def on_special(self, event):
        self.is_special = not self.is_special
        print(f"Special flag set to {self.is_special}")
        
    def on_badfit(self, event):
        options = [False, 'acf', 'gaussian', 'both']
        self.badfit = options[(options.index(self.badfit) + 1) % len(options)]
        print(f"Bad fit flag set to {self.badfit}")
        
    # Click functionality
    def on_click(self, event):

        # STAGE 1 CLICKS
        if self.stage == "preview":
            # top panel clicks
            # crop clicking
            if event.inaxes == self.ax_top and event.xdata is not None:
                x_disp = int(np.round(event.xdata))
                x_og = x_disp # convert to og resolution
                
                # CROP
                if event.key == 'c':
                    self.crop_clicks.append(x_og)
                    if args.verbose:
                        print(f"Crop click at displayed x={x_disp} -> original x={x_og}")
                        
                    if len(self.crop_clicks) == 2:
                        new_crop_start, new_crop_end = sorted(self.crop_clicks)
                        if new_crop_end > new_crop_start: # some validation
                            old_crop_start = self.crop_start #save the current crop start for the offset calculation
                            self.crop_start = new_crop_start
                            self.crop_end =  new_crop_end
                            self.masked_ds = self.masked_ds[:, (new_crop_start-old_crop_start)//self.time_factor:(new_crop_end-old_crop_start)//self.time_factor]
                            if args.verbose:
                                print(f"New crop from og timebin {self.crop_start} to {self.crop_end}")
                            self.crop_clicks = []
                            self.update_plot()
                        else:
                            if args.verbose:
                                print("Invalid crop selection: end must be greater than start.")
                            self.crop_clicks = []
                            self.update_plot()

                # EVENT SELECTION
                elif event.key == 'e':
                    self.event_clicks.append(x_og)
                    if args.verbose:
                        print(f"Event click at displayed x={x_disp} -> original x={x_og}")
                    if len(self.event_clicks) == 2:
                        self.event_start, self.event_end = sorted(self.event_clicks)
                        if self.event_end > self.event_start:
                            if args.verbose:
                                print(f"Event from column {self.event_start} to {self.event_end}")
                            self.event_clicks = []
                            self.update_plot()
                        else:
                            if args.verbose:
                                print("Invalid selection: event end must be greater than event start.")
                            self.event_clicks = []
                            self.update_plot()
                            
                # BURST REGIONS           
                elif event.key == 'b':
                    self.onburst_clicks.append(x_og)
                    if self.args.verbose:
                        print(f"Onburst click at displayed x={x_disp} -> original x={x_og}")
                    if len(self.onburst_clicks) == 2:
                        start, end = sorted(self.onburst_clicks)
                        if end > start:
                            self.burst_regions.append((start, end))
                            if self.args.verbose:
                                print(f"Added burst region from column {start} to {end}")
                        else:
                            if self.args.verbose:
                                print("Invalid selection: burst region end must be greater than burst region start.")
                        self.onburst_clicks = []
                        self.update_plot()
                        
                # PEAK SELECTION
                # add a peak
                elif event.key == 'm': 
                    p_og = x_disp
                    self.manual_peaks = True
                    self.peak_positions.append(p_og)
                    if args.verbose:
                        print(f"Peak position set to x={x_disp} -> original x={p_og}")
                    self.update_plot()
                # remove a peak
                elif event.key == 'd':
                    tol = 20 # how close to click in bins to remove the peak
                    if self.peak_positions:
                        disp_peaks = [ p for p in self.peak_positions ]
                        distances = [abs(p_disp - x_disp) for p_disp in disp_peaks]#find closest peak to click
                        min_dist = min(distances)
                        if min_dist <= tol:
                            index = distances.index(min_dist)
                            removed =self.peak_positions.pop(index)
                            if args.verbose:
                                print(f"Removed peak at {removed}")
                            if not self.peak_positions: # if all peaks removed
                                self.manual_peaks = False # so we automatically detect one
                            self.update_plot()
                        else:
                            if args.verbose:
                                print("No peak near the clicked position to remove.")
            if event.inaxes == self.ax_right and event.ydata is not None:
                y_disp = int(np.round(event.ydata))
                y_og = y_disp
                if event.key == 'e':
                    self.spec_ex_clicks.append(y_og)
                    if args.verbose:
                        print(f"Specex click at displayed y={y_disp} -> original y={y_og}")
                    if len(self.spec_ex_clicks) == 2:
                        self.spec_ex_lo, self.spec_ex_hi = sorted(self.spec_ex_clicks)
                        if self.spec_ex_hi > self.spec_ex_lo:
                            print(f"Spectral extent from from channel {self.spec_ex_lo} to {self.spec_ex_hi}")
                            self.spec_ex_clicks = []
                            self.update_plot()
                        else:
                            print("Invalid selection.")
                            self.spec_ex_clicks = []
                            self.update_plot()
        # STAGE 2 CLICKS
        elif self.stage == "flag":
            # main panel clicks
            if event.inaxes == self.ax_main and event.ydata is not None:
                y = int(np.round(event.ydata))
                # z for single channel zap
                if event.key == 'z': 
                    if (0 <= y < self.masked_ds.shape[0]) and y not in self.masked_channels:
                        if args.verbose:
                            print(f"Zapped channel {y}")
                        self.masked_channels = np.append(self.masked_channels, y)
                        self.masked_ds.mask[y,:] = True
                        self.update_plot()
                else:
                    # left click sets start of range to zaps
                    if event.button == 1:
                        if args.verbose:
                            print(f"Starting zap range at {y}")
                        self.zap_start = y
                    # right click sets end of range to zap
                    elif event.button == 3 and self.zap_start is not None:
                        if args.verbose:
                            print(f"Ending zap range at {y}")
                        self.zap_end = y
                        #make the order not matter
                        low, high = min(self.zap_start,self.zap_end),max(self.zap_start,self.zap_end)
                        # zap a range
                        for chan in range(low,high+1):
                            if chan not in self.masked_channels:
                                self.masked_channels = np.append(self.masked_channels, chan)
                                self.masked_ds.mask[chan,:] = True
                        self.zap_start = None
                        self.update_plot()
                        
            # right panel clicks
            if event.inaxes == self.ax_right and event.key == 'b' and event.ydata is not None:
                y_disp = int(np.round(event.ydata))
                y_og = y_disp * self.freq_factor
                self.spec_ex_clicks.append(y_og)
                if args.verbose:
                    print(f"Specex click at displayed y={y_disp} -> original y={y_og}")
                if len(self.spec_ex_clicks) == 2:
                    self.spec_ex_lo, self.spec_ex_hi = sorted(self.spec_ex_clicks)
                    if self.spec_ex_hi > self.spec_ex_lo:
                        print(f"Spectral extent from from channel {self.spec_ex_lo} to {self.spec_ex_hi}")
                        self.update_plot()
                    else:
                        print("Invalid selection.")
                        self.spec_ex_clicks = []
                        self.update_plot()
        # STAGE 3 CLICKS
        elif self.stage == "compute":
            if event.inaxes == self.ax_main:
                x = int(np.round(event.xdata))
                y = int(np.round(event.ydata))
                if x is not None and y is not None:
                    xdata = np.linspace(self.crop_start*self.tsamp*1e3, self.crop_end*self.tsamp*1e3, self.masked_ds.shape[1])
                    ydata = np.linspace(self.freqs[0], self.freqs[-1], self.masked_ds.shape[0])
                    acf = basic_funcs.acf_2d(self.masked_ds)
                    timeseries = np.mean(acf, axis=0)
                    spectra = np.mean(acf, axis=1)
                    print('starting acf fit')
                    initial_guess = (np.max(acf), xdata[np.argmax(timeseries)], ydata[np.argmax(spectra)],
                                     5, 50, 0)
                    popt_acf = self.fit_gaussian_2d(xdata, ydata, acf, initial_guess) 
                    self.acf_fits = popt_acf
                    timeseries = np.mean(self.masked_ds, axis=0)
                    spectra = np.mean(self.masked_ds, axis=1)

                    print('starting ds fit')
                    initial_guess = (np.max(self.masked_ds), x, popt_acf[3]/np.sqrt(2), 0)
                    poptx = self.fit_gaussian_1d(xdata, timeseries, initial_guess)
                    print(initial_guess, poptx)
                    initial_guess = (np.max(self.masked_ds), y, popt_acf[4]/np.sqrt(2), 0)
                    print(initial_guess)
                    popty = self.fit_gaussian_1d(ydata, spectra, initial_guess)
                    # Combine parameters: center and amplitude from ds, widths from acf adjusted by sqrt(2)
                    combined_popt = (np.max(self.masked_ds), poptx[1], popty[1], popt_acf[3] / np.sqrt(2), 
                                     popt_acf[4] / np.sqrt(2), 0)
                    print('plotting', combined_popt)
                    self.plot_2d_gaussian_fit(xdata, ydata, self.masked_ds, combined_popt)

    def run(self):
        plt.show()




# MAIN
if __name__ == "__main__":
    # parser stuff
    parser = argparse.ArgumentParser(description="Analyze bursts, now, tomorrow, together.")
    parser.add_argument("-b","--bfile",type=str,
                        help="Name of the burst filterbank e.g. burst_60531.37442289023s4p1t38.5845_500ms.fil",required=False)
    parser.add_argument("-B","--Bfiles",type=str,
                        help="A text file containing a list of burst names, one on each line",required=False)
    parser.add_argument("-f","--flags",type=str,
                        help="Wildcard flag to use to search for filterbank files, e.g. *60350.*", required=False)
    parser.add_argument("-d","--dm",type=float,
                        help="The dispersion measure (DM) of the burst in pc/cc (float)",required=True)
    parser.add_argument("-t","--telescope",type=str,
                        help="The telescope, to calculate the fluence. Supported options: NRT",required=True)
    parser.add_argument("-l","--distance",type=float,
                        help="Distance in Mpc to source.",required=False)
    parser.add_argument("-z","--redshift",type=float,
                        help="redshift to source.",required=False)
    parser.add_argument("-v", "--verbose", action="store_true", 
                        help="Increase output verbosity")
    parser.add_argument("-s", "--burst_df", type=str,
                        help="Path to an existing burst properties CSV file", required=False)
    parser.add_argument("-j", "--jess", action="store_true",
                        help="Enables jess-flagging in the first preview stage. Useful for spotting weaker bursts.")
    parser.add_argument("-a", "--array_save_mode", action="store_true",
                        help="In addition to a database file to save burst properties, also save the cropped and flagged dynamic spectra to plot.")
    
    args = parser.parse_args()
    #create dictionary for all burst's properties, check verbosity
    global verbose, burst_database
    burst_database ={}
    if not args.verbose:
        verbose = False
        
    #process bursts
    if args.bfile:
        burst_file = args.bfile
        data, metadata = load_burst(burst_file,args.dm,args.telescope)
        analyzer = BurstAnalyzer(data, metadata, burst_file, args)
        analyzer.run()
        # np.savez_compressed("burst_database.npz",**burst_database)
    elif args.Bfiles:
        try:
            with open(args.Bfiles, 'r') as f:
                for line in f:
                    burst_file = line.strip()
                    if burst_file:
                        data, metadata = load_burst(burst_file,args.dm,args.telescope)
                        analyzer = BurstAnalyzer(data, metadata, burst_file, args)
                        analyzer.run()
                        # np.savez_compressed("burst_database.npz",**burst_database)
        except Exception as e:
            print(f"Error reading file {args.Bfiles}: {e}")
            sys.exit(1)
    elif args.flags:
        try:
            for burst_file in glob.glob(f"/mnt/d/R147/ECLAT_bursts_R147_CD/L/bursts/*{args.flags}*"):
                if '.npz' not in burst_file:
                    data, metadata = load_burst(burst_file,args.dm,args.telescope)
                    analyzer = BurstAnalyzer(data, metadata, burst_file, args)
                    analyzer.run()
                    # np.savez_compressed("burst_database.npz",**burst_database)
        except Exception as e:
            print(f"Error reading file {args.flags}: {e}")
            sys.exit(1)
    else:
        print("No burst filterbank name provided. Use -b for a single burst or -B for a file containing multiple burst names, one per line.")
        sys.exit(1)

    
