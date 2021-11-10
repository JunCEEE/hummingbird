# Import analysis/plotting modules
import analysis.event
import analysis.hitfinding
import analysis.pixel_detector
import analysis.patterson
import plotting.image
import plotting.line
import plotting.correlation
import plotting.histogram
import utils.cxiwriter
import ipc
from backend.record import add_record
import numpy as np
import time, os, sys
import h5py
import logging

# Commandline arguments
from utils.cmdline_args import argparser, add_config_file_argument
add_config_file_argument('--hitscore-threshold', metavar='INT',
                         help='Hitscore threshold', type=int)
add_config_file_argument('--gain-lvl', metavar='INT',
                         help='Gain level of pnccds', type=int)
add_config_file_argument('--multiscore-threshold', metavar='INT',
                         help='Multiscore threshold', type=int)
add_config_file_argument('--run-nr', metavar='INT',
                         help='Run number', type=int, required=True)
add_config_file_argument('--dark-nr', metavar='INT',
                         help='Run number of dark', type=int)
add_config_file_argument('--output-level', type=int, 
                         help='Output level (0: dry run, 1: small data for all events, 2: tof data for hits, \
                               3: pnccd data for hits, 4: all data for multiple hits)',
                         default=3)
add_config_file_argument('--outdir', metavar='STR',
                         help='output directory different from default (optional)', type=str)
add_config_file_argument('--nr-frames', type=int, 
                         help='Number of frames', default=None)
add_config_file_argument('--skip-tof', action='store_true')
add_config_file_argument('--only-save-multiples', action='store_true')
args = argparser.parse_args()

this_dir = os.path.dirname(os.path.realpath(__file__))                                                                             
sys.path.append(this_dir)

from conf import *

# Read in parameters from a csv file
import params
p = params.read_params('params.csv', args.run_nr)
gap_top = p['pnccdGapTopMM'] * 1E-3
gap_bottom = p['pnccdGapBottomMM'] * 1E-3
gap_total = gap_top + gap_bottom
center_shift = int((gap_top-gap_bottom)/pixel_size)

# Dark file
if args.dark_nr:
    darkfile_nr = args.dark_nr
else:
    darkfile_nr = p['darkNr']

# Hitscore threshold
if args.hitscore_threshold is not None:
    hitScoreThreshold = args.hitscore_threshold
else:
    hitScoreThreshold = p['hitscoreThreshold']

if args.gain_lvl is not None:
    gain_lvl = args.gain_lvl
else:
    gain_lvl = p['pnccdGainLevel']

if gain_lvl == 64:
    aduThreshold = 50
elif gain_lvl == 16:
    aduThreshold = 100
elif gain_lvl == 4:
    aduThreshold = 200
elif gain_lvl == 1:
    aduThreshold = 400
else:
    aduThreshold = 0
    logging.warning("Do not have tabulated value for chosen pnccd gain level %i. Setting aduThreshold to %i." % (gain_lvl, aduThreshold))
    time.sleep(2)

# Multiscore threshold
if args.multiscore_threshold is not None:
    multiScoreThreshold = args.multiscore_threshold
else:
    multiScoreThreshold = p['multiscoreThreshold']

if ipc.mpi.is_main_event_reader():
    print "hitscore threshold: %i" % hitScoreThreshold
    print "multiscore threshold: %i" % multiScoreThreshold
    time.sleep(2)
    
# Specify the facility
state = {}
state['Facility'] = 'FLASH'
# Specify folders with frms6 and darkcal data
state['FLASH/DataGlob']    = base_path + "raw/pnccd/block-*/holography_*_2017*_%04d_*.frms6" %args.run_nr
state['FLASH/CalibGlob']   = base_path + "processed/calib/block-*/calib_*_%04d.darkcal.h5"   %darkfile_nr
state['FLASH/DAQFolder']   = base_path + "processed/daq/"
state['FLASH/DAQBaseDir']  = base_path + "raw/hdf/block-03/"
state['FLASH/MotorFolder'] = '/home/tekeberg/Beamtimes/Holography2017/motor_positions/motor_data.data'
state['do_offline'] = True
state['online_start_from_run'] = False
state['reduce_nr_event_readers'] = 1
#state['FLASH/ProcessingRate'] = 1
    
# Geometry
move_half = True

# Output levels
level = args.output_level
save_anything = level > 0
save_tof = level >= 2 and not args.skip_tof                                                                                                      
save_pnccd = level >= 3
save_multiple = level >= 4

# Output directory
if args.outdir is None:
    w_dir = base_path + "processed/hummingbird/"
else:
    w_dir = args.outdir
filename_tmp  = w_dir + "/.r%04d_ol%d.h5" %(args.run_nr, level)
filename_done = w_dir + "/r%04d_ol%d.h5" %(args.run_nr, level)
D_solo = {}

# Counter
counter = -1

# Htscores
cache_length = 10000
hitscore_cache = np.zeros(cache_length)

def beginning_of_run():
    if save_anything:
        global W
        W = utils.cxiwriter.CXIWriter(filename_tmp, chunksize=10)

# This function is called for every single event
# following the given recipe of analysis
def onEvent(evt):

    # Counter
    global counter
    counter += 1

    # Option to stop after fixed number of frames
    if args.nr_frames is not None:
        #print counter, args.nr_frames/ipc.mpi.nr_event_readers()
        if (counter == args.nr_frames/ipc.mpi.nr_event_readers()):
            raise StopIteration

    # Processing rate [Hz]
    analysis.event.printProcessingRate()

    # Read FEL parameters
    try:
        wavelength_nm = evt['FEL']['wavelength'].data
        gmd = evt['FEL']['gmd'].data
    except RuntimeError:
        wavelength_nm = np.nan
        gmd = np.nan

    detector_type = detector_type_raw
    detector_key = detector_key_raw
    detector = evt[detector_type][detector_key]
    if move_half:
        detector_s = analysis.pixel_detector.moveHalf(evt, detector, horizontal=int(gap_total/pixel_size), outkey='data_half-moved')
        mask_center_s = analysis.pixel_detector.moveHalf(evt, add_record(evt["analysis"], "analysis", "mask", mask_center), 
                                                         horizontal=int(gap_total/pixel_size), outkey='mask_half-moved').data
        detector_type = "analysis"
        detector_key  = "data_half-moved"
        detector = evt[detector_type][detector_key]
    else:
        mask_center_s = mask_center

    # Do basic hitfinding using lit pixels
    analysis.hitfinding.countLitPixels(evt, detector,
                                       aduThreshold=aduThreshold, 
                                       hitscoreThreshold=hitScoreThreshold,
                                       mask=mask_center_s)
    hit = bool(evt["analysis"]["litpixel: isHit"].data)
    hitscore = evt['analysis']['litpixel: hitscore'].data
    global hitscore_cache
    hitscore_cache[counter % cache_length] = hitscore

    # Find multiple hits based on patterson function
    if hit:
        analysis.patterson.patterson(evt, "analysis", "data_half-moved", mask_center_s, 
                                     threshold=patterson_threshold,
                                     diameter_pix=patterson_diameter,
                                     xgap_pix=patterson_xgap_pix, ygap_pix=patterson_ygap_pix,
                                     frame_pix=patterson_frame_pix,
                                     crop=512, full_output=True, **patterson_params)
        #print evt['analysis'].keys()
        multiple_hit = evt["analysis"]["multiple score"].data > multiScoreThreshold

    # Write to file
    if save_anything:
        if hit and (not args.only_save_multiples or multiple_hit):
            D = {}
            D['entry_1'] = {}
            D['entry_1']['event'] = {}
            D['entry_1']['motors'] = {}
            D['entry_1']['FEL'] = {}
            D['entry_1']['result_1'] = {}
            if save_pnccd:
                D['entry_1']['detector_1'] = {}
            if save_tof:
                D['entry_1']['detector_2'] = {}

            # PNCCD
            if save_pnccd:
                D['entry_1']['detector_1']['data'] = np.asarray(detector.data, dtype='float16')
                if ipc.mpi.is_main_event_reader() and len(D_solo) == 0:
                    bitmask = np.array(mask_center_s, dtype='uint16')
                    bitmask[bitmask==0] = 512
                    bitmask[bitmask==1] = 0
                    D_solo["entry_1"] = {}
                    D_solo["entry_1"]["detector_1"] = {}
                    D_solo["entry_1"]["detector_1"]["mask"]= bitmask
            
            # PATTERSON
            if save_multiple:
                D['entry_1']['detector_1']['patterson'] = np.asarray(evt['analysis']['patterson'].data, dtype='float16')
                D['entry_1']['detector_1']['patterson_mask'] = np.asarray(evt['analysis']['patterson multiples'].data, dtype='bool') 

            # TOF
            if save_tof:
                # Read ToF traces
                try:
                    tof = evt["DAQ"]["TOF"]
                except RuntimeError:
                    logging.warning("Runtime error when reading TOF data.")
                    return
                except KeyError:
                    logging.warning("Key error when reading TOF data.")
                    return
                D['entry_1']['detector_2']['data'] = np.asarray(tof.data, dtype='float16')
            
            # HIT PARAMETERS
            D['entry_1']['result_1']['hitscore_litpixel'] = evt['analysis']['litpixel: hitscore'].data
            D['entry_1']['result_1']['hitscore_litpixel_threshold'] = hitScoreThreshold
            D['entry_1']['result_1']['multiscore_patterson'] = evt['analysis']['multiple score'].data
            D['entry_1']['result_1']['multiscore_patterson_threshold'] = multiScoreThreshold

            try:
                # FEL PARAMETERS
                D['entry_1']['FEL']['gmd'] = gmd
                D['entry_1']['FEL']['wavelength_nm'] = wavelength_nm
            except KeyError:
                logging.warning("Cannot find FEL data.")
                
            try:
                # EVENT IDENTIFIERS
                D['entry_1']['event']['bunch_id']   = evt['ID']['BunchID'].data
                D['entry_1']['event']['tv_sec']     = evt['ID']['tv_sec'].data
                D['entry_1']['event']['tv_usec']    = evt['ID']['tv_usec'].data
                D['entry_1']['event']['dataset_id'] = evt['ID']['DataSetID'].data
                D['entry_1']['event']['bunch_sec']  = evt['ID']['bunch_sec'].data 
            except KeyError:
                logging.warning("Cannot find event data.")

            try:
                # MOTORS
                D['entry_1']['motors']['manualy']       = evt['motorPositions']['ManualY'].data
                D['entry_1']['motors']['injectorx']     = evt['motorPositions']['InjectorX'].data
                D['entry_1']['motors']['injectory']     = evt['motorPositions']['InjectorZ'].data
                D['entry_1']['motors']['trigdelay']     = evt['motorPositions']['TrigDelay'].data
                D['entry_1']['motors']['samplepress']   = evt['motorPositions']['InjectorSamplePressure'].data
                D['entry_1']['motors']['nozzlepress']   = evt['motorPositions']['InjectorNozzlePressure'].data
                D['entry_1']['motors']['posdownstream'] = evt['motorPositions']['PosDownstream'].data
                D['entry_1']['motors']['posupstream']   = evt['motorPositions']['PosUpstream'].data
                D['entry_1']['motors']['injectorpress'] = evt['motorPositions']['InjectorPressure'].data
                D['entry_1']['motors']['focusinggas']   = evt['motorPositions']['InjectorFocusingGas'].data
            except KeyError:
                logging.warning("Cannot find motor data.")
        
            # TODO: FEL
            W.write_slice(D)

def end_of_run():
    if save_anything:
        if ipc.mpi.is_main_event_reader():
            if 'entry_1' not in D_solo:
                D_solo["entry_1"] = {}
            W.write_solo(D_solo)
        if ipc.mpi.size <= 2:
            W.close()
        else:
            W.close(barrier=True)
        if ipc.mpi.is_main_event_reader():
            with h5py.File(filename_tmp, 'a') as f:
                if save_pnccd and '/entry_1/detector_1' in f:
                    f['entry_1/data_1'] = h5py.SoftLink('/entry_1/detector_1')
                    f['entry_1/detector_1/data'].attrs['axes'] = ['experiment_identifier:y:x']
                    n_frames = (len(f['/entry_1/data_1/data']))
                else:
                    n_frames = 0
                print "Counting in total %i frames." % n_frames
                if save_multiple and 'entry_1/detector_1/patterson' in f:
                    f['entry_1/detector_1/patterson'].attrs['axes'] = ['experiment_identifier:y:x']
                if save_tof and '/entry_1/detector_2' in f:
                    f['entry_1/data_2'] = h5py.SoftLink('/entry_1/detector_2')
                    #f['entry_1/detector_2/data'].attrs['axes'] = ['experiment_identifier:x']
                print "Successfully created soft links and attributes"
            os.system('mv %s %s' %(filename_tmp, filename_done))
            os.system('chmod 770 %s' %(filename_done))
            print "Moved temporary file %s to %s" %(filename_tmp, filename_done)
    if ipc.mpi.is_main_event_reader():
        if counter > 0:
            print "Run %i: Median hit score is %.1f." % (args.run_nr, np.median(hitscore_cache[:min([counter, cache_length])]))
        print "Clean exit"
