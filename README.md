# burst_analyzer
This is an interactive tool to read in burst snippet in filterbank format, zap RFI, and calculate burst properties.

There are 3 stages. 

## Stage 1: burst preview
Hold the 'c' key and click in the top panel to select a crop window. Similarly, the 'e' key is used to set the bounds of the event duration (for complex bursts or failed fits this can be used as a measure of the burst duration). The 'b' key is used to set bounds of either the bursts, or subbursts. In Stage 3 the code will attempt to fit 1-D Gaussians to each burst region. Peak positions can be added/deleted with the 'm'/'d' keys, e.g. to check the separation between different burst components. If the burst is not visible, you can downsample in time or frequency.

## Stage 2: RFI zapping
Left and right click to zap a range, or hold 'z' and click to zap a channel. Jess (https://github.com/josephwkania/jess?tab=readme-ov-file) can be called to provide automatic zapping. Also set the spectral extent of the burst here using the 'b' key and clicking in right panel.

## Stage 3: burst property calculation
Click on burst properties to calculate various burst properties and attempt 1D Gaussian fits to all burst regions. The save button will save these properties in a .csv. 

# Usage
python CLASSY_burst_analyzer.py -b burst.fil -d 123 -t NRT -v

Extracted burst properties:
- Fluence (Jy ms), calculated for the selected event duration over the entire bandwidth
- Peak flux density (Jy)
- Event duration (ms), manually selected 
- Spectral extent (MHz), manually selected
- MJD: Topocentric at NRT
